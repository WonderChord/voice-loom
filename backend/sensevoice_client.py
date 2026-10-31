"""Reuse local model weights and Python without shipping Torch in the UI bundle."""
from __future__ import annotations
import atexit
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

import numpy as np
from . import local_runtime
from .runtime_debug import note

_lock = threading.Lock()
_child = None
_responses = None


def close():
    global _child
    process, _child = _child, None
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


atexit.register(close)


def _receive(timeout, cancel_check):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(cancel_check):
            cancel_check()
        try:
            response = _responses.get(timeout=.2)
        except queue.Empty:
            continue
        if response is None:
            raise RuntimeError("SenseVoice模型进程已退出，请重新提交")
        if response.get("error"):
            raise RuntimeError(response["error"])
        return response
    raise RuntimeError("SenseVoice识别超时，请缩短录音或检查模型环境")


def transcribe(samples, options, cancel_check=None):
    global _child, _responses
    with _lock:
        try:
            if _child is None or _child.poll() is not None:
                started = time.time()
                note(local_runtime.LOG_DIR, 'sensevoice', 'launching', '正在加载 SenseVoice CPU 模型', started=started)
                python = local_runtime.sense_python()
                if not python.is_file() or not local_runtime.available("sensevoice"):
                    raise RuntimeError("SenseVoice模型环境未配置，请设置模型Python路径或选择Qwen识别")
                worker = Path(__file__).with_name("sensevoice_worker.py")
                local_runtime.LOG_DIR.mkdir(parents=True, exist_ok=True)
                environment = os.environ.copy()
                environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1")
                with (local_runtime.LOG_DIR / "sensevoice-worker.log").open("ab") as error_log:
                    _child = subprocess.Popen([str(python), "-u", str(worker), "--model", str(local_runtime.SENSE_MODEL),
                                               "--packages", str(local_runtime.SENSE_PACKAGES)], stdin=subprocess.PIPE,
                                              stdout=subprocess.PIPE, stderr=error_log, env=environment,
                                              cwd=str(local_runtime.SPEECH_ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                _responses = queue.Queue(maxsize=4)
                output, responses = _child.stdout, _responses

                def read():
                    try:
                        for line in iter(output.readline, b""):
                            if len(line) > 65536:
                                break
                            responses.put(json.loads(line))
                    finally:
                        responses.put(None)

                threading.Thread(target=read, name="sensevoice-private-pipe", daemon=True).start()
                if not _receive(120, cancel_check).get("ready"):
                    raise RuntimeError("SenseVoice模型未能就绪")
                note(local_runtime.LOG_DIR, 'sensevoice', 'ready', 'SenseVoice CPU 模型已就绪', started=started, pid=_child.pid)
            payload = {"float32": base64.b64encode(np.asarray(samples, dtype="<f4").tobytes()).decode("ascii"),
                       "language": options.get("language", "auto")}
            _child.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
            _child.stdin.flush()
            result = _receive(120, cancel_check)
            if not isinstance(result.get("text"), str) or not result["text"].strip():
                raise RuntimeError("SenseVoice未识别出文字")
            return result["text"].strip()
        except BaseException as error:
            note(local_runtime.LOG_DIR, 'sensevoice', 'cancelled' if type(error).__name__ == 'Cancelled' else 'error',
                 '识别已取消' if type(error).__name__ == 'Cancelled' else str(error))
            close()
            raise
