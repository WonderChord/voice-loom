"""目标语言合同：翻译审核、任务快照、历史稿和本地 TTS 路由。

所有服务响应均由模拟对象提供；临时应用数据与真实配置隔离。
"""
from __future__ import annotations

import base64
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

# This helper imports backend.app under a temporary VOICE_LOOM_DATA directory.
from test_service import FakeAudio, FakeProviders, create_app, wav_bytes
from backend import providers as p
from backend.models import Config, Plan
from backend.pipeline import validate_listening_config
from backend.store import Store


TARGETS = ("original", "zh", "en", "ja", "ko", "yue", "zh_en")
LABELS = {"zh": "普通话", "en": "英语", "ja": "日语", "ko": "韩语", "yue": "粤语", "zh_en": "中英混说"}
TRANSLATIONS = {
    "zh": "明天三点，我会再来。", "en": "I will come back tomorrow at three.",
    "ja": "明日の三時に、また来ます。", "ko": "내일 세 시에 다시 올게요.",
    "yue": "我听日三点会再嚟。", "zh_en": "明天三点，I will come back.",
}


def draft(text="I will come back tomorrow at three.", target="en"):
    return {"source_text": "模型返回的原文占位", "text": text, "target_language": target,
            "segments": [{"kind": "speech", "text": text, "emotion": "neutral", "intensity": 0}],
            "warnings": [], "needs_review": False,
            "audit": {"passed": True, "message": "候选稿自报通过"}}


def completion(value):
    return Mock(json=lambda: {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]})


def audit(passed=True):
    return completion({"passed": passed, "uncertain": False,
                       "message": "目标语言和原意已核对" if passed else "译文遗漏原文信息",
                       "leaked_instructions": []})


@contextmanager
def application(providers):
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory, providers=providers, audio_factory=FakeAudio,
                         devices_fn=lambda: {"inputs": [], "outputs": []}, seed_reference=False)
        with TestClient(app, base_url="http://127.0.0.1:17861", client=("127.0.0.1", 50000), headers={"Authorization": "Bearer " + app.state.access.browser_session}) as client:
            try:
                yield app, client, Path(directory)
            finally:
                hold = getattr(providers, "hold", None)
                if hold is not None:
                    hold.set()


class TrackingProviders(FakeProviders):
    def synthesize(self, provider, plan, reference, output_path, config):
        self.calls.append(("language_tts", copy.deepcopy(plan), config["target_language"],
                           config.get("_human_confirmed", False)))
        return super().synthesize(provider, plan, reference, output_path, config)


class TargetLanguageTests(unittest.TestCase):
    def wait_record(self, app, record_id):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            record = app.state.store.record(record_id)
            if record["status"] in {"done", "review", "error", "cancelled"}:
                return record
            time.sleep(.01)
        self.fail("目标语言任务未在预期时间内完成")

    def save_config(self, client, **changes):
        config = client.get("/api/config").json()
        config.update(changes)
        response = client.put("/api/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, client, text, preview=True):
        response = client.post("/api/process/text", json={"text": text, "preview_only": preview})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def test_language_values_persist_and_legacy_config_defaults_to_original(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory, seed_reference=False)
            for target in TARGETS:
                with self.subTest(target=target):
                    config = Config.model_validate({**store.config(), "target_language": target,
                                                    "persona": "持久化角色", "style_strength": "strong"})
                    store.save_config(config)
                    restored = Store(directory, seed_reference=False).config()
                    self.assertEqual(restored["target_language"], target)
                    self.assertEqual(restored["persona"], "持久化角色")
                    self.assertEqual(restored["style_strength"], "strong")
            path = Path(directory) / "config.json"
            legacy = json.loads(path.read_text(encoding="utf-8"))
            legacy.pop("target_language")
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            restored = Store(directory, seed_reference=False).config()
            self.assertEqual(restored["target_language"], "original")
            self.assertEqual(restored["persona"], "持久化角色")
        legacy_plan = draft()
        legacy_plan.pop("target_language")
        self.assertEqual(Plan.model_validate(legacy_plan).target_language, "original")
        self.assertEqual(p.validate_plan(legacy_plan)["target_language"], "original")
        for invalid in ("fr", "auto", "EN", "", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                Config(target_language=invalid)
            with self.subTest(plan_invalid=invalid), self.assertRaises(ValidationError):
                Plan.model_validate({**draft(), "target_language": invalid})

    @patch.object(p, "_request")
    def test_original_with_rewrite_disabled_preserves_local_direct_path(self, request):
        result = p.rewrite({}, "我明天三点再来。", [],
                           {"rewrite_enabled": False, "target_language": "original"})
        self.assertEqual(result["text"], "我明天三点再来。")
        self.assertEqual(result["source_text"], result["text"])
        self.assertEqual(result["target_language"], "original")
        self.assertTrue(result["audit"]["passed"])
        request.assert_not_called()

    @patch.object(p, "_request")
    def test_translation_without_style_still_has_independent_language_audit(self, request):
        source = "我明天三点再来。"
        history = [{"role": "user", "content": "history-only-sentinel"}]
        provider = {"type": "openai_llm", "model": "mock", "response_mode": "json_schema"}
        config = {"rewrite_enabled": False, "context_limit": 2, "persona": "persona-only-sentinel",
                  "prompt": "wording-only-sentinel", "speech_prompt": "speech-style-only-sentinel",
                  "style_strength": "strong"}
        for target, translation in TRANSLATIONS.items():
            with self.subTest(target=target):
                request.reset_mock()
                request.side_effect = [completion(draft(translation, "original")), audit()]
                pending = []
                result = p.rewrite(provider, source, history,
                                   {**config, "target_language": target, "_on_audit": pending.append})
                self.assertEqual(request.call_count, 2)
                self.assertEqual(result["target_language"], target)
                self.assertEqual(result["source_text"], source)
                self.assertEqual(result["text"], translation)
                self.assertTrue(result["audit"]["passed"])
                self.assertFalse(result["needs_review"])
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]["target_language"], target)
                self.assertFalse(pending[0]["audit"]["passed"])
                generation = request.call_args_list[0].kwargs["json"]
                auditor = request.call_args_list[1].kwargs["json"]
                system = generation["messages"][0]["content"]
                self.assertIn(LABELS[target], system)
                self.assertIn("仅翻译", system)
                self.assertIn("history-only-sentinel", json.dumps(generation))
                combined = json.dumps([generation, auditor], ensure_ascii=False)
                for sentinel in ("persona-only-sentinel", "wording-only-sentinel", "speech-style-only-sentinel"):
                    self.assertNotIn(sentinel, combined)
                self.assertEqual(len(auditor["messages"]), 2)
                self.assertNotIn("history-only-sentinel", json.dumps(auditor))
                checked = json.loads(auditor["messages"][1]["content"])
                self.assertEqual(checked["source_text"], source)
                self.assertEqual(checked["candidate"]["text"], translation)
                self.assertEqual(checked["rules"]["target_language"], target)
                self.assertFalse(checked["rules"]["rewrite_enabled"])
                self.assertEqual(checked["rules"]["style_strength"], "off")
                self.assertNotEqual(generation["response_format"]["json_schema"]["name"],
                                    auditor["response_format"]["json_schema"]["name"])

    @patch.object(p, "_request")
    def test_translated_style_keeps_persona_and_language_in_both_requests(self, request):
        for target, translation in TRANSLATIONS.items():
            for strength in ("balanced", "off"):
                with self.subTest(target=target, style_strength=strength):
                    request.reset_mock()
                    request.side_effect = [completion(draft(translation, "original")), audit()]
                    result = p.rewrite({"type": "openai_llm", "model": "mock"}, "我明天三点再来。", [],
                                       {"rewrite_enabled": True, "target_language": target, "style_strength": strength,
                                        "persona": "persona-enabled-sentinel", "prompt": "wording-enabled-sentinel"})
                    self.assertEqual(result["target_language"], target)
                    self.assertEqual(result["text"], translation)
                    self.assertEqual(request.call_count, 2)
                    for call in request.call_args_list:
                        body = json.dumps(call.kwargs["json"], ensure_ascii=False)
                        self.assertIn("persona-enabled-sentinel", body)
                        self.assertIn("wording-enabled-sentinel", body)
                        self.assertIn(LABELS[target], body)
                    checked = json.loads(request.call_args_list[1].kwargs["json"]["messages"][1]["content"])
                    self.assertEqual(checked["rules"]["style_strength"], strength)
                    if strength == "off":
                        self.assertIn("关闭角色措辞润色", checked["rules"]["style_constraint"])
                        self.assertIn("翻译", checked["rules"]["style_constraint"])

    def test_translation_requires_llm_configuration_when_style_is_disabled(self):
        config = Config().model_dump()
        config.update(rewrite_enabled=False, target_language="original")
        config["active"]["tts"] = "cloud-tts"
        tts = next(row for row in config["providers"] if row["id"] == "cloud-tts")
        tts.update(base_url="http://127.0.0.1:19991/v1", model="mock", voice="mock")
        llm = next(row for row in config["providers"] if row["id"] == "cloud-llm")
        llm.update(base_url="", model="", api_key="")
        validate_listening_config(config, None)
        config["target_language"] = "en"
        with self.assertRaisesRegex(ValueError, "服务地址"):
            validate_listening_config(config, None)
        llm["base_url"] = "https://example.invalid/v1"
        with self.assertRaisesRegex(ValueError, "模型名称"):
            validate_listening_config(config, None)
        llm["model"] = "mock"
        with self.assertRaisesRegex(ValueError, "API 密钥"):
            validate_listening_config(config, None)
        llm["base_url"] = "http://127.0.0.1:19992/v1"
        validate_listening_config(config, None)

    @patch.object(p, "_ensure")
    @patch.object(p, "_request")
    def test_gpt_routes_target_language_separately_from_reference_and_keeps_options(self, request, ensure):
        request.return_value = Mock(content=wav_bytes())
        provider = {"id": "test-gpt", "type": "gptsovits", "base_url": "http://127.0.0.1:19993",
                    "options": {"text_lang": "ja", "text_split_method": "cut5", "seed": 42}}
        saved = copy.deepcopy(provider)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = root / "reference.wav"
            ref.write_bytes(wav_bytes())
            reference = {"path": str(ref), "transcript": "这是中文参考。", "language": "zh"}
            for target in TRANSLATIONS:
                with self.subTest(target=target):
                    request.reset_mock()
                    p.synthesize(provider, draft(TRANSLATIONS[target], target), reference,
                                 root / (target + ".wav"), {"target_language": "ko"})
                    request.assert_called_once()
                    payload = request.call_args.kwargs["json"]
                    self.assertEqual(payload["text_lang"], "zh" if target == "zh_en" else target)
                    self.assertEqual(payload["prompt_lang"], "zh")
                    self.assertEqual(payload["prompt_text"], reference["transcript"])
                    self.assertEqual(payload["text"], TRANSLATIONS[target])
                    self.assertEqual(payload["text_split_method"], "cut5")
                    self.assertEqual(payload["seed"], 42)
                    self.assertEqual(provider, saved)
            # An original-language plan preserves the pre-existing GPT setting.
            p.synthesize(provider, draft("原文。", "original"), reference, root / "original.wav",
                         {"target_language": "en"})
            self.assertEqual(request.call_args.kwargs["json"]["text_lang"], "ja")
            provider["options"].pop("text_lang")
            p.synthesize(provider, draft("原文。", "original"), reference, root / "default.wav", {})
            self.assertEqual(request.call_args.kwargs["json"]["text_lang"], "zh")

    @patch.object(p, "_ensure")
    @patch.object(p, "_request")
    def test_drama_uses_translated_text_without_invented_language_fields(self, request, ensure):
        encoded = base64.b64encode(wav_bytes()).decode("ascii")
        request.return_value = Mock(json=lambda: {"audio": encoded, "custom_schema_output": {
            "schema": "drama3_generation_v1", "data": {"reached_limit": False}}})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ref = root / "ref.wav"
            ref.write_bytes(wav_bytes())
            p.synthesize({"type": "drama3_local", "model": "drama-3", "base_url": "http://127.0.0.1:17863"},
                         draft(TRANSLATIONS["ko"], "ko"),
                         {"path": str(ref), "transcript": "中文参考。", "language": "zh"}, root / "out.wav", {})
            payload = request.call_args.kwargs["json"]
            self.assertEqual(payload["request"]["text"], TRANSLATIONS["ko"])
            self.assertEqual(payload["request"]["reference_text"], "中文参考。")
            for key in ("target_language", "language", "text_lang", "prompt_lang"):
                self.assertNotIn(key, payload)
                self.assertNotIn(key, payload["request"])

    @patch.object(p, "_request")
    def test_unsupported_elevenlabs_cantonese_warns_and_synthesizes(self, request):
        provider = {"id": "test-eleven", "type": "elevenlabs", "model": "eleven_multilingual_v2",
                    "voice": "mock-voice", "base_url": "https://example.invalid/v1"}
        candidate = draft(TRANSLATIONS["yue"], "yue")
        original = copy.deepcopy(candidate)
        reviewed = p.review_for_provider(provider, candidate, {})
        self.assertFalse(reviewed["needs_review"])
        self.assertTrue(reviewed["audit"]["passed"])
        language_warnings = [warning for warning in reviewed["warnings"] if "粤语" in warning]
        self.assertTrue(language_warnings)
        self.assertEqual(candidate, original)
        supported = p.review_for_provider({**provider, "model": "eleven_v3"}, candidate, {})
        self.assertFalse(supported["needs_review"])
        self.assertTrue(supported["audit"]["passed"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "confirmed.wav"
            request.return_value = Mock(content=b"\x00\x00" * 2400)
            result = p.synthesize(provider, candidate, None, output, {})
            request.assert_called_once()
            self.assertEqual(request.call_args.kwargs["json"]["text"], candidate["text"])
            self.assertEqual(request.call_args.kwargs["json"]["model_id"], "eleven_multilingual_v2")
            for warning in language_warnings:
                self.assertIn(warning, result["warnings"])
            self.assertTrue(output.exists())

    def test_queued_jobs_freeze_language_while_global_selection_changes(self):
        providers = TrackingProviders()
        providers.block = True
        with application(providers) as (app, client, directory):
            self.save_config(client, target_language="en")
            first = self.submit(client, "第一条", preview=False)
            self.assertTrue(providers.entered.wait(2))
            second = self.submit(client, "第二条", preview=False)
            self.save_config(client, target_language="ja")
            third = self.submit(client, "第三条", preview=False)
            queued = {record["id"]: record for record in client.get("/api/state").json()["records"]}
            self.assertEqual([queued[row]["target_language"] for row in (first, second, third)], ["en", "en", "ja"])
            providers.hold.set()
            for record_id, target in ((first, "en"), (second, "en"), (third, "ja")):
                record = self.wait_record(app, record_id)
                self.assertEqual(record["status"], "done", record)
                self.assertEqual(record["target_language"], target)
                self.assertEqual(record["plan"]["target_language"], target)
            generation = [row[3]["target_language"] for row in providers.calls if row[0] == "llm"]
            self.assertEqual(generation, ["en", "en", "ja"])
            synthesis = [row for row in providers.calls if row[0] == "language_tts"]
            self.assertEqual([row[2] for row in synthesis], ["en", "en", "ja"])
            saved = json.loads((directory / "records.json").read_text(encoding="utf-8"))
            self.assertEqual([row["target_language"] for row in saved], ["en", "en", "ja"])

    def test_resynthesis_uses_record_language_plan_fallback_and_legacy_original(self):
        providers = TrackingProviders()
        with application(providers) as (app, client, directory):
            self.save_config(client, target_language="ja")
            variants = [("record", "en", "ko", "en"), ("plan", None, "yue", "yue"),
                        ("legacy", None, None, "original")]
            for record_id, saved_language, plan_language, expected in variants:
                old_plan = draft("已审核的稿件。", "original")
                if plan_language is None:
                    old_plan.pop("target_language")
                else:
                    old_plan["target_language"] = plan_language
                old = {"id": record_id, "created_at": "2026-01-01T00:00:00Z", "status": "review",
                       "source_text": "真实原文", "text": old_plan["text"], "plan": old_plan,
                       "audio_url": None, "error": None, "elapsed_ms": 0, "origin": "text"}
                if saved_language is not None:
                    old["target_language"] = saved_language
                app.state.store.add_record(old)
                edited = draft("用户确认后的稿件。", "ja")
                edited["source_text"] = "客户端错误原文"
                response = client.post(f"/api/records/{record_id}/synthesize", json={"plan": edited})
                self.assertEqual(response.status_code, 200, response.text)
                result = self.wait_record(app, response.json()["id"])
                self.assertEqual(result["status"], "done", result)
                self.assertEqual(result["parent_id"], record_id)
                self.assertEqual(result["target_language"], expected)
                self.assertEqual(result["plan"]["target_language"], expected)
                self.assertEqual(result["source_text"], "真实原文")
                self.assertEqual(result["plan"]["source_text"], "真实原文")
                self.assertEqual(result["text"], edited["text"])
                last_tts = [row for row in providers.calls if row[0] == "language_tts"][-1]
                self.assertEqual(last_tts[2], expected)
                self.assertTrue(last_tts[3])
            self.assertFalse(any(row[0] == "llm" for row in providers.calls))
            self.assertEqual(client.get("/api/config").json()["target_language"], "ja")

    @patch.object(p, "synthesize")
    @patch.object(p, "_request")
    def test_translation_rejected_by_auditor_holds_draft_and_suppresses_tts_and_caption(self, request, synthesize):
        request.side_effect = [completion(draft()), audit(False)]
        with application(p) as (app, client, directory):
            self.save_config(client, rewrite_enabled=False, target_language="en")
            record = self.wait_record(app, self.submit(client, "我明天三点再来。", preview=False))
            self.assertEqual(request.call_count, 2)
            self.assertEqual(record["status"], "review", record)
            self.assertEqual(record["source_text"], "我明天三点再来。")
            self.assertEqual(record["text"], TRANSLATIONS["en"])
            self.assertEqual(record["target_language"], "en")
            self.assertEqual(record["plan"]["target_language"], "en")
            self.assertFalse(record["plan"]["audit"]["passed"])
            self.assertIsNone(record["audio_url"])
            self.assertEqual(client.get("/api/subtitles/current").json()["text"], "")
            synthesize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
