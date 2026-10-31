"""审核后字幕广播：本机 JSON/SSE、OBS 浏览器源与原子 UTF-8 文本输出。"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid


def utc_time(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(timestamp if timestamp is not None else time.time(), timezone.utc).isoformat()


def plain_subtitle(plan: dict) -> str:
    """Only approved speech contributes text; engine markup never enters captions."""
    if plan.get("needs_review") or not plan.get("audit", {}).get("passed", False):
        return ""
    speech = "".join(str(item.get("text") or "") for item in plan.get("segments", []) if item.get("kind") == "speech")
    # Keep the same inline direction removal convention as the TTS adapters.
    speech = re.sub(r"\[[^\]\n]{1,100}\]|<[^>\n]{1,100}>", "", speech)
    speech = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", speech)
    return speech.strip()


def atomic_text(path: Path, value: str):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        # OBS normally closes its read handle promptly; allow a brief Windows race.
        for attempt in range(3):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.025)
    finally:
        temporary.unlink(missing_ok=True)


class SubtitleHub:
    """One current caption with bounded event replay and an independent expiry timer."""

    def __init__(self, data_dir: Path, config: dict):
        self.text_file = Path(data_dir).resolve() / "exports" / "subtitles.txt"
        self.text_file.parent.mkdir(parents=True, exist_ok=True)
        self.condition = threading.Condition(threading.RLock())
        self.enabled = bool(config.get("enabled", True))
        self.display_seconds = int(config.get("display_seconds", 8))
        self.instance_id = uuid.uuid4().hex
        self.sequence = 0
        self.events: deque[dict] = deque(maxlen=128)
        self.subscribers: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self.current_event: dict = {}
        self.deadline: float | None = None
        self.closed = False
        self.timer: threading.Thread | None = None
        with self.condition:
            self._commit("clear", "cleared" if self.enabled else "disabled", "", None, "startup")

    def start(self):
        with self.condition:
            if self.timer and self.timer.is_alive():
                return
            self.closed = False
            self.timer = threading.Thread(target=self._expire_loop, name="voice-loom-subtitles", daemon=True)
            self.timer.start()

    def close(self):
        with self.condition:
            if self.closed:
                return
            self._commit("clear", "cleared" if self.enabled else "disabled", "", None, "shutdown")
            self.closed = True
            self.condition.notify_all()
            for loop, queue in self.subscribers.values():
                self._deliver(loop, queue, None)
        if self.timer and self.timer is not threading.current_thread():
            self.timer.join(timeout=1)

    def configure(self, config: dict):
        with self.condition:
            was_enabled = self.enabled
            self.enabled = bool(config["enabled"])
            self.display_seconds = int(config["display_seconds"])
            if self.enabled != was_enabled:
                self._commit("clear", "cleared" if self.enabled else "disabled", "", None, "enabled" if self.enabled else "disabled")

    @staticmethod
    def _deliver(loop, queue, event):
        def put():
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
            queue.put_nowait(event)
        try:
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None
            if current_loop is loop:
                put()
            else:
                loop.call_soon_threadsafe(put)
        except RuntimeError:
            pass  # A disconnected client may already have closed its event loop.

    def _commit(self, event: str, status: str, text: str, record_id: str | None, reason: str):
        self.sequence += 1
        timestamp = time.time()
        expires_at = utc_time(timestamp + self.display_seconds) if text else None
        self.deadline = time.monotonic() + self.display_seconds if text else None
        file_error = None
        try:
            atomic_text(self.text_file, text)
        except OSError:
            file_error = "字幕文本文件写入失败，请检查文件权限或占用；JSON 与浏览器源仍可使用"
        payload = {"event_id": f"{self.instance_id}:{self.sequence}", "sequence": self.sequence,
                   "event": event, "status": status, "record_id": record_id, "text": text,
                   "created_at": utc_time(timestamp), "expires_at": expires_at, "reason": reason,
                   "enabled": self.enabled, "text_file": str(self.text_file), "file_error": file_error}
        self.current_event = payload
        self.events.append(payload)
        self.condition.notify_all()
        for loop, queue in self.subscribers.values():
            self._deliver(loop, queue, dict(payload))
        return dict(payload)

    def publish(self, record_id: str, plan: dict) -> dict | None:
        text = plain_subtitle(plan)
        with self.condition:
            if self.closed or not self.enabled or not text:
                return None
            return self._commit("publish", "approved", text, record_id, "approved")

    def clear(self, reason="manual") -> dict:
        with self.condition:
            return self._commit("clear", "cleared" if self.enabled else "disabled", "", None, reason)

    def current(self) -> dict:
        with self.condition:
            self._expire_if_due()
            return dict(self.current_event)

    def _expire_if_due(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self._commit("clear", "cleared" if self.enabled else "disabled", "", None, "expired")

    def _expire_loop(self):
        with self.condition:
            while not self.closed:
                self._expire_if_due()
                wait = None if self.deadline is None else max(0.01, self.deadline - time.monotonic())
                self.condition.wait(wait)

    def events_since(self, event_id: str | None) -> list[dict]:
        with self.condition:
            self._expire_if_due()
            if event_id:
                for index, item in enumerate(self.events):
                    if item["event_id"] == event_id:
                        return [dict(event) for event in list(self.events)[index + 1:]]
            # New clients or cursors older than the bounded history get a snapshot.
            return [dict(self.current_event)]

    async def stream(self, last_event_id: str | None = None, *, heartbeat_seconds=15):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=128)
        token = uuid.uuid4().hex
        with self.condition:
            initial = self.events_since(last_event_id)
            self.subscribers[token] = (loop, queue)
        try:
            yield "retry: 1500\n\n"
            for event in initial:
                yield encode_sse(event)
            while not self.closed:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event is None:
                    return
                yield encode_sse(event)
        finally:
            with self.condition:
                self.subscribers.pop(token, None)


def encode_sse(event: dict) -> str:
    return f"id: {event['event_id']}\nevent: subtitle\ndata: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n\n"


SUBTITLE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>声织 · 直播字幕</title><style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent!important}
body{display:flex;align-items:flex-end;justify-content:center}
#caption{max-width:90vw;margin:0 0 6vh;padding:0 12px;color:#fff;font:600 clamp(24px,3.5vw,56px)/1.35 "Microsoft YaHei","Segoe UI",sans-serif;
white-space:pre-wrap;overflow-wrap:anywhere;text-align:center;text-shadow:0 2px 5px #000,0 0 3px #000,2px 0 3px #000,-2px 0 3px #000}
</style></head><body><div id="caption" aria-live="polite"></div><script>
const caption=document.getElementById('caption');let timer=null,lastId='';
function render(value){
 if(value.event_id===lastId)return;lastId=value.event_id;clearTimeout(timer);
 const remaining=value.expires_at?Date.parse(value.expires_at)-Date.now():0;
 caption.textContent=value.enabled&&value.status==='approved'&&remaining>0?value.text:'';
 if(caption.textContent)timer=setTimeout(()=>{caption.textContent=''},remaining);
}
const access=new URLSearchParams(location.search).get('access');
const source=new EventSource('/api/subtitles/events'+(access?'?access='+encodeURIComponent(access):''));
source.addEventListener('subtitle',event=>{try{render(JSON.parse(event.data))}catch{}});
window.addEventListener('pagehide',()=>source.close());
</script></body></html>"""
