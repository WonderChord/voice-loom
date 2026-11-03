"""用量统计：按天、按服务累计请求数、失败数、LLM token、TTS 字数与音频时长，保存在 usage.json。"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import uuid
from pathlib import Path

from .store import atomic_json

KEEP_DAYS = 90
FIELDS = ("requests", "failures", "tokens_in", "tokens_out", "chars", "audio_s", "input_s", "fallbacks")
SAVE_INTERVAL_S = 10


def _blank(provider: dict) -> dict:
    return {"name": provider.get("name", ""), "kind": provider.get("kind", ""),
            "type": provider.get("type", ""), **{field: 0 for field in FIELDS}}


def _add(target: dict, entry: dict):
    for field in FIELDS:
        if entry.get(field):
            target[field] = round(target.get(field, 0) + entry[field], 3)


class UsageLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.days: dict[str, dict] = {}
        self.session: dict[str, dict] = {}
        self.session_started = dt.datetime.now().isoformat(timespec="seconds")
        self.session_id = uuid.uuid4().hex
        self.dirty = False
        self.saved_at = 0.0
        self.timer: threading.Timer | None = None
        self.closed = False
        self.save_error = ""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data.get("days"), dict):
                self.days = data["days"]
        except (OSError, ValueError, AttributeError):
            pass

    def record(self, provider: dict, entry: dict, *, session_id: str | None = None):
        key = provider.get("id") or "unknown"
        today = dt.date.today().isoformat()
        with self.lock:
            day = self.days.setdefault(today, {})
            buckets = (day, self.session) if session_id is None or session_id == self.session_id else (day,)
            for bucket in buckets:
                item = bucket.setdefault(key, _blank(provider))
                item["name"] = provider.get("name", item["name"])
                _add(item, entry)
            self.dirty = True
            if self.closed or time.monotonic() - self.saved_at >= SAVE_INTERVAL_S:
                self._save()
            if self.dirty and not self.closed and self.timer is None:
                self.timer = threading.Timer(SAVE_INTERVAL_S, self.flush)
                self.timer.daemon = True
                self.timer.start()

    def flush(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            if self.dirty:
                self._save()

    def close(self):
        with self.lock:
            self.closed = True
        self.flush()

    def _save(self):
        cutoff = (dt.date.today() - dt.timedelta(days=KEEP_DAYS - 1)).isoformat()
        self.days = {day: value for day, value in self.days.items() if day >= cutoff}
        try:
            atomic_json(self.path, {"days": self.days})
        except OSError:
            self.save_error = "用量暂未写入磁盘，请检查数据目录的空间与写入权限。"
            return
        self.save_error = ""
        self.dirty = False
        self.saved_at = time.monotonic()

    def reset_session(self):
        with self.lock:
            self.session = {}
            self.session_started = dt.datetime.now().isoformat(timespec="seconds")
            self.session_id = uuid.uuid4().hex

    def summary(self, days: int = 30) -> dict:
        days = max(1, min(int(days), KEEP_DAYS))
        cutoff = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
        with self.lock:
            totals: dict[str, dict] = {}
            daily = []
            for day in sorted(self.days):
                if day < cutoff:
                    continue
                row = {"date": day, **{field: 0 for field in FIELDS}}
                for key, item in self.days[day].items():
                    _add(totals.setdefault(key, {k: item.get(k, "") for k in ("name", "kind", "type")}), item)
                    _add(row, item)
                daily.append(row)
            return {
                "days": days,
                "save_error": self.save_error,
                "session_started": self.session_started,
                "session": [{"id": key, **value} for key, value in self.session.items()],
                "providers": [{"id": key, **{field: 0 for field in FIELDS}, **value} for key, value in totals.items()],
                "daily": daily,
            }
