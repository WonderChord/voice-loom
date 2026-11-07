"""声织 HTTP 服务；仅接受 localhost / 127.0.0.1 同源访问。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import io
import ipaddress
import hashlib
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import uuid
import threading
import re

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .models import Config, OutputSelection, PronunciationPreview, ReferencePatch, SynthesizeRequest, TextRequest
from . import pronunciation
from .pipeline import BusyError, Cancelled, Pipeline
from .store import Store
from .subtitles import SUBTITLE_HTML
from .access import COOKIE, LocalAccess

ROOT = Path(__file__).resolve().parent.parent
MAX_UPLOAD = 25 * 1024 * 1024
MAX_BODY = MAX_UPLOAD + 1024 * 1024


class PayloadTooLarge(HTTPException):
    def __init__(self, detail="文件过大，最多 25 MB"):
        # FastAPI preserves HTTP exceptions raised while it parses a streamed body.
        super().__init__(status_code=413, detail=detail)


class LocalOnlyMiddleware:
    def __init__(self, app, access):
        self.app = app
        self.access = access
        self.upload_slots = threading.BoundedSemaphore(2)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
        host = headers.get("host", "")
        try:
            host_url = urlsplit("http://" + host)
            allowed_host = host_url.hostname in ("127.0.0.1", "localhost") and not host_url.username and not host_url.password and not host_url.path
            host_port = host_url.port or 80
        except ValueError:
            allowed_host = False
            host_port = 0
        denied = not allowed_host
        peer = scope.get("client")
        if peer:
            try:
                denied = denied or not ipaddress.ip_address(peer[0]).is_loopback
            except ValueError:
                denied = True
        origin = headers.get("origin")
        if origin:
            try:
                source = urlsplit(origin)
                denied = denied or source.scheme != "http" or source.hostname not in ("127.0.0.1", "localhost") or (source.port or 80) != host_port or bool(source.username or source.password or source.path or source.query or source.fragment)
            except ValueError:
                denied = True
        mutation = scope.get("method") not in ("GET", "HEAD", "OPTIONS")
        if headers.get("sec-fetch-site") == "cross-site":
            denied = True
        if denied:
            await JSONResponse({"detail": "此工作台只接受本机同源访问"}, status_code=403)(scope, receive, send)
            return
        path = scope.get("path", "")
        public = path in {"/", "/index.html", "/api/health", "/api/auth/bootstrap"} or path.startswith("/assets/")
        authenticated = self.access.authorized(headers)
        caption_access = self.access.public_caption(scope["method"], path, scope.get("query_string", b""))
        if not public and not authenticated and not caption_access:
            await JSONResponse({"detail": "请从声织桌面程序或启动器打开，以建立本机授权"}, status_code=401)(scope, receive, send)
            return
        path = scope.get("path", "")
        max_body = min(MAX_BODY, MAX_BODY if path in {"/api/references", "/api/process/audio"} else 4096 if path == "/api/auth/bootstrap" else 4 * 1024 * 1024)
        limit_message = "文件过大，最多 25 MB" if max_body == MAX_BODY else "请求内容过大，请缩短输入"
        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError:
            content_length = max_body + 1
        if content_length < 0 or content_length > max_body:
            await JSONResponse({"detail": limit_message}, status_code=413)(scope, receive, send)
            return
        size = 0
        response_started = False
        upload = mutation and path in {"/api/references", "/api/process/audio"}
        if upload and not self.upload_slots.acquire(blocking=False):
            await JSONResponse({"detail": "已有两份音频正在导入，请稍后重试"}, status_code=429)(scope, receive, send)
            return

        async def limited_receive():
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > max_body:
                    raise PayloadTooLarge(limit_message)
            return message

        async def secure_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                original = message.get("headers", [])
                csp = [] if any(k.lower() == b"content-security-policy" for k, _ in original) else [(b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]
                message["headers"] = [*original, *csp, (b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer"), (b"cache-control", b"no-store"), (b"permissions-policy", b"camera=(), microphone=(), geolocation=()")]
            await send(message)

        try:
            await self.app(scope, limited_receive, secure_send)
        except PayloadTooLarge:
            if not response_started:
                await JSONResponse({"detail": limit_message}, status_code=413)(scope, receive, secure_send)
        finally:
            if upload:
                self.upload_slots.release()


async def save_audio(upload: UploadFile, directory: Path, max_seconds: int, name_suffix: str = "") -> tuple[Path, float]:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in (".wav", ".flac", ".ogg", ".mp3"):
        await upload.close()
        raise HTTPException(415, "请导入 WAV、FLAC、OGG 或 MP3 音频")
    try:
        data = await upload.read(MAX_UPLOAD + 1)
    finally:
        await upload.close()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "音频文件最多 25 MB")
    if not data:
        raise HTTPException(422, "音频文件为空")

    def normalize():
        try:
            stream = io.BytesIO(data)
            info = sf.info(stream)
            if info.frames < 1 or not 8000 <= info.samplerate <= 192000 or not 1 <= info.channels <= 8:
                raise ValueError("音频采样率或声道数量不受支持")
            if info.duration > max_seconds:
                raise ValueError(f"此音频最长 {max_seconds} 秒")
            path = directory / f"{uuid.uuid4().hex}{name_suffix}.wav"
            count = 0
            stream.seek(0)
            try:
                with sf.SoundFile(stream) as source, sf.SoundFile(path, "w", samplerate=info.samplerate, channels=1, subtype="PCM_16") as target:
                    for block in source.blocks(blocksize=16384, dtype="float32", always_2d=True):
                        count += len(block)
                        if count > info.samplerate * max_seconds:
                            raise ValueError(f"此音频最长 {max_seconds} 秒")
                        if not np.isfinite(block).all():
                            raise ValueError("音频包含无效采样")
                        target.write(np.clip(block.mean(axis=1), -1, 1))
                if not count:
                    raise ValueError("音频文件为空")
                return path, round(count / info.samplerate, 3)
            except Exception:
                path.unlink(missing_ok=True)
                raise
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, f"音频无法读取：{exc}") from exc

    return await run_in_threadpool(normalize)


def create_app(data_dir: str | Path | None = None, *, providers=None, audio_factory=None, hotkey_factory=None, devices_fn=None, vault=None, seed_reference=True) -> FastAPI:
    store = Store(data_dir or os.environ.get("VOICE_LOOM_DATA") or ROOT / "data", vault=vault, seed_reference=seed_reference)
    pipeline = Pipeline(store, providers=providers, audio_factory=audio_factory, hotkey_factory=hotkey_factory)
    access = LocalAccess(store.root, store.vault)

    @asynccontextmanager
    async def lifespan(application):
        pipeline.start_worker()
        yield
        await run_in_threadpool(pipeline.close)
        from .sensevoice_client import close as close_sense_worker
        await run_in_threadpool(close_sense_worker)

    app = FastAPI(title="声织 · 本机语音工作台", version="1.5.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(LocalOnlyMiddleware, access=access)
    app.state.store = store
    app.state.pipeline = pipeline
    app.state.access = access
    app.state.shutdown_callback = None

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        errors = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(BusyError)
    async def busy(request, exc):
        return JSONResponse({"detail": pipeline.safe_error(exc)}, status_code=409)

    @app.exception_handler(Cancelled)
    async def cancelled(request, exc):
        return JSONResponse({"detail": pipeline.safe_error(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": pipeline.safe_error(exc).strip("'")}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": pipeline.safe_error(exc)}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        message = pipeline.safe_error(exc)
        pipeline.log("error", message)
        return JSONResponse({"detail": message}, status_code=500)

    @app.get("/api/health")
    def health(request: Request):
        result = {"ok": True, "name": "声织", "version": "1.5.0", "security_version": 1, "listening": pipeline.listening}
        proof = access.health_proof(request.query_params.get("challenge", ""))
        if proof:
            result["proof"] = proof
        return result

    @app.post("/api/auth/bootstrap")
    async def bootstrap(request: Request):
        try:
            value = await request.json()
        except ValueError:
            raise HTTPException(422, "本机授权格式错误") from None
        if not isinstance(value, dict) or not access.same(access.browser_session, value.get("session")):
            raise HTTPException(401, "本机授权无效，请从启动器重新打开")
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE, access.browser_session, max_age=43200, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/api/access/outputs")
    def output_links(request: Request):
        root = str(request.base_url).rstrip("/")
        suffix = "?access=" + access.subtitle_key
        return {"browser": root + "/subtitles" + suffix, "json": root + "/api/subtitles/current" + suffix,
                "events": root + "/api/subtitles/events" + suffix}

    @app.post("/api/desktop/shutdown")
    def desktop_shutdown():
        callback = app.state.shutdown_callback
        if callback is None:
            raise HTTPException(409, "此后台由独立启动器管理")
        pipeline.stop()
        callback()
        return {"ok": True}

    @app.get("/api/config")
    def get_config():
        return store.config()

    @app.get("/api/integrations/mcp")
    def mcp_config(request: Request):
        from .desktop_settings import integration_configs
        return integration_configs(store.root, request.url.port or 17861)

    @app.get("/api/runtimes")
    def runtime_status():
        from .local_runtime import service_status
        return service_status()

    @app.get('/api/runtime/debug')
    def runtime_debug(include_logs: bool = False):
        from .runtime_debug import snapshot
        from .local_runtime import SERVICE_NAMES
        value = snapshot(store.root/'logs', include_logs)
        for item in [*value['models'], *value['events']]:
            item['name'] = 'SenseVoice' if item.get('model') == 'sensevoice' else SERVICE_NAMES.get(item.get('model'), item.get('model'))
            item['message'] = pipeline.safe_error(item.get('message', ''))
            if item.get('log_tail'):
                item['log_tail'] = pipeline.safe_error(item['log_tail'])
        return value

    @app.post('/api/model-environments/check')
    def check_model_environment(body: dict):
        from .model_environments import ModelEnvironment, inspect
        return inspect(ModelEnvironment.model_validate(body))

    @app.post('/api/model-environments')
    def save_model_environment(body: dict):
        from .model_environments import ModelEnvironment, save
        if pipeline.stage != 'idle' or pipeline.listening:
            raise HTTPException(409, '请先停止监听并等待当前任务结束，再导入模型环境')
        return save(ModelEnvironment.model_validate(body))

    @app.post("/api/runtimes/{name}/{action}")
    def runtime_action(name: str, action: str):
        from .local_runtime import control_service
        if pipeline.stage != "idle" or pipeline.listening:
            raise HTTPException(409, "请先停止监听并等待当前任务结束，再管理模型服务")
        return control_service(name, action)

    @app.put("/api/config")
    def put_config(config: Config):
        return pipeline.update_config(config)

    @app.post("/api/output/select")
    def select_output(change: OutputSelection):
        return pipeline.select_output(change)

    @app.post("/api/characters/{character_id}/apply")
    def apply_character(character_id: str):
        return pipeline.apply_character(character_id)

    @app.post("/api/pronunciations/preview")
    def preview_pronunciation(body: PronunciationPreview):
        config = store.config()
        tts = body.provider or config["active"]["tts"]
        return {"provider": tts, "text": pronunciation.apply(body.text, pronunciation.effective(config, tts))}

    @app.post("/api/providers/{provider_id}/test")
    def test_provider(provider_id: str):
        provider = store.provider(provider_id)
        try:
            result = pipeline.providers.test_provider(provider)
            return {"ok": bool(result.get("ok")), "message": pipeline.safe_error(result.get("message", "检查完成"))}
        except Exception as exc:
            return {"ok": False, "message": pipeline.safe_error(exc)}

    @app.get("/api/capabilities")
    def capabilities():
        return {provider["id"]: pipeline.providers.capabilities(provider) for provider in store.config()["providers"]}

    @app.get("/api/devices")
    def devices():
        try:
            if devices_fn:
                return devices_fn()
            from .audio_engine import list_devices
            return list_devices()
        except Exception as exc:
            raise HTTPException(503, pipeline.safe_error(exc)) from exc

    @app.get("/api/references")
    def references():
        return store.references()

    @app.post("/api/references")
    async def add_reference(file: UploadFile = File(...), name: str = Form("新参考语音", min_length=1, max_length=100), transcript: str = Form("", max_length=12000), language: str = Form("zh", min_length=1, max_length=30)):
        path, duration = await save_audio(file, store.references_dir, 60)
        item = {"id": path.stem, "name": name.strip() or "新参考语音", "transcript": transcript, "language": language, "tags": [], "pinned": False, "favorite": False, "duration": duration, "path": str(path), "audio_url": store.audio_url(path), "provider_voice_ids": {}}
        try:
            return store.add_reference(item)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @app.patch("/api/references/{reference_id}")
    def patch_reference(reference_id: str, patch: ReferencePatch):
        return store.patch_reference(reference_id, patch.model_dump(exclude_none=True))

    @app.delete("/api/references/{reference_id}")
    def delete_reference(reference_id: str):
        store.delete_reference(reference_id)
        return {"ok": True}

    @app.post("/api/references/{reference_id}/transcribe")
    def transcribe_reference(reference_id: str):
        return pipeline.transcribe_reference(reference_id)

    @app.get("/api/state")
    def state(since_records_rev: int | None = None, since_logs_rev: int | None = None):
        return pipeline.state(since_records_rev=since_records_rev, since_logs_rev=since_logs_rev)

    @app.get("/api/usage")
    def usage(days: int = Query(default=30, ge=1, le=90)):
        return pipeline.usage.summary(days)

    @app.get("/api/subtitles/current")
    def current_subtitle():
        return pipeline.subtitles.current()

    @app.get("/api/subtitles/events")
    async def subtitle_events(request: Request):
        cursor = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
        return StreamingResponse(pipeline.subtitles.stream(cursor), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @app.post("/api/subtitles/clear")
    def clear_subtitle():
        return pipeline.subtitles.clear("manual")

    @app.get("/subtitles", response_class=HTMLResponse)
    def subtitle_browser_source():
        return HTMLResponse(SUBTITLE_HTML, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})

    @app.get("/api/listen/readiness")
    def listen_readiness():
        return pipeline.listening_readiness()

    @app.post("/api/listen/start")
    def listen_start():
        pipeline.listen()
        return {"ok": True, "listening": pipeline.listening}

    @app.post("/api/listen/stop")
    def listen_stop():
        pipeline.stop()
        return {"ok": True, "listening": False}

    @app.post("/api/listen/flush")
    def listen_flush():
        pipeline.flush()
        return {"ok": True}

    @app.post("/api/process/text")
    def process_text(body: TextRequest):
        return {"id": pipeline.submit(text=body.text, preview=body.preview_only, play_audio=body.play_audio)}

    @app.post("/api/preview")
    def preview_text(body: TextRequest):
        return {"id": pipeline.submit(text=body.text, preview=True)}

    @app.post("/api/process/audio")
    async def process_audio(file: UploadFile = File(...), preview_only: bool = Form(False), play_audio: bool | None = Form(None)):
        path, duration = await save_audio(file, store.runs, 60, "_input")
        try:
            return {"id": pipeline.submit(path=path, origin="file", preview=preview_only, play_audio=play_audio)}
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @app.post("/api/records/{record_id}/synthesize")
    def synthesize_record(record_id: str, body: SynthesizeRequest):
        old = store.record(record_id)
        plan = body.plan.model_dump(exclude_none=True)
        plan.update(needs_review=False, audit={"passed": True, "message": "用户已确认并编辑此稿"})
        return {"id": pipeline.submit(text=old["source_text"] or body.plan.source_text, plan=plan, origin="text", parent_id=record_id, play_audio=body.play_audio)}

    @app.post("/api/records/{record_id}/play")
    def play_record(record_id: str):
        try:
            pipeline.replay(record_id)
        except FileNotFoundError:
            raise HTTPException(404, "音频不存在") from None
        return {"ok": True}

    @app.post("/api/playback/stop")
    def playback_stop():
        pipeline.stop_playback()
        return {"ok": True}

    @app.post("/api/session/clear")
    def clear():
        pipeline.clear()
        return {"ok": True}

    @app.get("/api/audio/{filename:path}")
    def audio(filename: str):
        try:
            return FileResponse(store.audio_path(filename))
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "音频不存在")

    @app.get("/api/export/session")
    def export():
        content = {"application": "声织", "exported_at": datetime.now(timezone.utc).isoformat(), "records": store.records()}
        return Response(json.dumps(content, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="voice-loom-session.json"'})

    @app.get("/{path:path}")
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "API 不存在")
        report = path.startswith(("comparison/", "cross-language/", "comparison-history/"))
        external_reports = os.environ.get("VOICE_LOOM_REPORTS")
        dist = (Path(external_reports) if report and external_reports else ROOT / "frontend" / "dist").resolve()
        if any(part.startswith(".") for part in Path(path).parts):
            raise HTTPException(404, "文件不存在")
        requested = (dist / path).resolve()
        if not requested.is_relative_to(dist):
            raise HTTPException(404, "文件不存在")
        if requested.is_file():
            headers = {}
            if requested.suffix.lower() == ".html" and path.startswith(("comparison/", "cross-language/", "comparison-history/")):
                scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", requested.read_text(encoding="utf-8"), flags=re.S | re.I)
                hashes = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode("ascii") + "'" for s in scripts if s.strip())
                headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' " + hashes + "; style-src 'self' 'unsafe-inline'; media-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            return FileResponse(requested, headers=headers)
        if report:
            raise HTTPException(404, "此安装尚未导入本机对比报告")
        index = dist / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"message": "界面尚未构建，请在 frontend 运行 npm run build", "api": "/api/health"}, status_code=503)

    return app


app = create_app(seed_reference=False)
