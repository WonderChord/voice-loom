"""请求计数、token、会话隔离及延迟持久化。"""
import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import requests

from backend import providers as p
from backend.usage import UsageLog

PROVIDER = {"id": "llm", "kind": "llm", "type": "openai_llm", "name": "云端 LLM", "base_url": "https://llm.example.test"}


class UsageTests(unittest.TestCase):
    def test_request_counts_tokens_failures_and_excludes_other_threads(self):
        calls = []
        with patch.object(p, "_send", return_value=Mock(json=lambda: {"usage": {"prompt_tokens": 18, "completion_tokens": 7}})) as send:
            p._request(PROVIDER, "GET", "/v1/models")
            token = p.usage_sink.set(lambda provider, entry: calls.append((provider["id"], entry)))
            try:
                p._request(PROVIDER, "POST", "/v1/chat/completions")
                send.side_effect = p.ProviderHTTPError("限流", 429)
                with self.assertRaises(p.ProviderHTTPError):
                    p._request(PROVIDER, "POST", "/v1/chat/completions")
            finally:
                p.usage_sink.reset(token)
        self.assertEqual(calls, [("llm", {"requests": 1, "failures": 0, "tokens_in": 18, "tokens_out": 7}),
                                 ("llm", {"requests": 1, "failures": 1})])
        self.assertIsNone(p.usage_sink.get())

    def test_network_errors_are_unavailable_and_probe_retains_classification(self):
        for exc in (requests.Timeout(), requests.ConnectionError()):
            with self.subTest(error=type(exc).__name__), patch.object(p.requests, "Session") as session:
                session.return_value.__enter__.return_value.request.side_effect = exc
                with self.assertRaises(p.ProviderUnavailableError):
                    p._request(PROVIDER, "POST", "/v1/chat/completions")
        for status in (502, 503, 504, 520, 522, 530, 401, 429):
            with self.subTest(status=status), patch.object(p, "_request", side_effect=p.ProviderHTTPError("错误", status)):
                result = p.test_provider(PROVIDER)
                self.assertEqual(result["unavailable"], status not in (401, 429))

    def test_persistence_pruning_daily_totals_and_old_session_response(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.json"
            old = (dt.date.today() - dt.timedelta(days=90)).isoformat()
            path.write_text(json.dumps({"days": {old: {"llm": {"requests": 999}}}}), encoding="utf-8")
            usage = UsageLog(path)
            old_session = usage.session_id
            usage.record(PROVIDER, {"requests": 1, "tokens_in": 3})
            usage.reset_session()
            usage.record(PROVIDER, {"tokens_out": 8}, session_id=old_session)
            usage.record(PROVIDER, {"requests": 2, "failures": 1, "chars": 12, "audio_s": .2})
            usage.close()
            current = usage.summary(30)
            self.assertEqual(current["session"][0]["requests"], 2)
            self.assertEqual(current["session"][0]["tokens_out"], 0)
            self.assertEqual(current["providers"][0]["requests"], 3)
            self.assertEqual(current["providers"][0]["tokens_out"], 8)
            self.assertEqual(current["daily"][0]["chars"], 12)
            self.assertNotIn(old, json.loads(path.read_text(encoding="utf-8"))["days"])
            reopened = UsageLog(path)
            self.assertEqual(reopened.summary()["providers"], current["providers"])
            self.assertEqual(reopened.summary()["session"], [])
            reopened.close()

    def test_last_update_is_flushed_when_no_more_requests_arrive(self):
        with tempfile.TemporaryDirectory() as folder, patch("backend.usage.SAVE_INTERVAL_S", .03):
            path = Path(folder) / "usage.json"
            usage = UsageLog(path)
            try:
                usage.record(PROVIDER, {"requests": 1})
                usage.record(PROVIDER, {"tokens_out": 19})
                deadline = time.monotonic() + 2
                while usage.dirty and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(usage.dirty)
                saved = next(iter(json.loads(path.read_text(encoding="utf-8"))["days"].values()))
                self.assertEqual(saved["llm"]["tokens_out"], 19)
            finally:
                usage.close()

    def test_disk_failure_keeps_counts_and_recovers_on_flush(self):
        with tempfile.TemporaryDirectory() as folder:
            usage = UsageLog(Path(folder) / "usage.json")
            with patch("backend.usage.atomic_json", side_effect=OSError("disk full")):
                usage.record(PROVIDER, {"requests": 1})
                self.assertTrue(usage.summary()["save_error"])
                self.assertEqual(usage.summary()["session"][0]["requests"], 1)
            usage.close()
            self.assertFalse(usage.summary()["save_error"])
