"""审核后字幕、OBS 文件与可恢复 SSE；全程使用模拟服务。"""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

import numpy as np
from fastapi.testclient import TestClient
from starlette.requests import Request

from test_service import FakeAudio, FakeProviders, create_app, wav_bytes
from backend.subtitles import SubtitleHub


class SubtitleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.providers = FakeProviders()
        self.app = create_app(self.folder.name, providers=self.providers, audio_factory=FakeAudio, seed_reference=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:17861", client=("127.0.0.1", 50000), headers={"Authorization": "Bearer " + self.app.state.access.browser_session})
        self.client.__enter__()

    def tearDown(self):
        self.providers.hold.set()
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def current(self):
        return self.client.get("/api/subtitles/current").json()

    def wait_record(self, record_id):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            record = self.app.state.store.record(record_id)
            if record["status"] in {"done", "review", "skipped", "error", "cancelled"}:
                return record
            time.sleep(.01)
        self.fail("字幕任务超时")

    def submit(self, text="测试字幕", preview=True):
        result = self.client.post("/api/process/text", json={"text": text, "preview_only": preview})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["id"]

    def test_approved_preview_exports_pure_text_and_manual_synthesis_republishes(self):
        response = self.client.post("/api/preview", json={"text": "[sad]你好，<whisper>世界</whisper>"})
        record = self.wait_record(response.json()["id"])
        current = self.current()
        self.assertEqual(current["status"], "approved")
        self.assertEqual(current["text"], "你好，世界。")
        self.assertEqual(current["record_id"], record["id"])
        self.assertEqual(Path(current["text_file"]).read_bytes(), "你好，世界。".encode("utf-8"))
        self.assertFalse(any(call[0] == "tts" for call in self.providers.calls))
        repeated = self.client.post(f"/api/records/{record['id']}/synthesize", json={"plan": record["plan"]})
        second = self.wait_record(repeated.json()["id"])
        self.assertEqual(self.current()["record_id"], second["id"])
        self.assertNotEqual(self.current()["event_id"], current["event_id"])

    def test_review_error_skip_and_control_only_publish_nothing(self):
        initial = self.current()["event_id"]
        self.providers.review = True
        self.assertEqual(self.wait_record(self.submit())["status"], "review")
        self.providers.review = False
        self.providers.malformed = True
        self.assertEqual(self.wait_record(self.submit())["status"], "error")
        self.providers.malformed = False
        self.providers.directive = True
        self.assertEqual(self.wait_record(self.submit())["status"], "done")
        self.providers.directive = False
        self.providers.event_only = True
        self.assertEqual(self.wait_record(self.submit())["status"], "done")
        result = self.client.post("/api/process/audio", files={"file": ("silent.wav", wav_bytes(np.zeros(3200)))})
        self.assertEqual(self.wait_record(result.json()["id"])["status"], "skipped")
        self.assertEqual(self.current()["event_id"], initial)
        self.assertEqual(Path(self.current()["text_file"]).read_text(), "")

    def test_approved_text_remains_available_after_tts_failure(self):
        def fail(*args, **kwargs):
            raise ValueError("模拟 TTS 失败")
        self.providers.synthesize = fail
        record = self.wait_record(self.submit(preview=False))
        self.assertEqual(record["status"], "error")
        self.assertEqual(self.current()["status"], "approved")
        self.assertEqual(self.current()["record_id"], record["id"])
        self.assertEqual(self.current()["text"], "测试字幕。")

    def test_stop_prevents_late_publish_and_new_session_clears(self):
        self.providers.block = True
        task = self.submit()
        self.assertTrue(self.providers.entered.wait(2))
        self.client.post("/api/listen/stop")
        stopped_id = self.current()["event_id"]
        self.providers.hold.set()
        self.assertEqual(self.wait_record(task)["status"], "cancelled")
        time.sleep(.05)
        self.assertEqual(self.current()["event_id"], stopped_id)
        self.providers.block = False
        self.wait_record(self.submit())
        self.client.post("/api/session/clear")
        self.assertEqual(self.current()["reason"], "new_session")
        self.assertEqual(self.current()["text"], "")

    def test_disable_manual_clear_and_expiry_update_text_file(self):
        config = self.client.get("/api/config").json()
        config["subtitles"] = {"enabled": True, "display_seconds": 2}
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 200)
        self.wait_record(self.submit())
        path = Path(self.current()["text_file"])
        time.sleep(2.1)
        self.assertEqual(path.read_text(encoding="utf-8"), "")
        self.assertEqual(self.current()["reason"], "expired")
        self.wait_record(self.submit())
        self.app.state.pipeline.listening = True
        self.client.post("/api/subtitles/clear")
        self.assertTrue(self.app.state.pipeline.listening)
        self.app.state.pipeline.listening = False
        self.assertEqual(self.current()["reason"], "manual")
        config["subtitles"]["enabled"] = False
        self.client.put("/api/config", json=config)
        disabled_id = self.current()["event_id"]
        self.wait_record(self.submit())
        self.assertEqual(self.current()["status"], "disabled")
        self.assertEqual(self.current()["event_id"], disabled_id)
        config["subtitles"]["display_seconds"] = 61
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 422)

    def test_sse_route_cursor_replay_heartbeat_and_unsubscribe(self):
        initial = self.current()["event_id"]
        first = self.wait_record(self.submit("第一句"))
        second = self.wait_record(self.submit("第二句"))
        hub = self.app.state.pipeline.subtitles
        async def exercise():
            route = next(route for route in self.app.routes if getattr(route, "path", "") == "/api/subtitles/events")
            request = Request({"type": "http", "headers": [(b"last-event-id", initial.encode())], "query_string": b""})
            response = await route.endpoint(request)
            self.assertEqual(response.media_type, "text/event-stream")
            stream = response.body_iterator
            self.assertIn("retry:", await anext(stream))
            self.assertIn(first["id"], await anext(stream))
            self.assertIn(second["id"], await anext(stream))
            await stream.aclose()
            self.assertEqual(len(hub.subscribers), 0)
            live = hub.stream(hub.current()["event_id"], heartbeat_seconds=.01)
            await anext(live)
            self.assertEqual(await anext(live), ": heartbeat\n\n")
            hub.clear("manual")
            self.assertIn('"event":"clear"', await anext(live))
            await live.aclose()
        asyncio.run(exercise())
        self.assertEqual(hub.events_since("old-server:1")[0]["event_id"], hub.current()["event_id"])

    def test_browser_is_transparent_safe_and_stale_file_clears_on_restart(self):
        response = self.client.get("/subtitles")
        self.assertEqual(response.status_code, 200)
        self.assertIn("textContent", response.text)
        self.assertNotIn("innerHTML", response.text)
        self.assertIn("background:transparent", response.text)
        self.assertIn("/api/subtitles/events", response.text)
        self.assertEqual(self.client.get("/api/subtitles/current", headers={"origin": "https://evil.example"}).status_code, 403)
        with tempfile.TemporaryDirectory() as directory:
            first = SubtitleHub(Path(directory), {"enabled": True, "display_seconds": 8})
            first.publish("record", {"audit": {"passed": True}, "segments": [{"kind": "speech", "text": "过去字幕"}]})
            second = SubtitleHub(Path(directory), {"enabled": True, "display_seconds": 8})
            self.assertEqual(second.text_file.read_text(), "")
            self.assertNotEqual(first.current()["event_id"], second.current()["event_id"])
            first.close()
            second.close()


if __name__ == "__main__":
    unittest.main()
