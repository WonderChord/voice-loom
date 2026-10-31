"""自检运行态、取消保护和字幕发布顺序；请求均由本地桩替代。"""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend import providers as p
from backend.store import Store
from test_service import FakeAudio, create_app


def candidate(delivery="normal"):
    return {"source_text": "原话", "text": "我想再试一次。",
            "segments": [{"kind": "speech", "text": "我想再试一次。", "emotion": "neutral",
                          "intensity": 0, "delivery": delivery}], "warnings": [],
            "audit": {"passed": True, "message": "生成器自报通过"}}


def response(value):
    result = Mock()
    result.json.return_value = {"choices": [{"message": {"content": json.dumps(value)}}]}
    return result


class AuditCallbackTests(unittest.TestCase):
    @patch.object(p, "_request")
    def test_pending_snapshot_precedes_independent_request(self, request):
        request.side_effect = [response(candidate()), response({"passed": True, "uncertain": False,
                                                               "message": "审核完成", "leaked_instructions": []})]
        snapshots = []
        def on_audit(plan):
            self.assertEqual(request.call_count, 1)
            self.assertFalse(plan["audit"]["passed"])
            snapshots.append(plan)
            plan["segments"][0]["text"] = "回调隔离验证"
        result = p.rewrite({"type": "openai_llm", "model": "mock"}, "原话", [],
                           {"rewrite_enabled": True, "_on_audit": on_audit})
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(result["segments"][0]["text"], "我想再试一次。")
        self.assertTrue(result["audit"]["passed"])
        self.assertFalse(snapshots[0]["audit"]["passed"])
        self.assertEqual(request.call_count, 2)

    @patch.object(p, "_request")
    def test_direct_mode_reports_local_review_and_keeps_control_guard(self, request):
        for text, passed in [("我今天有点累。", True), ("这段用耳语，我马上回来。", False)]:
            with self.subTest(text=text):
                pending = []
                result = p.rewrite({}, text, [], {"rewrite_enabled": False, "_on_audit": pending.append})
                self.assertEqual(pending[0]["text"], text)
                self.assertFalse(pending[0]["audit"]["passed"])
                self.assertIn("本地", pending[0]["audit"]["message"])
                self.assertEqual(result["audit"]["passed"], passed)
        request.assert_not_called()

    @patch.object(p, "_request")
    def test_cancellation_in_callback_prevents_second_request(self, request):
        request.return_value = response(candidate())
        cancelled = threading.Event()
        def guard():
            if cancelled.is_set():
                raise InterruptedError("cancelled")
        with self.assertRaises(InterruptedError):
            p.rewrite({"type": "openai_llm", "model": "mock"}, "原话", [],
                      {"rewrite_enabled": True, "_on_audit": lambda plan: cancelled.set(), "_cancel_check": guard})
        self.assertEqual(request.call_count, 1)

    @patch.object(p, "_request")
    def test_audit_toggle_and_separate_audit_llm(self, request):
        request.return_value = response(candidate())
        result = p.rewrite({"type": "openai_llm", "model": "rewrite-model"}, "原话", [],
                           {"rewrite_enabled": True, "audit_enabled": False})
        self.assertEqual(request.call_count, 1)
        self.assertTrue(result["audit"]["passed"])
        self.assertIn("已关闭", result["audit"]["message"])

        request.reset_mock()
        # 原文直通没有 LLM 生成，默认只做本地复核；只有显式指定自检 LLM 才发起独立自检。
        direct_local = p.rewrite({"type": "openai_llm", "model": "audit-only-model"}, "我今天有点累。", [],
                                 {"rewrite_enabled": False, "audit_enabled": True})
        request.assert_not_called()
        self.assertEqual(direct_local["text"], "我今天有点累。")
        self.assertTrue(direct_local["audit"]["passed"])

        request.reset_mock()
        request.return_value = response({"passed": True, "uncertain": False,
                                         "message": "直通自检通过", "leaked_instructions": []})
        direct_audited = p.rewrite({"type": "openai_llm", "model": "audit-only-model"}, "我今天有点累。", [],
                                   {"rewrite_enabled": False, "audit_enabled": True,
                                    "active": {"llm": "rewrite-model", "audit_llm": "audit-only-model"}})
        self.assertEqual(request.call_count, 1)
        self.assertEqual(direct_audited["text"], "我今天有点累。")
        self.assertTrue(direct_audited["audit"]["passed"])

        request.reset_mock()
        request.side_effect = [
            response(candidate()),
            response({"passed": True, "uncertain": False, "message": "本地自检通过", "leaked_instructions": []}),
        ]
        providers_list = [
            {"id": "cloud-llm", "kind": "llm", "type": "openai_llm", "model": "cloud-rewrite"},
            {"id": "local-llm", "kind": "llm", "type": "openai_llm", "model": "local-auditor", "base_url": "http://127.0.0.1:11434/v1"},
        ]
        p.rewrite(providers_list[0], "原话", [],
                  {"rewrite_enabled": True, "audit_enabled": True,
                   "active": {"llm": "cloud-llm", "audit_llm": "local-llm"},
                   "providers": providers_list})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0]["id"], "cloud-llm")
        self.assertEqual(request.call_args_list[1].args[0]["id"], "local-llm")


class AuditStageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = 0
        self.block_request = 2
        self.passed = True
        self.draft = candidate()
        self.app = create_app(self.folder.name, providers=p, audio_factory=FakeAudio,
                              devices_fn=lambda: {"inputs": [], "outputs": []}, seed_reference=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:17861", client=("127.0.0.1", 50000), headers={"Authorization": "Bearer " + self.app.state.access.browser_session})
        self.client.__enter__()
        config = self.client.get("/api/config").json()
        config["rewrite_enabled"] = True
        next(provider for provider in config["providers"] if provider["id"] == config["active"]["llm"])["model"] = "mock"
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 200)
        self.request_patch = patch.object(p, "_request", side_effect=self.request)
        self.request_patch.start()

    def tearDown(self):
        self.release.set()
        self.client.__exit__(None, None, None)
        self.request_patch.stop()
        self.folder.cleanup()

    def request(self, *args, **kwargs):
        self.calls += 1
        if self.calls == self.block_request:
            self.entered.set()
            if not self.release.wait(3):
                raise AssertionError("测试未释放请求")
        if self.calls == 1:
            return response(self.draft)
        return response({"passed": self.passed, "uncertain": False,
                         "message": "审核完成" if self.passed else "需要确认原意", "leaked_instructions": []})

    def submit(self):
        result = self.client.post("/api/process/text", json={"text": "原话", "preview_only": True})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(self.entered.wait(2))
        return result.json()["id"]

    def finish(self, record_id):
        self.release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            record = self.app.state.store.record(record_id)
            if self.app.state.pipeline.active is None:
                return record
            time.sleep(.01)
        self.fail("流水线未完成")

    def test_polling_sees_pending_draft_before_approved_caption(self):
        record_id = self.submit()
        state = self.client.get("/api/state").json()
        self.assertEqual(state["stage"], "audit")
        record = next(row for row in state["records"] if row["id"] == record_id)
        self.assertEqual(record["status"], "audit")
        self.assertEqual(record["text"], self.draft["text"])
        self.assertFalse(record["plan"]["audit"]["passed"])
        self.assertIn("进行中", record["plan"]["audit"]["message"])
        saved = json.loads((Path(self.folder.name) / "records.json").read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["status"], "audit")
        self.assertEqual(self.client.get("/api/subtitles/current").json()["text"], "")
        final = self.finish(record_id)
        self.assertEqual(final["status"], "done")
        self.assertTrue(final["plan"]["audit"]["passed"])
        self.assertEqual(self.client.get("/api/subtitles/current").json()["text"], self.draft["text"])

    def test_review_failure_keeps_candidate_and_suppresses_captions(self):
        self.passed = False
        record_id = self.submit()
        final = self.finish(record_id)
        self.assertEqual(final["status"], "review")
        self.assertEqual(final["text"], self.draft["text"])
        self.assertFalse(final["plan"]["audit"]["passed"])
        self.assertEqual(self.client.get("/api/subtitles/current").json()["text"], "")

    def test_tts_capability_review_still_precedes_caption_publish(self):
        self.draft = candidate(delivery="whisper")
        original = p.review_for_provider
        with patch.object(p, "review_for_provider", side_effect=lambda provider, plan, config:
                          original({"type": "gptsovits"}, plan, config)):
            degraded = self.finish(self.submit())
        self.assertEqual(degraded["status"], "done")
        self.assertTrue(any("耳语" in w for w in degraded["plan"]["warnings"]))
        self.calls = 0
        self.entered.clear()
        self.release.clear()
        self.draft = {"source_text": "原话", "text": "", "segments": [{"kind": "event", "event": "sigh"}],
                      "warnings": [], "audit": {"passed": True, "message": "生成器自报通过"}}
        with patch.object(p, "review_for_provider", side_effect=lambda provider, plan, config:
                          original({"type": "gptsovits"}, plan, config)):
            final = self.finish(self.submit())
        self.assertEqual(final["status"], "review")
        self.assertIn("TTS", final["plan"]["audit"]["message"])

    def test_stop_during_audit_prevents_final_result_and_caption(self):
        record_id = self.submit()
        self.app.state.pipeline.stop()
        final = self.finish(record_id)
        self.assertEqual(final["status"], "cancelled")
        self.assertFalse(final["plan"]["audit"]["passed"])
        self.assertEqual(self.client.get("/api/subtitles/current").json()["text"], "")

    def test_stop_during_generation_prevents_late_audit_draft(self):
        self.block_request = 1
        record_id = self.submit()
        self.app.state.pipeline.stop()
        final = self.finish(record_id)
        self.assertEqual(final["status"], "cancelled")
        self.assertIsNone(final["plan"])
        self.assertEqual(final["text"], "")
        self.assertEqual(self.calls, 1)

    def test_restart_cancels_audit_and_retains_pending_draft(self):
        record_id = self.submit()
        restored = Store(self.folder.name, seed_reference=False)
        record = restored.record(record_id)
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["text"], self.draft["text"])
        self.assertFalse(record["plan"]["audit"]["passed"])


if __name__ == "__main__":
    unittest.main()
