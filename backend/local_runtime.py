"""Known local model runtimes. Importing this module never starts a process."""
from __future__ import annotations

from contextlib import nullcontext, contextmanager
import json
import logging
import math
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import psutil
import requests
from filelock import FileLock, Timeout

from .store import atomic_json
from .runtime_debug import note

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = Path(os.environ.get("VOICE_LOOM_DATA") or PROJECT_ROOT / "data").expanduser().resolve() / "logs"
SPEECH_ROOT = Path("G:/AI/speech-chain")
SENSE_MODEL = SPEECH_ROOT / "models" / "SenseVoiceSmall"
SENSE_PACKAGES = SPEECH_ROOT / "sense_packages"
GPT_V5_ROOT = Path("G:/AI/gptsovits-v5")
GPT_V5_PRETRAINED = GPT_V5_ROOT / "GPT-SoVITS/GPT_SoVITS/pretrained_models"
GPT_V5_WEIGHTS = {name: GPT_V5_PRETRAINED / "gsv-v5-pretrained" / filename
                  for name, filename in {"v5turbo": "s2Gv5turbo.pth", "v5dev": "s2Gv5dev.pth"}.items()}

# Keep deployment paths here; the UI and provider adapters do not know them.
RUNTIMES = {
    "qwen_local": {
        "url": "http://127.0.0.1:18081", "port": 18081,
        "exe": Path("G:/AI/llm/llama.cpp-src/build/bin/llama-server.exe"),
        "cwd": Path("G:/AI/llm/llama.cpp-src/build/bin"),
        "files": [Path("G:/AI/llm/Qwen3-ASR-1.7B/Qwen3-ASR-1.7B-Q8_0.gguf"),
                  Path("G:/AI/llm/Qwen3-ASR-1.7B/mmproj-Qwen3-ASR-1.7B-Q8_0.gguf")],
        "args": ["-m", "G:/AI/llm/Qwen3-ASR-1.7B/Qwen3-ASR-1.7B-Q8_0.gguf",
                 "--mmproj", "G:/AI/llm/Qwen3-ASR-1.7B/mmproj-Qwen3-ASR-1.7B-Q8_0.gguf",
                 "-c", "4096", "-np", "1", "-ngl", "99", "--reasoning", "off",
                 "--host", "127.0.0.1", "--port", "18081", "--no-webui",
                 "--no-cache-prompt", "--no-cache-idle-slots", "--no-context-shift"],
    },
    "breeze": {
        "url": "http://127.0.0.1:7871", "port": 7871,
        "exe": Path("G:/AI/breezetts/Breeze-TTS-2.cpp/build/breeze-server.exe"),
        "cwd": Path("G:/AI/breezetts/gguf"),
        "files": [Path("G:/AI/breezetts/gguf/breeze-tts-2-q8_0.gguf")],
        "args": ["G:/AI/breezetts/gguf/breeze-tts-2-q8_0.gguf", "--host", "127.0.0.1",
                 "--port", "7871", "--voices-dir", "G:/AI/breezetts/gguf/voices", "--ws-port", "-1"],
    },
    "drama3_local": {
        "url": "http://127.0.0.1:17863", "port": 17863,
        "exe": Path("G:/AI/drama3/audio.cpp/build/windows-cuda-release/bin/audiocpp_server.exe"),
        "cwd": Path("G:/AI/drama3/audio.cpp"),
        "files": [Path("G:/AI/drama3/server.json"),
                  Path("G:/AI/drama3/models/drama-3-backbone-8bit-experts-Q4_K_S.gguf")],
        "path_prepend": "C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.0/bin",
        "args": ["--config", "G:/AI/drama3/server.json", "--no-ui", "--log",
                 "--log-file", "G:/AI/drama3/server-runtime.log"],
    },
    "drama3_full": {
        "kind": "drama3_local", "url": "http://127.0.0.1:17865", "port": 17865,
        "exe": Path("G:/AI/drama3/original-runtime/venv/Scripts/python.exe"),
        "cwd": Path("G:/AI/drama3/original-runtime"),
        "files": [Path("G:/AI/drama3/original-runtime/serve.py"),
                  Path("G:/AI/drama3/original-runtime/models/drama3-fp8-bf16.safetensors"),
                  Path("G:/AI/drama3/original-runtime/models/drama3-text-head.safetensors"),
                  Path("G:/AI/drama3/original-runtime/models/drama3-codec.safetensors")],
        "directories": [Path("G:/AI/drama3/original-runtime/upstream/portable/drama3_t8"),
                        Path("G:/AI/drama3/original-runtime/upstream/portable/vendor/ComfyUI")],
        "args": ["G:/AI/drama3/original-runtime/serve.py", "--port", "17865"],
    },
    "gptsovits_v5": {
        "kind": "gptsovits", "url": "http://127.0.0.1:17864", "port": 17864,
        "exe": GPT_V5_ROOT / "venv/Scripts/python.exe",
        "cwd": GPT_V5_ROOT / "GPT-SoVITS",
        "files": [GPT_V5_ROOT / "serve.py", GPT_V5_ROOT / "configs/v5turbo.yaml",
                  GPT_V5_ROOT / "configs/v5dev.yaml", GPT_V5_ROOT / "GPT-SoVITS/api_v2.py",
                  GPT_V5_PRETRAINED / "s1v3.ckpt", GPT_V5_PRETRAINED / "gsv-v5-pretrained/vocoder.pth",
                  *GPT_V5_WEIGHTS.values()],
        "directories": [GPT_V5_PRETRAINED / "chinese-hubert-base",
                        GPT_V5_PRETRAINED / "chinese-roberta-wwm-ext-large",
                        GPT_V5_ROOT / "GPT-SoVITS/GPT_SoVITS/text/G2PWModel"],
        "args": [str(GPT_V5_ROOT / "serve.py"), "--variant", "v5turbo", "--port", "17864"],
    },
}
_locks = {name: threading.Lock() for name in RUNTIMES}
_processes: dict[str, subprocess.Popen] = {}
_tts_lock = threading.RLock()
_TTS_NAMES = frozenset({"breeze", "drama3_local", "drama3_full", "gptsovits_v5"})
_logger = logging.getLogger(__name__)


class LocalRuntimeError(RuntimeError):
    pass


def is_loopback(url: str) -> bool:
    try:
        return urlsplit(url).hostname in {"127.0.0.1", "localhost", "::1"}
    except ValueError:
        return False


def runtime_name(kind: str, base_url: str) -> str | None:
    """Resolve a managed deployment while preserving custom provider endpoints."""
    load_environments()
    for name, spec in sorted(RUNTIMES.items(), key=lambda item: not item[1].get('imported', False)):
        endpoint = base_url.rstrip('/').removesuffix('/v1') if spec.get('imported') else base_url.rstrip('/')
        if spec.get("kind", name) == kind and endpoint == spec["url"]:
            return name
    if kind in RUNTIMES:
        return kind
    return None


_health_session_local = threading.local()


def healthy(base_url: str, name: str | None = None) -> bool:
    try:
        session = getattr(_health_session_local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            _health_session_local.session = session
        route = RUNTIMES.get(name, {}).get("health_path", "/health")
        response = session.get(base_url.rstrip("/") + route, timeout=(1, 2), allow_redirects=False)
        return response.status_code == 200
    except requests.RequestException:
        return False


def sense_python() -> Path:
    """SenseVoice 使用的独立 Torch/FunASR 环境，可用 VOICE_LOOM_MODEL_PYTHON 覆盖。"""
    override = os.environ.get("VOICE_LOOM_MODEL_PYTHON", "").strip().strip('"')
    return Path(override) if override else Path("G:/AI/seedvc/venv/Scripts/python.exe")


def available(name: str) -> bool:
    if name == "sensevoice":
        return (SENSE_MODEL / "am.mvn").is_file() and SENSE_PACKAGES.is_dir() and sense_python().is_file()
    spec = RUNTIMES.get(name)
    if spec is None:
        return False
    return (spec["exe"].is_file() and all(path.is_file() for path in spec["files"])
            and all(path.is_dir() for path in spec.get("directories", [])))


SERVICE_NAMES = {"qwen_local": "Qwen3-ASR", "breeze": "Breeze TTS", "drama3_local": "Drama3 · Q4 专家版",
                 "drama3_full": "Drama3 · 原版权重", "gptsovits_v5": "GPT-SoVITS V5"}
_environment_ids = set()


def load_environments():
    from .model_environments import load
    profiles = load()
    active = {profile.id for profile in profiles}
    for name in _environment_ids-active:
        RUNTIMES.pop(name, None)
        SERVICE_NAMES.pop(name, None)
    for profile in profiles:
        RUNTIMES[profile.id] = {'kind': profile.adapter, 'url': profile.base_url, 'port': urlsplit(profile.base_url).port,
            'exe': Path(profile.executable), 'cwd': Path(profile.working_directory),
            'files': [*([Path(profile.script)] if profile.script else []), *[Path(value) for value in profile.model_files]],
            'args': [*([profile.script] if profile.script else []), *profile.arguments], 'health_path': profile.health_path,
            'startup_timeout': profile.startup_seconds, 'imported': True,
            'role': 'tts' if profile.adapter in {'openai_tts', 'breeze', 'drama3_local', 'gptsovits'} else 'other'}
        SERVICE_NAMES[profile.id] = profile.name
        _locks.setdefault(profile.id, threading.Lock())
    _environment_ids.clear()
    _environment_ids.update(active)


def _tts_names():
    return _TTS_NAMES | {name for name, spec in RUNTIMES.items() if spec.get('role') == 'tts'}


def service_status():
    load_environments()
    from concurrent.futures import ThreadPoolExecutor
    def status(name):
        spec = RUNTIMES[name]
        return {"id": name, "name": SERVICE_NAMES[name], "url": spec["url"], "available": available(name),
                "running": healthy(spec["url"], name), "managed": _read_record(name) is not None,
                "imported": bool(spec.get('imported')), "adapter": spec.get('kind', name)}
    with ThreadPoolExecutor(max_workers=5) as pool:
        imported_urls = {spec['url'] for spec in RUNTIMES.values() if spec.get('imported')}
        visible = [name for name in SERVICE_NAMES if RUNTIMES[name].get('imported') or RUNTIMES[name]['url'] not in imported_urls]
        services = list(pool.map(status, visible))
    return {"services": services, "sensevoice": {"available": available("sensevoice"), "mode": "独立 CPU 进程，识别时加载"}}


def control_service(name, action):
    load_environments()
    if name not in SERVICE_NAMES or action not in {"start", "stop"}:
        raise ValueError("请选择已有模型及 start/stop 操作")
    if action == "start":
        ensure(name, timeout=180)
    else:
        record = _read_record(name)
        if record is None:
            if healthy(RUNTIMES[name]["url"], name):
                raise ValueError("该服务由其他启动器管理，请使用原启动器停止")
        else:
            with _tts_lock, service_lock('tts' if name in _tts_names() else name), _locks[name]:
                _stop_registered(name, record)
    return {"id": name, "running": healthy(RUNTIMES[name]["url"], name), "available": available(name)}


def _record_path(name: str) -> Path:
    return LOG_DIR / f"{name}-process.json"


def _identity(process) -> dict:
    """Capture the actual executable, including a Windows venv's child Python."""
    identity = {"pid": process.pid, "create_time": process.create_time(),
                "exe": process.exe(), "cmdline": process.cmdline(), "parent_pid": process.ppid()}
    if not identity["exe"] or not identity["cmdline"] or not process.is_running():
        raise psutil.NoSuchProcess(process.pid)
    return identity


def _valid_identity(value) -> bool:
    return (isinstance(value, dict) and type(value.get("pid")) is int
            and value["pid"] > 0 and value["pid"] != os.getpid()
            and type(value.get("create_time")) in (int, float)
            and math.isfinite(value["create_time"]) and value["create_time"] > 0
            and isinstance(value.get("exe"), str) and bool(value["exe"])
            and isinstance(value.get("cmdline"), list) and bool(value["cmdline"])
            and all(isinstance(arg, str) for arg in value["cmdline"]))


def _read_record(name: str) -> dict | None:
    try:
        record = json.loads(_record_path(name).read_text(encoding="utf-8"))
        version = record.get('version') if isinstance(record, dict) else None
        same_data = version == 2 and record.get('data_directory') == str(LOG_DIR.parent.resolve())
        legacy = version == 1 and isinstance(record.get('project'), str) and (record.get('project') == str(PROJECT_ROOT.resolve()) or
                                  str((Path(record.get('project', ''))/'data').resolve()) == str(LOG_DIR.parent.resolve()))
        if (record.get("owner") == "voice-loom" and (same_data or legacy)
                and record.get("runtime") == name and record.get("url") == RUNTIMES[name]["url"]
                and isinstance(record.get("processes"), list)
                and all(_valid_identity(item) for item in record["processes"])):
            return record
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _save_record(name: str, record: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    atomic_json(_record_path(name), record)


def _matching_process(identity: dict):
    """Fresh objects avoid cached command lines; psutil also guards PID reuse on terminate."""
    try:
        process = psutil.Process(identity["pid"])
        actual = _identity(process)
        if all(actual[key] == identity[key] for key in ("pid", "create_time", "exe", "cmdline")):
            return process, "matched"
        return None, "changed"
    except psutil.NoSuchProcess:
        return None, "missing"
    except psutil.Error:
        return None, "unavailable"


def _refresh_record(name: str, record: dict, persist: bool = True) -> dict:
    """Discover descendants only from a still-matching, already registered owner."""
    known = {(item["pid"], item["create_time"]) for item in record["processes"]}
    added = []
    for identity in record["processes"]:
        process, _ = _matching_process(identity)
        if process is None:
            continue
        try:
            for child in process.children(recursive=True):
                try:
                    value = _identity(child)
                    key = (value["pid"], value["create_time"])
                    if value["create_time"] >= identity["create_time"] and key not in known:
                        known.add(key)
                        added.append(value)
                except psutil.NoSuchProcess:
                    continue
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            raise LocalRuntimeError("无法核验本地 TTS 的子进程，请查看 data/logs 后重试。") from exc
    if added:
        # Keep discovered identities available to rollback even if the disk write fails.
        record["processes"] = [*record["processes"], *added]
        if persist:
            _save_record(name, record)
    return record


def _register_process(name: str, child: subprocess.Popen) -> dict:
    spec = RUNTIMES[name]
    try:
        identity = _identity(psutil.Process(child.pid))
        command = [str(spec["exe"]), *spec["args"]]
        # A just-exited child PID must not accidentally register another process.
        same_path = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        if (identity["parent_pid"] != os.getpid() or not same_path(identity["exe"], command[0])
                or not same_path(identity["cmdline"][0], command[0]) or identity["cmdline"][1:] != command[1:]):
            raise LocalRuntimeError("本地 TTS 启动进程身份不符，已保留该进程，请查看模型日志。")
        record = {"version": 2, "owner": "voice-loom", "project": str(PROJECT_ROOT.resolve()), "data_directory": str(LOG_DIR.parent.resolve()),
                  "runtime": name, "url": spec["url"], "launch_cmdline": command,
                  "processes": [identity]}
        try:
            _save_record(name, record)
            return _refresh_record(name, record)
        except OSError as exc:
            # A full/unwritable disk must not leave a new, unregistered GPU owner.
            _stop_registered(name, record, persist=False)
            raise LocalRuntimeError("无法保存本地 TTS 进程登记，已停止本次启动，请检查 data/logs。") from exc
    except psutil.Error as exc:
        raise LocalRuntimeError("本地 TTS 已退出或无法登记进程身份，请查看模型日志。") from exc


def _stop_registered(name: str, record: dict, cancel_check=None, persist: bool = True) -> None:
    record = _refresh_record(name, record, persist=persist)
    frozen = []
    try:
        index = 0
        while index < len(record["processes"]):
            if callable(cancel_check):
                cancel_check()
            identity = record["processes"][index]
            index += 1
            process, _ = _matching_process(identity)
            if process is None:
                continue
            try:
                process.suspend()
                frozen.append(identity)
            except psutil.NoSuchProcess:
                continue
            except psutil.Error as exc:
                raise LocalRuntimeError("上一套本地 TTS 的进程树无法暂停，暂缓加载新模型。") from exc
            # Freeze each known parent before discovering its last descendants.
            # Appending to the list also freezes those children before termination.
            record = _refresh_record(name, record, persist=persist)
        _terminate_registered(name, record, cancel_check, persist)
    finally:
        # Cancellation, permission errors and timeouts leave survivors usable.
        for identity in reversed(frozen):
            process, _ = _matching_process(identity)
            if process is not None:
                try:
                    process.resume()
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error:
                    _logger.exception("恢复本地服务进程失败：%s PID=%s", name, identity["pid"])


def _terminate_registered(name: str, record: dict, cancel_check=None, persist: bool = True) -> None:
    by_pid = {item["pid"]: item for item in record["processes"]}

    def depth(identity):
        seen = {identity["pid"]}
        while identity.get("parent_pid") in by_pid and identity["parent_pid"] not in seen:
            identity = by_pid[identity["parent_pid"]]
            seen.add(identity["pid"])
        return len(seen)

    remaining = list(record["processes"])
    for identity in sorted(record["processes"], key=depth, reverse=True):
        if callable(cancel_check):
            cancel_check()
        process, status = _matching_process(identity)
        if status == "missing":
            remaining.remove(identity)
        elif process is None:
            _logger.warning("保留身份变化或无法核验的本地服务：%s PID=%s", name, identity["pid"])
        else:
            try:
                process.terminate()
                process.wait(timeout=15)
                remaining.remove(identity)
            except psutil.NoSuchProcess:
                remaining.remove(identity)
            except (psutil.AccessDenied, psutil.TimeoutExpired) as exc:
                if persist:
                    _save_record(name, {**record, "processes": remaining})
                raise LocalRuntimeError("上一套本地 TTS 尚未退出，暂缓加载新模型，请查看 data/logs 后重试。") from exc
    if remaining and persist:
        _save_record(name, {**record, "processes": remaining})
    elif not remaining:
        if persist:
            _record_path(name).unlink(missing_ok=True)
        _processes.pop(name, None)
        note(LOG_DIR, name, 'stopped', '模型进程已停止')


def _release_other_tts(name: str, cancel_check=None) -> None:
    for other in sorted(_tts_names() - {name}):
        record = _read_record(other)
        if record is not None:
            _stop_registered(other, record, cancel_check)


def ensure(name: str, base_url: str | None = None, timeout: float = 120, cancel_check=None) -> None:
    """Reuse existing listeners, launch only the known loopback endpoint on demand."""
    spec = RUNTIMES[name]
    if callable(cancel_check):
        cancel_check()
    target = (base_url or spec["url"]).rstrip("/")
    if not is_loopback(target):
        raise LocalRuntimeError("本地模型地址请使用 localhost 或 127.0.0.1。")
    managed_tts = name in _tts_names() and target == spec["url"]
    # One lock covers release, launch and readiness across all local TTS choices.
    with _tts_lock if managed_tts else nullcontext(), service_lock('tts' if managed_tts else name, cancel_check):
        try:
            if managed_tts:
                _release_other_tts(name, cancel_check)
            _ensure_runtime(name, target, timeout, cancel_check, managed_tts)
        except Exception as error:
            cancelled = type(error).__name__ == 'Cancelled'
            note(LOG_DIR, name, 'cancelled' if cancelled else 'error', '启动等待已取消' if cancelled else str(error))
            raise


@contextmanager
def service_lock(name, cancel_check=None):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(LOG_DIR/('model-'+name+'.lock')))
    deadline = time.monotonic()+240
    while True:
        if callable(cancel_check):
            cancel_check()
        try:
            lock.acquire(timeout=.2)
            break
        except Timeout:
            if time.monotonic() >= deadline:
                raise LocalRuntimeError('其他模型启动操作尚未完成，请稍后重试') from None
    try:
        yield
    finally:
        lock.release()


def _ensure_runtime(name: str, target: str, timeout: float, cancel_check, managed_tts: bool) -> None:
    spec = RUNTIMES[name]
    record = _read_record(name)
    if record is not None:
        record = _refresh_record(name, record)
    if healthy(target, name):
        return
    startup_started = time.time()
    if target != spec["url"]:
        raise LocalRuntimeError("自定义本地服务尚未就绪，请先启动该地址的模型服务。")
    if not available(name):
        raise LocalRuntimeError("本地模型文件或执行程序不存在，请检查 local_runtime.py 中的部署路径。")
    deadline = time.monotonic() + max(timeout, spec.get("startup_timeout", timeout))
    with _locks[name]:
        if callable(cancel_check):
            cancel_check()
        if healthy(target, name):
            return
        with socket.socket() as sock:
            sock.settimeout(1)
            listening = sock.connect_ex(("127.0.0.1", spec["port"])) == 0
        existing = _processes.get(name)
        registered_running = record is not None and any(
            _matching_process(item)[0] is not None for item in record["processes"])
        if not listening and not registered_running and (existing is None or existing.poll() is not None):
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            note(LOG_DIR, name, 'launching', '正在启动模型进程', started=startup_started)
            try:
                with (LOG_DIR / f"{name}.log").open("ab") as stdout, (LOG_DIR / f"{name}-error.log").open("ab") as stderr:
                    environment = os.environ.copy()
                    if spec.get("path_prepend"):
                        environment["PATH"] = spec["path_prepend"] + os.pathsep + environment.get("PATH", "")
                    _processes[name] = subprocess.Popen(
                        [str(spec["exe"]), *spec["args"]], cwd=str(spec["cwd"]),
                        stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                        env=environment,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                record = _register_process(name, _processes[name])
            except OSError:
                raise LocalRuntimeError("本地服务启动失败，请查看 data/logs 中的模型日志。") from None
        note(LOG_DIR, name, 'waiting', '进程已启动，正在等待模型加载和健康检查', started=startup_started,
             pid=_processes[name].pid if name in _processes else None)
        while time.monotonic() < deadline:
            if callable(cancel_check):
                cancel_check()
            if record is not None:
                record = _refresh_record(name, record)
            if healthy(target, name):
                note(LOG_DIR, name, 'ready', '模型服务已就绪', started=startup_started)
                return
            child = _processes.get(name)
            if child is not None and child.poll() is not None:
                raise LocalRuntimeError("本地模型进程已退出，请查看 data/logs 中的模型日志。")
            time.sleep(0.3)
        if listening and not registered_running and (existing is None or existing.poll() is not None):
            raise LocalRuntimeError(f"端口 {spec['port']} 被其他程序占用，健康检查不通过；请关闭占用该端口的程序或修改部署端口。")
    raise LocalRuntimeError("本地模型仍未就绪，请查看 data/logs 中的模型日志。")
