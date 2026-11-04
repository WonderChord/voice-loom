"""有界串行语音流水线。每个任务独立配置快照；停止使旧代次失效。"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import copy
import re
import shutil
import threading
import time
import uuid
from urllib.parse import urlsplit

from .models import Config, OutputSelection, Plan
from . import hotkeys, language
from .energy import check_file_energy
from .store import Store
from .subtitles import SubtitleHub
from .usage import UsageLog


def audio_seconds(path) -> float:
    try:
        import soundfile
        return round(float(soundfile.info(str(path)).duration), 3)
    except Exception:
        return 0.0


def now():
    return datetime.now(timezone.utc).isoformat()


class BusyError(ValueError):
    pass


class Cancelled(Exception):
    pass


class ListeningSetupIssue(ValueError):
    def __init__(self, message: str, provider: str, page: str = "models"):
        super().__init__(message)
        self.provider = provider
        self.page = page


def validate_listening_config(config: dict, reference: dict | None) -> None:
    """Only inspect saved fields/files; never contact services or open audio devices."""
    selected = {kind: next(p for p in config["providers"] if p["id"] == config["active"][kind]) for kind in ("asr", "llm", "tts")}
    audit_id = config["active"].get("audit_llm") or ""
    if audit_id and config.get("audit_enabled", True):
        audit_provider = next((p for p in config["providers"] if p["id"] == audit_id and p["kind"] == "llm"), None)
        if audit_provider is not None:
            selected["audit_llm"] = audit_provider
    fallback_id = config["active"].get("tts_fallback") or ""
    if fallback_id and fallback_id != config["active"]["tts"]:
        fallback = next((p for p in config["providers"] if p["id"] == fallback_id and p["kind"] == "tts"), None)
        if fallback is not None:
            selected["tts_fallback"] = fallback
    for category, provider in selected.items():
        if category in {"llm", "audit_llm"} and not language.requires_llm(config):
            continue
        kind = provider["type"]
        label = ("备用 TTS " if category == "tts_fallback" else "") + provider["name"]
        provider_id = provider["id"]
        if kind in {"openai_asr", "openai_llm", "openai_tts"}:
            if not str(provider.get("base_url", "")).strip():
                raise ListeningSetupIssue(f"{label} 缺少服务地址，请在模型连接中填写。", provider_id)
            if not str(provider.get("model", "")).strip():
                raise ListeningSetupIssue(f"{label} 缺少模型名称，请在模型连接中填写。", provider_id)
        base = str(provider.get("base_url", ""))
        if base:
            address = urlsplit(base)
            local = address.hostname in {"127.0.0.1", "localhost", "::1"}
            if address.scheme == "http" and not local:
                raise ListeningSetupIssue(f"{label} 的云服务地址请使用 HTTPS", provider_id)
            if kind == "qwen_local" and not local:
                raise ListeningSetupIssue(f"{label} 请使用 localhost 或 127.0.0.1 本机地址", provider_id)
            if kind in {"openai_asr", "openai_llm", "openai_tts"} and not local and not str(provider.get("api_key", "")).strip():
                raise ListeningSetupIssue(f"{label} 缺少 API 密钥，请在模型连接中保存。", provider_id)
        if kind in {"elevenlabs", "fish_tts"} and not str(provider.get("api_key", "")).strip():
            raise ListeningSetupIssue(f"{label} 缺少 API 密钥，请在模型连接中保存。", provider_id)
        if kind in {"openai_tts", "elevenlabs", "fish_tts"}:
            mappings = (reference or {}).get("provider_voice_ids") or {}
            voice = mappings.get(provider["id"]) or mappings.get(kind) or provider.get("voice")
            if not str(voice or "").strip():
                raise ListeningSetupIssue(f"{label} 缺少声音 ID，请在模型连接中填写，或在参考音色中绑定。", provider_id)
        if kind in {"breeze", "gptsovits", "drama3_local"}:
            mappings = (reference or {}).get("provider_voice_ids") or {}
            preset = str(mappings.get(provider["id"]) or mappings.get(kind) or "").strip()
            remote = bool(base) and urlsplit(base).hostname not in {"127.0.0.1", "localhost", "::1"}
            if not preset and kind == "gptsovits" and remote:
                raise ListeningSetupIssue(f"{label} 是云端服务，请在参考语音里填写服务器上的参考音频路径", provider_id, "voices")
            # 云端预置音色只需名字；其余情况把本机参考音频上传给服务。
            if not preset and not Path((reference or {}).get("path") or "").is_file():
                raise ListeningSetupIssue(f"{label} 需要参考音频，请选择或导入参考音色。云端预置音色可直接绑定。", provider_id, "voices")
            if (not preset or kind == "gptsovits") and not str((reference or {}).get("transcript") or "").strip():
                raise ListeningSetupIssue("参考语音缺少对应台词，请先识别或填写参考文字。", provider_id, "voices")
        if kind == "sensevoice":
            from . import local_runtime
            if not local_runtime.available("sensevoice"):
                raise ListeningSetupIssue("SenseVoice 本机模型文件不完整，请检查本地模型部署或更换 ASR。", provider_id)


@dataclass
class Job:
    id: str
    generation: int
    config: dict
    reference: dict | None = None
    source_text: str = ""
    path: Path | None = None
    plan: dict | None = None
    preview: bool = False
    mode: str = "process"
    reference_id: str = ""
    future: Future | None = None
    started: float = field(default_factory=time.monotonic)
    tts_revision: int = 0
    usage_session: str = ""


class Pipeline:
    def __init__(self, store: Store, providers=None, audio_factory=None, hotkey_factory=None):
        if providers is None:
            from . import providers
        if audio_factory is None:
            from .audio_engine import AudioEngine
            audio_factory = AudioEngine
        self.store = store
        self.providers = providers
        self.subtitles = SubtitleHub(store.root, store.config()["subtitles"])
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.control_lock = threading.RLock()
        self.audio = audio_factory(self._on_segment, self._on_status)
        self.jobs: deque[Job] = deque()
        self.active: Job | None = None
        self.generation = 0
        self.listening = False
        self.playing = False
        self.level_db = -90.0
        self.stage = "idle"
        self.last_error: str | None = None
        self.audio_error_count = 0
        self.last_audio_error = ""
        self.skipped_audio_count = 0
        self.last_skip_reason = ""
        self.dropped_audio_count = 0
        self.tts_remote: dict | None = None
        self.tts_failover: dict | None = None
        self.tts_revision = 0
        self.probe_generation = None
        self.probe_thread: threading.Thread | None = None
        self.usage = UsageLog(store.root / "usage.json")
        self.hotkeys = (hotkey_factory or hotkeys.create)(self.hotkey)
        self.hotkey_binding: dict[str, str] = {}
        self.hotkey_status: dict[str, dict] = {}
        self.hotkey_config_lock = threading.Lock()
        self.logs = deque(maxlen=200)
        self.logs_revision = 1
        self.history: list[dict] = []
        self.started = time.monotonic()
        self.closed = False
        self.worker: threading.Thread | None = None
        for record in store.records():
            if record.get("status") == "skipped":
                self.skipped_audio_count += 1
                self.last_skip_reason = record.get("skip_reason", "")
            if record.get("status") == "done" and record.get("context_member"):
                self._remember(record.get("source_text", ""), record.get("text", ""))
        for warning in store.startup_warnings:
            self.log("warning", warning)
        self.log("info", "工作台就绪，监听尚未开始。保持原语言且关闭角色润色时使用原文直通，直通只做本地指令复核。")

    def start_worker(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.closed = False
            self.subtitles.start()
            self.worker = threading.Thread(target=self._work, name="voice-loom-pipeline", daemon=True)
            self.worker.start()
        self._configure_hotkeys(self.store.config())

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        with self.hotkey_config_lock:
            self.hotkeys.close()
            self.hotkey_binding, self.hotkey_status = {}, {}
        self.stop()
        self.subtitles.close()
        if self.worker:
            self.worker.join(timeout=2)
        self.usage.close()

    def safe_error(self, error) -> str:
        message = str(error) or type(error).__name__
        # Only use decrypted values for in-memory redaction; never include them in logs.
        try:
            secrets = [p.get("api_key", "") for p in self.store.config(True)["providers"]]
            with self.lock:
                if self.active:
                    secrets += [p.get("api_key", "") for p in self.active.config["providers"]]
            for secret in secrets:
                if secret:
                    message = message.replace(secret, "[密钥已隐藏]")
        except Exception:
            pass
        message = re.sub(r"(?i)(bearer\s+|(?:api[_-]?key|authorization|token)\s*[:=]\s*)[^\s,;]+", r"\1[已隐藏]", message)
        return message[:800]

    def log(self, level, message):
        with self.lock:
            self.logs.append({"time": now(), "level": level, "message": self.safe_error(message)})
            self.logs_revision += 1

    def _on_status(self, status):
        with self.lock:
            for key in ("listening", "playing"):
                if key in status:
                    setattr(self, key, bool(status[key]))
            if "level_db" in status:
                try:
                    self.level_db = max(-120, min(0, float(status["level_db"])))
                except (ValueError, TypeError):
                    pass
            if status.get("message"):
                self.log("info", status["message"])
            if status.get("error"):
                self.last_error = self.safe_error(status["error"])
                if status.get("error_source", "output") == "output":
                    self.last_audio_error = self.last_error
                    self.audio_error_count += 1
                self.log("error", self.last_error)

    def state(self, since_records_rev: int | None = None, since_logs_rev: int | None = None):
        with self.lock:
            stage = self.stage
            if stage == "idle" and self.listening:
                stage = "listening"
            records_rev, records = self.store.records_snapshot(since_records_rev)
            logs_rev = self.logs_revision
            result = {
                "listening": self.listening,
                "playing": self.playing,
                "stage": stage,
                "level_db": self.level_db,
                "queue_size": len(self.jobs),
                "records_revision": records_rev,
                "logs_revision": logs_rev,
                "last_error": self.last_error,
                "skipped_audio_count": self.skipped_audio_count,
                "last_skip_reason": self.last_skip_reason,
                "dropped_audio_count": self.dropped_audio_count,
                "tts_remote": dict(self.tts_remote) if self.tts_remote else None,
                "tts_failover": ({k: v for k, v in self.tts_failover.items() if k != "retry_at"}
                                 if self.tts_failover else None),
                "hotkeys": copy.deepcopy(self.hotkey_status),
                "uptime_s": round(time.monotonic() - self.started, 1),
            }
            if records is not None:
                result["records"] = records
            if since_logs_rev is None or since_logs_rev != logs_rev:
                result["logs"] = list(self.logs)
            return result

    def update_config(self, config: Config):
        with self.control_lock, self.lock:
            if self.listening and config.audio.model_dump() != self.store.config()["audio"]:
                raise BusyError("请先停止监听，再修改音频设备或断句设置")
            previous = self.store.config(decrypted=True)
            saved = self.store.save_config(config)
            self.subtitles.configure(saved["subtitles"])
            self._refresh_tts_config(previous)
        if self.worker:
            self._configure_hotkeys(saved)
        return saved

    def _configure_hotkeys(self, config: dict):
        with self.hotkey_config_lock:
            if not self.closed:
                self._register_hotkeys(self.store.config())

    def _register_hotkeys(self, config: dict):
        binding = hotkeys.bindings(config)
        if binding == self.hotkey_binding and all(s["ok"] for s in self.hotkey_status.values()):
            return
        status = self.hotkeys.configure(binding)
        with self.lock:
            self.hotkey_binding, self.hotkey_status = binding, status
        failed = [f"{item['key']}：{item['message']}" for item in status.values() if not item["ok"]]
        if failed:
            self.log("warning", "部分全局快捷键未生效 —— " + "；".join(failed))
        elif binding:
            self.log("info", f"全局快捷键已启用（{len(binding)} 个）")

    def hotkey(self, action: str):
        """全局快捷键入口，在快捷键线程里调用；失败写日志，不抛出。"""
        try:
            with self.control_lock:
                if not self.closed:
                    self._dispatch_hotkey(action)
        except Exception as exc:
            self.log("warning", f"快捷键操作失败：{self.safe_error(exc)}")

    def _dispatch_hotkey(self, action: str):
        if action == "toggle_listen":
            if self.listening:
                self.stop()
            else:
                self.listen()
        elif action == "flush":
            self.flush()
            self.log("info", "快捷键：立即处理当前语音")
        elif action == "stop_playback":
            self.stop_playback()
            self.log("info", "快捷键：已停止播放")
        elif action in {"next_character", "previous_character"}:
            config = self.store.config()
            ids = [card["id"] for card in config.get("characters") or []]
            if not ids:
                raise ValueError("还没有角色卡")
            step = 1 if action == "next_character" else -1
            current = config.get("active_character")
            index = (ids.index(current) + step) % len(ids) if current in ids else (0 if step > 0 else len(ids) - 1)
            self.apply_character(ids[index])
        elif action.startswith("character:"):
            self.apply_character(action.split(":", 1)[1])

    def select_output(self, change: OutputSelection) -> dict:
        """仅改选中的一项，保留快捷键或其他客户端刚应用的配置。"""
        if change.target == "character" and change.id:
            return self.apply_character(change.id)
        with self.control_lock:
            value = self.store.config()
            if change.target == "character":
                value["active_character"] = ""
            else:
                value["active"][change.target] = change.id
            return {"config": self.update_config(Config.model_validate(value)), "warnings": []}

    def apply_character(self, character_id: str) -> dict:
        """切换角色卡：把卡上的音色、TTS、人设、语言和情绪强度写入当前设置；监听中也可切换，下一句生效。"""
        with self.control_lock, self.lock:
            value = self.store.config()
            card = next((c for c in value.get("characters") or [] if c["id"] == character_id), None)
            if card is None:
                raise KeyError("角色卡不存在")
            value.update(active_character=card["id"], persona=card["persona"] or value["persona"],
                         prompt=card["prompt"] or value["prompt"], style_strength=card["style_strength"],
                         rewrite_enabled=card["rewrite_enabled"], target_language=card["target_language"],
                         emotion_intensity=card["emotion_intensity"], active_style_preset="")
            notes = []
            if card["tts"]:
                value["active"]["tts"] = card["tts"]
            if card["reference"]:
                if card["reference"] in {r["id"] for r in self.store.references()}:
                    value["active"]["reference"] = card["reference"]
                else:
                    notes.append("角色卡的参考语音已删除，保留当前音色")
            previous = self.store.config(decrypted=True)
            saved = self.store.save_config(Config.model_validate(value))
            self.subtitles.configure(saved["subtitles"])
            self._refresh_tts_config(previous)
        self.log("info", f"已切换到角色卡「{card['name']}」" + ("；" + "；".join(notes) if notes else ""))
        return {"config": saved, "warnings": notes}

    def _checked_listening_config(self):
        config = self.store.config(decrypted=True)
        reference_id = config["active"]["reference"]
        reference = self.store.reference(reference_id, private=True) if reference_id else None
        validate_listening_config(config, reference)
        return config

    def listening_readiness(self):
        with self.lock:
            try:
                self._checked_listening_config()
            except ListeningSetupIssue as exc:
                return {"ready": False, "message": self.safe_error(exc), "page": exc.page, "provider": exc.provider}
            return {"ready": True}

    def listen(self):
        with self.control_lock:
            with self.lock:
                if self.listening:
                    return
                config = self._checked_listening_config()
                audio_config = copy.deepcopy(config["audio"])
                self.last_error = None
            self.audio.start(audio_config)
            with self.lock:
                if not self.listening:
                    raise ValueError(self.last_error or "输入设备未开始监听，请检查设备连接")
            self.log("info", "持续监听已开始")
            self._start_remote_probe()

    REMOTE_PROBE_INTERVAL_S = 240
    # 已切到备用 TTS 时更频繁地探测主 TTS，恢复后下一句立即切回。
    FAILOVER_PROBE_S = 30
    FAILOVER_RETRY_S = 60

    def _refresh_tts_config(self, previous):
        def selected(config):
            return [next((p for p in config["providers"] if p["id"] == config["active"].get(key)), None)
                    for key in ("tts", "tts_fallback")]
        if selected(previous) != selected(self.store.config(decrypted=True)):
            self.tts_revision += 1
            self.tts_failover = None
            self.tts_remote = None
            self.condition.notify_all()
            if self.listening:
                self._start_remote_probe()

    def _remote_tts(self):
        config = self.store.config(decrypted=True)
        provider = next((p for p in config["providers"] if p["id"] == config["active"]["tts"]), None)
        host = urlsplit(str((provider or {}).get("base_url") or "")).hostname
        if not provider or not host or host in {"127.0.0.1", "localhost", "::1"}:
            return None
        if provider["type"] not in {"breeze", "drama3_local", "gptsovits", "openai_tts"}:
            return None
        return provider

    def _start_remote_probe(self):
        """监听期间定时探测云端自部署 TTS：开播即建立连接，工作台显示可达状态与往返耗时。"""
        with self.lock:
            generation = (self.generation, self.tts_revision)
            if self.probe_thread and self.probe_thread.is_alive() and self.probe_generation == generation:
                return
            if self._remote_tts() is None:
                self.tts_remote = None
                return
            self.probe_generation = generation
            self.probe_thread = threading.Thread(target=self._probe_loop, args=generation,
                                                 name="voice-loom-tts-probe", daemon=True)
            self.probe_thread.start()

    def _probe_loop(self, generation, revision=None):
        revision = self.tts_revision if revision is None else revision
        def stopped():
            return self.closed or not self.listening or self.generation != generation or self.tts_revision != revision
        last = None
        while True:
            with self.condition:
                if stopped():
                    return
            interval = self.FAILOVER_PROBE_S if self.tts_failover else self.REMOTE_PROBE_INTERVAL_S
            waited = None if last is None else time.monotonic() - last
            if waited is not None and waited < interval:
                with self.condition:
                    self.condition.wait_for(stopped, timeout=min(self.FAILOVER_PROBE_S, interval - waited))
                continue
            last = time.monotonic()
            provider = self._remote_tts()
            if provider is None:
                with self.lock:
                    self.tts_remote = None
                return
            started = time.monotonic()
            try:
                result = self.providers.test_provider(provider)
            except Exception as exc:
                check = getattr(self.providers, "is_unavailable", lambda _: False)
                result = {"ok": False, "message": str(exc), "unavailable": check(exc)}
            status = {"provider": provider["name"], "ok": bool(result.get("ok")),
                      "rtt_ms": round((time.monotonic() - started) * 1000), "checked_at": now(),
                      "message": self.safe_error(result.get("message", ""))}
            with self.lock:
                if stopped():
                    return
                previous = self.tts_remote
                self.tts_remote = status
                if status["ok"]:
                    self._recover_tts(provider, revision)
                elif result.get("unavailable"):
                    fallback = self._fallback_tts(self.store.config(decrypted=True), provider["id"])
                    if fallback:
                        self._fail_over(provider, fallback, status["message"], revision, retry=False)
            if not previous or previous["ok"] != status["ok"]:
                text = f"已连通，往返 {status['rtt_ms']} 毫秒" if status["ok"] else "无法连接：" + status["message"]
                self.log("info" if status["ok"] else "warning", f"云端 TTS {status['provider']} {text}")

    def stop(self):
        with self.control_lock:
            with self.condition:
                self.generation += 1
                self.listening = False
                pending = list(self.jobs)
                cancelled = list(pending)
                self.jobs.clear()
                if self.active:
                    cancelled.append(self.active)
                for job in cancelled:
                    if job.mode == "process":
                        try:
                            record = self.store.record(job.id)
                        except KeyError:
                            # A cleared session can still have a cancelled remote request returning.
                            record = None
                        if record and record.get("status") not in {"done", "review", "error", "cancelled", "skipped"}:
                            self.store.update_record(job.id, status="cancelled", error="已停止，任务已取消", elapsed_ms=round((time.monotonic() - job.started) * 1000))
                    if job.future and not job.future.done():
                        job.future.set_exception(Cancelled("已停止，参考转写已取消"))
                self.stage = "idle"
                self.playing = False
                self.subtitles.clear("stopped")
                self.condition.notify_all()
            self.audio.stop()
            self.audio.stop_playback()
            for job in pending:
                if job.reference:
                    Path(job.reference["path"]).unlink(missing_ok=True)
                if job.mode == "reference" and job.path:
                    job.path.unlink(missing_ok=True)
            self.log("info", "监听与播放已停止，等待任务已取消")

    def stop_playback(self):
        self.audio.stop_playback()
        with self.lock:
            self.playing = False

    def flush(self):
        if not self.listening:
            raise ValueError("请先开始监听")
        self.audio.flush()

    def clear(self):
        self.stop()
        with self.lock:
            self.history.clear()
            self.store.clear_records()
            self.last_error = None
            self.skipped_audio_count = 0
            self.last_skip_reason = ""
            self.subtitles.clear("new_session")
            self.usage.reset_session()
        self.log("info", "新会话已开始，上下文与记录已清空，已有音频保留")

    def _snapshot(self, job_id, preview=False):
        config = self.store.config(decrypted=True)
        reference = None
        reference_id = config["active"]["reference"]
        if reference_id and not preview:
            reference = self.store.reference(reference_id, private=True)
            # A queued task keeps a private copy when the library entry is deleted.
            source = Path(reference["path"])
            target = self.store.runs / f"{job_id}_reference.wav"
            shutil.copy2(source, target)
            reference["path"] = str(target)
        return config, reference

    def _check_capacity(self):
        if self.closed:
            raise BusyError("服务正在关闭")
        if len(self.jobs) >= 3:
            raise BusyError("等待队列已满（3 条），请等当前语音完成")

    def submit(self, *, text="", path: Path | None = None, origin="text", preview=False, plan=None, parent_id=None, play_audio=None):
        with self.condition:
            self._check_capacity()
            job_id = uuid.uuid4().hex
            config, reference = self._snapshot(job_id, preview=preview)
            if play_audio is not None:
                config["audio"]["auto_play"] = bool(play_audio)
            target_language = language.selected(config, plan)
            if parent_id:
                parent = self.store.record(parent_id)
                # A reviewed draft keeps the language selected when it was made.
                # Older records predate translation and therefore use original.
                saved_language = parent.get("target_language", (parent.get("plan") or {}).get("target_language", "original"))
                target_language = language.selected({"target_language": saved_language})
                config["_human_confirmed"] = True
            config["target_language"] = target_language
            if plan is not None:
                plan = {**copy.deepcopy(plan), "target_language": target_language}
            job = Job(job_id, self.generation, config, reference, text, path, plan, preview,
                      tts_revision=self.tts_revision, usage_session=self.usage.session_id)
            record = {"id": job_id, "created_at": now(), "status": "queued", "source_text": text, "text": "", "target_language": target_language, "plan": None, "audio_url": None, "error": None, "elapsed_ms": 0, "origin": origin, "preview_only": preview, "context_member": False}
            if parent_id:
                record["parent_id"] = parent_id
            self.store.add_record(record)
            self.jobs.append(job)
            self.condition.notify()
            return job_id

    def replay(self, record_id):
        record = self.store.record(record_id)
        if not record.get("audio_url"):
            raise ValueError("此记录还没有可播放的音频")
        path = self.store.audio_path(record["audio_url"].removeprefix("/api/audio/"))
        with self.condition:
            self._check_capacity()
            job = Job(record_id, self.generation, self.store.config(), path=path, mode="play")
            self.jobs.append(job)
            self.condition.notify()

    def transcribe_reference(self, reference_id):
        with self.condition:
            self._check_capacity()
            reference = self.store.reference(reference_id, private=True)
            selected_asr = self.store.provider(self.store.config()["active"]["asr"])
            if selected_asr["type"] == "qwen_local":
                import soundfile as sf
                if sf.info(reference["path"]).duration > 60:
                    raise ValueError("Qwen 单次参考转写最长 60 秒，请导入较短片段")
            job_id = uuid.uuid4().hex
            copy_path = self.store.runs / f"{job_id}_reference.wav"
            shutil.copy2(reference["path"], copy_path)
            future = Future()
            job = Job(job_id, self.generation, self.store.config(True), path=copy_path, mode="reference", reference_id=reference_id, future=future,
                      usage_session=self.usage.session_id)
            self.jobs.append(job)
            self.condition.notify()
        return future.result(timeout=300)

    def _on_segment(self, audio, rate):
        import numpy as np
        import soundfile as sf
        path = None
        try:
            with self.lock:
                if not self.listening:
                    return
                if len(self.jobs) >= 3:
                    # 队列已满时丢弃并明确告知，避免语音静默丢失。
                    self.dropped_audio_count += 1
                    self.last_error = "等待队列已满（3 条），这一段语音未能处理"
                    self.log("warning", self.last_error)
                    return
                self._check_capacity()
                if len(audio) == 0:
                    return
                if len(audio) / rate > 65:
                    raise ValueError("录音片段超过允许时长")
                path = self.store.runs / f"{uuid.uuid4().hex}_input.wav"
                sf.write(path, np.asarray(audio, dtype=np.float32), rate, subtype="PCM_16")
                self.submit(path=path, origin="mic")
        except Exception as exc:
            if path:
                path.unlink(missing_ok=True)
            self.last_error = self.safe_error(exc)
            self.log("error", self.last_error)

    def _guard(self, job):
        if self.closed or job.generation != self.generation:
            raise Cancelled("任务已取消")

    def _stage(self, job, stage, **changes):
        with self.lock:
            self._guard(job)
            self.stage = stage
            if job.mode == "process":
                self.store.update_record(job.id, status=stage, **changes)

    def _update(self, job, **changes):
        with self.lock:
            self._guard(job)
            self.store.update_record(job.id, **changes)

    def _on_audit(self, job, candidate):
        candidate = {**candidate, "target_language": language.selected(job.config)}
        plan = Plan.model_validate(candidate).model_dump(exclude_none=True)
        plan["audit"]["passed"] = False
        self._stage(job, "audit", plan=plan, text=plan["text"])

    def _selected(self, job, kind):
        provider_id = job.config["active"][kind]
        provider = copy.deepcopy(next(provider for provider in job.config["providers"] if provider["id"] == provider_id))
        provider["_cancel_check"] = lambda: self._guard(job)
        return provider

    def _fallback_tts(self, config, primary_id):
        fallback_id = config["active"].get("tts_fallback") or ""
        if not fallback_id or fallback_id == primary_id:
            return None
        return next((p for p in config["providers"] if p["id"] == fallback_id and p["kind"] == "tts"), None)

    def _fail_over(self, primary, fallback, reason, revision=None, *, retry=True):
        reason = self.safe_error(reason)
        with self.lock:
            if revision is not None and revision != self.tts_revision:
                return
            current = self.tts_failover
            retry_at = time.monotonic() + self.FAILOVER_RETRY_S
            if current and current["primary"] == primary["id"]:
                if retry:
                    current["retry_at"] = retry_at
                return
            self.tts_failover = {"primary": primary["id"], "primary_name": primary["name"],
                                 "fallback": fallback["id"], "fallback_name": fallback["name"],
                                 "since": now(), "reason": reason, "retry_at": retry_at}
        self.log("warning", f"主 TTS {primary['name']} 无法连接（{reason}），"
                            f"之后的句子改用备用 TTS {fallback['name']}；主 TTS 恢复后自动切回")

    def _recover_tts(self, primary, revision=None):
        with self.lock:
            if revision is not None and revision != self.tts_revision:
                return
            current = self.tts_failover
            if not current or current["primary"] != primary["id"]:
                return
            self.tts_failover = None
        self.log("info", f"主 TTS {primary['name']} 已恢复，切回主 TTS")

    def _synthesize(self, job, plan, output):
        """主 TTS 不可达时改用备用 TTS；返回 (合成结果, 实际使用的服务, 是否用了备用)。"""
        primary = self._selected(job, "tts")
        fallback = self._fallback_tts(job.config, primary["id"])
        if fallback:
            fallback = copy.deepcopy(fallback)
            fallback["_cancel_check"] = primary["_cancel_check"]
        with self.lock:
            failover = self.tts_failover
            skip_primary = bool(fallback and failover and failover["primary"] == primary["id"]
                                and failover["fallback"] == fallback["id"] and job.tts_revision == self.tts_revision
                                and time.monotonic() < failover["retry_at"])
        if not skip_primary:
            try:
                result = self.providers.synthesize(primary, plan, job.reference, output, job.config)
            except Exception as exc:
                unavailable = getattr(self.providers, "is_unavailable", None)
                if not fallback or not callable(unavailable) or not unavailable(exc):
                    raise
                with self.lock:
                    self._guard(job)
                    self._fail_over(primary, fallback, str(exc), job.tts_revision)
            else:
                with self.lock:
                    self._guard(job)
                    self._recover_tts(primary, job.tts_revision)
                return result, primary, False
        result = self.providers.synthesize(fallback, plan, job.reference, output, job.config)
        note = f"主 TTS {primary['name']} 不可达，本句由备用 TTS {fallback['name']} 合成"
        if note not in plan["warnings"]:
            plan["warnings"].append(note)
        self.log("warning", note)
        return result, fallback, True

    def _record_usage(self, job, provider, entry):
        self.usage.record(provider, entry, session_id=job.usage_session)

    def _remember(self, source, text):
        if source.strip() and text.strip():
            self.history.extend([{"role": "user", "content": source}, {"role": "assistant", "content": text}])
            self.history = self.history[-50:]

    def _play(self, job, path):
        with self.lock:
            self._guard(job)
            error_count = self.audio_error_count
            self.playing = True
            # Dispatch under the generation lock so a stop cannot precede an old play.
            self.audio.play(path, device=job.config["audio"]["output_device"], blocking=False)
        while self.audio.is_playing:
            with self.lock:
                self._guard(job)
            time.sleep(0.05)
        with self.lock:
            self._guard(job)
            self.playing = False
            if self.audio_error_count != error_count:
                raise ValueError(self.last_audio_error)

    def _run(self, job):
        self._guard(job)
        job.config["_cancel_check"] = lambda: self._guard(job)
        if job.mode == "play":
            self._stage(job, "playing")
            self._play(job, job.path)
            return
        if job.path and not self._passes_energy_gate(job):
            return
        if job.mode == "reference":
            self._stage(job, "asr")
            asr = self._selected(job, "asr")
            self._record_usage(job, asr, {"input_s": audio_seconds(job.path)})
            text = self.providers.transcribe(asr, job.path)
            with self.lock:
                self._guard(job)
                result = self.store.patch_reference(job.reference_id, {"transcript": text})
                if not job.future.done():
                    job.future.set_result(result)
            self.log("info", "参考语音转写完成")
            return
        source = job.source_text
        if job.path:
            self._stage(job, "asr")
            asr = self._selected(job, "asr")
            self._record_usage(job, asr, {"input_s": audio_seconds(job.path)})
            source = self.providers.transcribe(asr, job.path).strip()
            self._update(job, source_text=source)
        if not source.strip():
            raise ValueError("未识别到有效语音，请检查输入音量与参考录音")
        if job.plan:
            plan = Plan.model_validate(job.plan).model_dump(exclude_none=True)
        else:
            self._stage(job, "llm")
            job.config["_on_audit"] = lambda candidate: self._on_audit(job, candidate)
            with self.lock:
                self._guard(job)
                limit = job.config["context_limit"]
                history = copy.deepcopy(self.history[-limit:]) if limit else []
            plan = self.providers.rewrite(self._selected(job, "llm"), source, history, job.config)
            plan = Plan.model_validate(plan).model_dump(exclude_none=True)
        plan["source_text"] = source
        plan["target_language"] = language.selected(job.config)
        review = getattr(self.providers, "review_for_provider", None)
        if callable(review):
            with self.lock:
                self._guard(job)
            plan = review(self._selected(job, "tts"), plan, job.config)
        self._update(job, plan=plan, text=plan["text"])
        for warning in plan["warnings"]:
            self.log("warning", warning)
        if plan.get("needs_review") or not plan.get("audit", {}).get("passed", True):
            self._update(job, status="review", elapsed_ms=round((time.monotonic() - job.started) * 1000))
            self.log("warning", "此稿需要确认：请检查控制指令和语音表演安排后再合成")
            return
        if not plan["segments"] and plan.get("directives"):
            self._update(job, status="done", elapsed_ms=round((time.monotonic() - job.started) * 1000))
            self.log("info", "已记录控制指令，此条没有朗读内容")
            return
        # Captions represent approved text, independently of later TTS success.
        with self.lock:
            self._guard(job)
            published = self.subtitles.publish(job.id, plan)
            if published:
                self.store.update_record(job.id, subtitle_event_id=published["event_id"], subtitle_status="approved")
                if published["file_error"]:
                    self.log("warning", published["file_error"])
        audio_url = None
        if not job.preview:
            self._stage(job, "tts")
            output = self.store.runs / f"{job.id}.wav"
            result, used, fell_back = self._synthesize(job, plan, output)
            with self.lock:
                self._guard(job)
            path = Path(result["path"]).resolve()
            if not path.is_relative_to(self.store.runs) or not path.is_file():
                raise ValueError("合成器未返回有效的本机音频")
            spoken = sum(len(seg.get("text") or "") for seg in plan["segments"] if seg["kind"] == "speech")
            self._record_usage(job, used, {"chars": spoken, "audio_s": audio_seconds(path), "fallbacks": int(fell_back)})
            audio_url = self.store.audio_url(path)
            for warning in result.get("warnings", []):
                if warning not in plan["warnings"]:
                    plan["warnings"].append(str(warning))
                    self.log("warning", str(warning))
            self._update(job, audio_url=audio_url, plan=plan)
            if job.config["audio"]["auto_play"]:
                self._stage(job, "playing")
                self._play(job, path)
        with self.lock:
            self._guard(job)
            # 预览稿只用于试听，不进入对话上下文，避免影响后续改写。
            self.store.update_record(job.id, status="done", source_text=source, text=plan["text"], plan=plan, audio_url=audio_url, context_member=not job.preview, elapsed_ms=round((time.monotonic() - job.started) * 1000))
            if not job.preview:
                self._remember(source, plan["text"])
        self.log("info", "预览稿已生成" if job.preview else "语音任务已完成")

    def _passes_energy_gate(self, job):
        result = check_file_energy(job.path, job.config["audio"])
        with self.lock:
            self._guard(job)
            if result is None:
                return True
            if job.mode == "process":
                self.store.update_record(job.id, energy_check=result.as_dict())
            if result.accepted:
                return True
            self.skipped_audio_count += 1
            self.last_skip_reason = result.reason
            if job.mode == "process":
                self.store.update_record(job.id, status="skipped", skip_reason=result.reason,
                                         error=None, context_member=False,
                                         elapsed_ms=round((time.monotonic() - job.started) * 1000))
            elif job.future and not job.future.done():
                job.future.set_exception(ValueError(result.reason))
            self.log("info", result.reason)
        return False

    def _work(self):
        sink = getattr(self.providers, "usage_sink", None)
        while True:
            with self.condition:
                while not self.closed and not self.jobs:
                    self.condition.wait()
                if self.closed:
                    return
                job = self.jobs.popleft()
                self.active = job
                job.started = time.monotonic()
            # 每个任务保留提交时的会话；已取消的云请求返回后仍计入历史用量。
            token = sink.set(lambda provider, entry: self._record_usage(job, provider, entry)) if sink is not None else None
            try:
                self._run(job)
            except Cancelled:
                pass
            except Exception as exc:
                with self.lock:
                    if job.generation == self.generation:
                        if getattr(exc, "plan", None) and job.mode == "process":
                            review_plan = Plan.model_validate(exc.plan).model_dump(exclude_none=True)
                            review_plan["needs_review"] = True
                            self.store.update_record(job.id, status="review", plan=review_plan, text=review_plan["text"], error=None, elapsed_ms=round((time.monotonic() - job.started) * 1000))
                            self.log("warning", self.safe_error(exc))
                            continue
                        error = self.safe_error(exc)
                        self.last_error = error
                        if job.mode == "process":
                            self.store.update_record(job.id, status="error", error=error, elapsed_ms=round((time.monotonic() - job.started) * 1000))
                        if job.future and not job.future.done():
                            job.future.set_exception(ValueError(error))
                        self.log("error", error)
            finally:
                if sink is not None:
                    sink.reset(token)
                if job.reference:
                    Path(job.reference["path"]).unlink(missing_ok=True)
                if job.mode == "reference" and job.path:
                    job.path.unlink(missing_ok=True)
                elif job.mode == "process" and job.path:
                    # 输入音频只在处理期间使用；转写文字与合成结果已保存，处理后删除避免 runs 无限增长。
                    job.path.unlink(missing_ok=True)
                with self.lock:
                    if self.active is job:
                        self.active = None
                        self.stage = "idle"
                        self.playing = False
