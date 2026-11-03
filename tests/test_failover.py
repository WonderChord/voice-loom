"""断线切换、恢复、取消和 HTTP 用量的端到端验证；音频设备与网络全部隔离。"""
import copy
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import test_service as service_tests
from test_service import FakeAudio, FakeProviders, create_app, wav_bytes
from backend import providers as p
from backend.hotkeys import NullHotkeys
from backend.models import Config
from backend.pipeline import validate_listening_config


class FailoverProviders(FakeProviders):
    is_unavailable = staticmethod(p.is_unavailable)
    usage_sink = p.usage_sink

    def __init__(self):
        super().__init__()
        self.tts_calls = []
        self.primary_error = p.ProviderUnavailableError("主服务断线")
        self.fallback_error = None
        self.real_tts = False
        self.probe = {"ok": True}
        self.probed = threading.Event()

    def synthesize(self, provider, plan, reference, output_path, config):
        self.tts_calls.append(provider["id"])
        if self.real_tts:
            return p.synthesize(provider, plan, reference, output_path, config)
        error = self.primary_error if provider["id"] == "primary" else self.fallback_error
        if error:
            raise error
        return super().synthesize(provider, plan, reference, output_path, config)

    def test_provider(self, provider):
        self.probed.set()
        return dict(self.probe)


class FailoverTests(unittest.TestCase):
    wait_record = service_tests.ServiceTests.wait_record
    submit = service_tests.ServiceTests.submit

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.providers = FailoverProviders()
        self.app = create_app(self.folder.name, providers=self.providers, audio_factory=FakeAudio,
                              hotkey_factory=NullHotkeys, seed_reference=False)
        self.pipeline = self.app.state.pipeline
        self.client = TestClient(self.app, base_url="http://127.0.0.1:17861", client=("127.0.0.1", 50000),
                                 headers={"Authorization": "Bearer " + self.app.state.access.browser_session})
        self.client.__enter__()
        config = self.app.state.store.config()
        config["providers"] += [{"id": key, "kind": "tts", "type": "openai_tts", "name": name,
                                  "base_url": "http://127.0.0.1:19765", "model": "tts-1", "voice": "alloy"}
                                 for key, name in (("primary", "主服务"), ("fallback", "备用 API"))]
        config["active"].update(tts="primary", tts_fallback="fallback")
        config["audio"]["auto_play"] = False
        self.pipeline.update_config(Config.model_validate(config))

    def tearDown(self):
        self.providers.hold.set()
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def save(self, edit):
        config = self.app.state.store.config()
        edit(config)
        return self.pipeline.update_config(Config.model_validate(config))

    def test_fallback_cooldown_and_successful_primary_retry(self):
        first = self.wait_record(self.submit(preview=False))
        self.assertEqual(first["status"], "done")
        self.assertEqual(self.providers.tts_calls, ["primary", "fallback"])
        state = self.client.get("/api/state").json()["tts_failover"]
        self.assertEqual((state["primary"], state["fallback"]), ("primary", "fallback"))
        self.assertNotIn("retry_at", state)
        self.assertTrue(state["since"])
        self.assertTrue(any("备用 API" in w for w in first["plan"]["warnings"]))
        self.wait_record(self.submit(preview=False))
        self.assertEqual(self.providers.tts_calls, ["primary", "fallback", "fallback"])
        self.providers.primary_error = None
        with self.pipeline.lock:
            self.pipeline.tts_failover["retry_at"] = 0
        self.wait_record(self.submit(preview=False))
        self.assertEqual(self.providers.tts_calls[-1], "primary")
        self.assertIsNone(self.pipeline.state()["tts_failover"])

    def test_only_unavailable_errors_fall_back(self):
        for status in (400, 401, 402, 403, 404, 409, 422, 429, 500):
            with self.subTest(status=status):
                self.providers.primary_error = p.ProviderHTTPError("请求失败", status)
                record = self.wait_record(self.submit(preview=False))
                self.assertEqual(record["status"], "error")
                self.assertIsNone(self.pipeline.tts_failover)
        self.assertNotIn("fallback", self.providers.tts_calls)
        self.providers.primary_error = p.ProviderHTTPError("隧道失效", 530)
        self.assertEqual(self.wait_record(self.submit(preview=False))["status"], "done")
        self.assertEqual(self.providers.tts_calls[-1], "fallback")

    def test_missing_or_same_fallback_preserves_primary_error(self):
        for fallback in ("", "primary"):
            self.save(lambda c: c["active"].update(tts_fallback=fallback))
            self.assertEqual(self.wait_record(self.submit(preview=False))["status"], "error")
        self.assertEqual(self.providers.tts_calls, ["primary", "primary"])

    def test_fallback_failure_is_reported_without_retry_loop(self):
        self.providers.fallback_error = p.ProviderHTTPError("备用密钥无效", 401)
        record = self.wait_record(self.submit(preview=False))
        self.assertEqual(record["status"], "error")
        self.assertIn("备用密钥无效", record["error"])
        self.assertEqual(self.providers.tts_calls, ["primary", "fallback"])

    def test_config_and_character_changes_clear_old_failover(self):
        self.wait_record(self.submit(preview=False))
        self.save(lambda c: c["active"].update(tts_fallback=""))
        self.assertIsNone(self.pipeline.tts_failover)
        self.save(lambda c: c["active"].update(tts_fallback="fallback"))
        self.wait_record(self.submit(preview=False))
        def change_address(config):
            next(p for p in config["providers"] if p["id"] == "primary")["base_url"] = "http://127.0.0.1:19766"
            config["characters"] = [{"id": "other", "name": "另一个角色", "tts": "fallback"}]
        self.save(change_address)
        self.assertIsNone(self.pipeline.tts_failover)
        self.wait_record(self.submit(preview=False))
        self.pipeline.apply_character("other")
        self.assertIsNone(self.pipeline.tts_failover)

    def test_stale_job_does_not_restore_previous_selection_state(self):
        original = self.providers.synthesize
        def synthesize(provider, *args):
            if provider["id"] == "primary":
                self.save(lambda c: c["active"].update(tts="fallback", tts_fallback=""))
            return original(provider, *args)
        self.providers.synthesize = synthesize
        self.assertEqual(self.wait_record(self.submit(preview=False))["status"], "done")
        self.assertEqual(self.providers.tts_calls, ["primary", "fallback"])
        self.assertIsNone(self.pipeline.tts_failover)

    def test_cancel_during_primary_failure_never_calls_fallback(self):
        original = self.providers.synthesize
        def synthesize(provider, *args):
            self.pipeline.stop()
            return original(provider, *args)
        self.providers.synthesize = synthesize
        record = self.wait_record(self.submit(preview=False))
        self.assertEqual(record["status"], "cancelled")
        self.assertNotIn("fallback", self.providers.tts_calls)
        self.assertIsNone(self.pipeline.tts_failover)

    def start_probe(self):
        def remote(config):
            primary = next(p for p in config["providers"] if p["id"] == "primary")
            primary.update(base_url="https://tts.example.test", api_key="test-only-key")
        self.save(remote)
        self.pipeline.FAILOVER_PROBE_S = .02
        self.pipeline.REMOTE_PROBE_INTERVAL_S = .02
        self.pipeline.listen()

    def eventually(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_probe_switches_on_outage_and_recovers(self):
        self.providers.probe = {"ok": False, "message": "隧道断线", "unavailable": True}
        self.start_probe()
        self.eventually(lambda: self.pipeline.tts_failover is not None)
        retry_at = self.pipeline.tts_failover["retry_at"]
        self.providers.probed.clear()
        self.assertTrue(self.providers.probed.wait(2))
        self.assertEqual(retry_at, self.pipeline.tts_failover["retry_at"])
        self.providers.probe = {"ok": True}
        self.eventually(lambda: self.pipeline.tts_failover is None)
        self.assertEqual(self.providers.tts_calls, [])
        self.assertEqual(self.pipeline.usage.summary()["providers"], [])

    def test_probe_authentication_error_keeps_primary(self):
        self.providers.probe = {"ok": False, "message": "密钥无效", "unavailable": False}
        self.start_probe()
        self.eventually(lambda: self.pipeline.tts_remote is not None)
        self.assertFalse(self.pipeline.tts_remote["ok"])
        self.assertIsNone(self.pipeline.tts_failover)

    def test_probe_finishing_after_stop_cannot_change_state(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(_):
            entered.set()
            release.wait(2)
            return {"ok": False, "message": "旧探测结果", "unavailable": True}
        self.providers.test_provider = blocked
        self.start_probe()
        self.assertTrue(entered.wait(2))
        self.pipeline.stop()
        release.set()
        self.pipeline.probe_thread.join(2)
        self.assertIsNone(self.pipeline.tts_remote)
        self.assertIsNone(self.pipeline.tts_failover)

    def test_real_provider_http_counts_and_new_session(self):
        self.providers.real_tts = True
        def response(provider, *args, **kwargs):
            if provider["id"] == "primary":
                raise p.ProviderUnavailableError("断线")
            return Mock(content=wav_bytes(), headers={"Content-Type": "audio/wav"})
        with patch.object(p, "_send", side_effect=response):
            record = self.wait_record(self.submit(preview=False))
        self.assertEqual(record["status"], "done")
        result = self.client.get("/api/usage?days=30").json()
        totals = {r["id"]: r for r in result["session"]}
        self.assertEqual((totals["primary"]["requests"], totals["primary"]["failures"]), (1, 1))
        self.assertEqual((totals["fallback"]["requests"], totals["fallback"]["failures"]), (1, 0))
        self.assertEqual(totals["fallback"]["chars"], len(record["text"]))
        self.assertAlmostEqual(totals["fallback"]["audio_s"], .2)
        self.assertEqual(totals["fallback"]["fallbacks"], 1)
        self.client.post("/api/session/clear")
        after = self.client.get("/api/usage").json()
        self.assertEqual(after["session"], [])
        self.assertEqual(after["providers"], result["providers"])
        for days in (0, 91, "bad"):
            self.assertEqual(self.client.get(f"/api/usage?days={days}").status_code, 422)

    def test_fallback_listening_validation_checks_voice_and_credentials(self):
        config = copy.deepcopy(self.app.state.store.config(True))
        fallback = next(p for p in config["providers"] if p["id"] == "fallback")
        fallback["voice"] = ""
        with self.assertRaisesRegex(ValueError, "备用 TTS .*声音 ID"):
            validate_listening_config(config, None)
        reference = {"provider_voice_ids": {"fallback": "bound-voice"}}
        validate_listening_config(config, reference)
        fallback["base_url"] = "https://tts.example.test"
        with self.assertRaisesRegex(ValueError, "备用 TTS .*密钥"):
            validate_listening_config(config, reference)
