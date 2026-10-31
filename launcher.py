"""Launch the local production UI without opening audio devices.

Project paths are relative to this file. VOICE_LOOM_PYTHON overrides the shared
Seed-VC runtime; VOICE_LOOM_DATA can relocate writable application data.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import secrets
import hmac
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("VOICE_LOOM_DATA") or ROOT / "data").expanduser().resolve()
LOGS = DATA / "logs"
HOST, PORT = "127.0.0.1", 17861
URL = f"http://{HOST}:{PORT}"
KNOWN_PYTHON = Path("G:/AI/seedvc/venv/Scripts/python.exe")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class LaunchError(RuntimeError):
    pass


def log(message):
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "launcher.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        return None


def health():
    # Keep loopback requests away from system proxy settings.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        from backend.access import LocalAccess
        access = LocalAccess(DATA)
        challenge = secrets.token_hex(32)
        with opener.open(URL + "/api/health?challenge=" + challenge, timeout=1.5) as response:
            payload = json.loads(response.read(65536))
        if (isinstance(payload, dict) and payload.get("ok") is True and payload.get("name") == "声织"
                and payload.get("security_version") == 1
                and LocalAccess.same(access.health_proof(challenge), payload.get("proof"))):
            return payload
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return None


def port_in_use():
    with socket.socket() as connection:
        connection.settimeout(.4)
        return connection.connect_ex((HOST, PORT)) == 0


@contextmanager
def launch_lock():
    """A Windows mutex prevents simultaneous double-clicks from spawning twice."""
    if os.name != "nt":
        # Primarily a Windows application; flock keeps CLI launches orderly on
        # other systems without persisting a stale exclusive lock after a crash.
        import fcntl
        DATA.mkdir(parents=True, exist_ok=True)
        with (DATA / "launcher.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Local\\VoiceLoom-17861-Launcher")
    if not handle:
        raise LaunchError("无法创建启动锁，请稍后重试")
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, 60000)
        acquired = result in (0, 0x80)
        if not acquired:
            raise LaunchError("另一个声织启动任务仍在进行，请稍后重试")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def python_runtime():
    override = os.environ.get("VOICE_LOOM_PYTHON", "").strip().strip('"')
    if override:
        resolved = shutil.which(override) or override
        candidate = Path(resolved).expanduser()
        if not candidate.is_file():
            raise LaunchError("VOICE_LOOM_PYTHON 指定的 Python 不存在，请检查该路径")
        return candidate.resolve()
    candidates = [ROOT / ".venv" / "Scripts" / "python.exe", KNOWN_PYTHON, Path(sys.executable)]
    for candidate in candidates:
        if candidate.is_file():
            if candidate.name.lower() == "pythonw.exe" and candidate.with_name("python.exe").is_file():
                candidate = candidate.with_name("python.exe")
            return candidate.resolve()
    raise LaunchError("未找到 Python，请设置 VOICE_LOOM_PYTHON")


def verify_runtime(python):
    probe = (
        "import sys; assert sys.version_info >= (3,10), 'Python 3.10 or newer required'; "
        "import fastapi, uvicorn, pydantic, multipart, numpy, sounddevice, "
        "soundfile, webrtcvad, requests, psutil"
    )
    try:
        result = subprocess.run([str(python), "-c", probe], cwd=ROOT,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                timeout=30, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LaunchError("Python 环境未能完成检查，请查看 data/logs/launcher.log") from exc
    if result.returncode:
        log("环境检查失败：\n" + result.stderr[-6000:])
        raise LaunchError("Python 环境缺少依赖，请按 README 的环境维护步骤安装 requirements.txt")


def ensure_server(timeout=45):
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise LaunchError("界面尚未构建。请在 frontend 目录运行 npm ci 和 npm run build")
    with launch_lock():
        if health():
            log("复用已运行的本机服务")
            return False
        if port_in_use():
            # A separately started server may still be importing its modules.
            for _ in range(8):
                time.sleep(.25)
                if health():
                    log("复用刚就绪的本机服务")
                    return False
            raise LaunchError("17861 端口已有程序占用，但声织健康检查未通过；请检查现有进程")
        python = python_runtime()
        verify_runtime(python)
        LOGS.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", VOICE_LOOM_DATA=str(DATA))
        command = [str(python), "-m", "uvicorn", "backend.app:app", "--host", HOST,
                   "--port", str(PORT), "--no-access-log", "--no-proxy-headers"]
        try:
            with (LOGS / "server.log").open("ab") as stdout, (LOGS / "server-error.log").open("ab") as stderr:
                child = subprocess.Popen(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                         stdout=stdout, stderr=stderr, creationflags=NO_WINDOW)
        except OSError as exc:
            raise LaunchError("本机服务启动失败，请检查 Python 路径") from exc
        (DATA / "server.pid").write_text(str(child.pid), encoding="ascii")
        log(f"启动本机服务，PID={child.pid}，Python={python}")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if health():
                log("本机服务已就绪，监听保持关闭")
                return True
            if child.poll() is not None:
                raise LaunchError("本机服务已退出，请查看 data/logs/server-error.log")
            time.sleep(.25)
        raise LaunchError("本机服务仍未就绪，请查看 data/logs/server-error.log 后重新打开")


def edge_path():
    locations = [
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    found = shutil.which("msedge")
    if found:
        locations.insert(0, Path(found))
    return next((candidate for candidate in locations if candidate.is_file()), None)


def open_window():
    edge = edge_path()
    if edge is None:
        raise LaunchError("服务已启动，但未找到 Microsoft Edge。请在浏览器打开 " + URL)
    try:
        from backend.access import LocalAccess
        url = URL + "/#session=" + LocalAccess(DATA).browser_session
        subprocess.Popen([str(edge), "--app=" + url, "--window-size=1480,960", "--no-first-run"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    except OSError as exc:
        raise LaunchError("服务已启动，Edge 窗口未能打开。请在浏览器打开 " + URL) from exc


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="启动声织本机工作台；启动后保持麦克风监听关闭")
    parser.add_argument("--no-browser", action="store_true", help="只启动或复用后台服务")
    parser.add_argument("--status", action="store_true", help="只检查本机服务状态，不启动服务")
    parser.add_argument("--show-errors", action="store_true", help="启动失败时显示 Windows 提示框")
    args = parser.parse_args(argv)
    if args.status:
        result = health()
        print(json.dumps(result or {"ok": False, "name": "声织", "url": URL}, ensure_ascii=False))
        return 0 if result else 1
    try:
        started = ensure_server()
        if not args.no_browser:
            open_window()
        print(("已启动" if started else "已复用") + "声织：" + URL)
        return 0
    except (LaunchError, OSError) as exc:
        message = str(exc)
        try:
            log("启动失败：" + message)
        except OSError:
            pass
        print(message, file=sys.stderr)
        if args.show_errors and os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "声织启动", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
