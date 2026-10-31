import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import requests
import soundfile as sf

from backend import providers as p


def speech(text="我想再试一次。", emotion="neutral"):
    return {"kind": "speech", "text": text, "emotion": emotion, "intensity": 0.5}


def plan(segments=None):
    segments = segments or [speech()]
    return {"source_text": "原文", "text": "".join(x["text"] for x in segments if x["kind"] == "speech"),
            "segments": segments, "warnings": []}


def json_response(value):
    response = Mock()
    response.json.return_value = value
    return response


def audit_response(passed=True, uncertain=False):
    value = {"passed": passed, "uncertain": uncertain, "message": "审核完成", "leaked_instructions": []}
    return json_response({"choices": [{"message": {"content": json.dumps(value)}}]})


class ProviderTests(unittest.TestCase):
    def test_plan_rejects_invalid_structure(self):
        for segments in ([speech(emotion="madeup")], [{"kind": "event", "event": "boom"}],
                         [speech(), {"kind": "pause", "duration_ms": True}],
                         [speech(), {"kind": "pause", "duration_ms": 2.5}],
                         [dict(speech(), intensity=float("nan"))]):
            with self.subTest(segments=segments), self.assertRaises(p.ProviderError):
                p.validate_plan(plan(segments))
        bad = plan()
        bad["text"] = "另一个句子"
        with self.assertRaises(p.ProviderError):
            p.validate_plan(bad)

    @patch.object(p, "_request")
    def test_bypass_never_calls_cloud(self, request):
        result = p.rewrite({}, "我今天有点累。", [], {"rewrite_enabled": False})
        self.assertEqual(result["text"], "我今天有点累。")
        self.assertTrue(result["warnings"])
        request.assert_not_called()

    @patch.object(p, "_request")
    def test_llm_context_zero_and_persona_sections(self, request):
        request.side_effect = [json_response({"choices": [{"message": {"content": json.dumps(plan(), ensure_ascii=False)}}]}), audit_response()]
        provider = {"type": "openai_llm", "model": "model", "response_mode": "json_schema"}
        config = {"rewrite_enabled": True, "context_limit": 0, "persona": "沉稳的她", "prompt": "轻微润色", "speech_prompt": "少量叹气"}
        result = p.rewrite(provider, "原话", [{"role": "user", "content": "旧话"}], config)
        body = request.call_args_list[0].kwargs["json"]
        self.assertEqual(len(body["messages"]), 2)
        self.assertIn("沉稳的她", body["messages"][0]["content"])
        self.assertIn("少量叹气", body["messages"][0]["content"])
        self.assertIn("不代替听者", body["messages"][0]["content"])
        self.assertEqual(result["source_text"], "原话")
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertEqual(request.call_count, 2)
        self.assertTrue(result["audit"]["passed"])
        self.assertFalse(result["needs_review"])
        auditor = request.call_args_list[1].kwargs["json"]
        self.assertEqual(len(auditor["messages"]), 2)
        self.assertIn("独立", auditor["messages"][0]["content"])

    @patch.object(p, "_request")
    def test_llm_receives_active_tts_capabilities_without_credentials(self, request):
        request.side_effect = [json_response({"choices": [{"message": {"content": json.dumps(plan())}}]}), audit_response()]
        config = {"rewrite_enabled": True, "context_limit": 0, "persona": "沉稳的她",
                  "prompt": "只轻微调整措辞", "speech_prompt": "自然地表演", "active": {"tts": "local-gpt"},
                  "providers": [
                      {"id": "unused", "kind": "tts", "type": "elevenlabs", "model": "eleven_v3"},
                      {"id": "local-gpt", "kind": "tts", "type": "gptsovits", "model": "local",
                       "api_key": "never-put-this-in-prompt", "base_url": "http://127.0.0.1:9880"}]}
        p.rewrite({"type": "openai_llm", "model": "m"}, "原话", [], config)
        generation = request.call_args_list[0].kwargs["json"]
        system = generation["messages"][0]["content"]
        for label in ["录音人希望呈现的角色性格", "微重构程度与措辞规则", "语音表演、情绪与声音动作规则"]:
            self.assertIn(label, system)
        for value in ["沉稳的她", "只轻微调整措辞", "自然地表演", '"emotions": []', '"events": []', '"whisper": false']:
            self.assertIn(value, system)
        self.assertIn("默认编排只使用该引擎支持的能力", system)
        self.assertNotIn("never-put-this-in-prompt", system)
        self.assertNotIn("eleven_v3", system)
        auditor_input = json.loads(request.call_args_list[1].kwargs["json"]["messages"][1]["content"])
        self.assertEqual(auditor_input["selected_tts"]["type"], "gptsovits")
        self.assertFalse(auditor_input["selected_tts"]["capabilities"]["whisper"])
        self.assertNotIn("api_key", json.dumps(auditor_input))

    def test_rule_profiles_follow_model_and_isolate_custom_rules(self):
        provider = {"type": "fish_tts", "model": " drama-3 ",
                    "performance_rules": {"drama-3": "只在告别时轻声。", "s2-pro": "保持平静。"}}
        caps = p.capabilities(provider)
        self.assertEqual(caps["rule_profile"], "fish_drama3")
        self.assertEqual(caps["rule_key"], "drama-3")
        self.assertEqual(caps["effective_rules"], "只在告别时轻声。")
        self.assertIn("<whisper>", " ".join(caps["syntax_examples"]))
        self.assertTrue(caps["rules_customized"])
        provider["model"] = "s2-pro"
        self.assertEqual(p.capabilities(provider)["rule_profile"], "fish_s2")
        self.assertEqual(p.capabilities(provider)["effective_rules"], "保持平静。")
        provider["model"] = "s2.1-pro"
        changed = p.capabilities(provider)
        self.assertFalse(changed["rules_customized"])
        self.assertEqual(changed["effective_rules"], changed["default_rules"])
        for kind, model, expected in [("breeze", "", "breeze_instructions"),
                ("openai_tts", "gpt-4o-mini-tts", "openai_instructions"),
                ("openai_tts", "tts-1", "plain"), ("elevenlabs", "eleven_v3", "eleven_audio_tags"),
                ("elevenlabs", "eleven_multilingual_v2", "plain"), ("gptsovits", "", "plain")]:
            self.assertEqual(p.capabilities({"type": kind, "model": model})["rule_profile"], expected)
        empty = p.capabilities({"type": "breeze", "performance_rules": {"__default__": "更轻柔。"}})
        self.assertEqual(empty["effective_rules"], "更轻柔。")

    @patch.object(p, "_request")
    def test_model_custom_rules_and_style_strength_reach_both_llm_passes(self, request):
        custom = "自定义规则：只在告别时使用耳语，其他句子保持自然。"
        fish = {"id": "fish", "kind": "tts", "type": "fish_tts", "model": "drama-3",
                "api_key": "fish-secret-not-for-prompt", "performance_rules": {"drama-3": custom, "s2-pro": "别的模型规则"}}
        config = {"rewrite_enabled": True, "context_limit": 0, "active": {"tts": "fish"},
                  "providers": [fish], "persona": "沉稳的她", "prompt": "保留事实", "speech_prompt": "少量停顿"}
        for strength in ("off", "light", "balanced", "strong"):
            with self.subTest(strength=strength):
                request.reset_mock()
                request.side_effect = [json_response({"choices": [{"message": {"content": json.dumps(plan())}}]}), audit_response()]
                config["style_strength"] = strength
                p.rewrite({"type": "openai_llm", "model": "m"}, "原话", [], config)
                system = request.call_args_list[0].kwargs["json"]["messages"][0]["content"]
                auditor = json.loads(request.call_args_list[1].kwargs["json"]["messages"][1]["content"])
                self.assertIn(custom, system)
                self.assertNotIn(p.capabilities(fish)["default_rules"], system)
                self.assertIn(p.performance.STYLE_RULES[strength], system)
                self.assertNotIn("fish-secret-not-for-prompt", system)
                self.assertNotIn("别的模型规则", system)
                self.assertEqual(auditor["selected_tts"]["capabilities"]["effective_rules"], custom)
                self.assertEqual(auditor["rules"]["style_strength"], strength)
                self.assertEqual(auditor["rules"]["performance"], "少量停顿")

    @patch.object(p, "_request")
    def test_llm_model_tags_in_dialogue_hold_even_when_auditor_passes(self, request):
        candidate = plan([speech("<whisper>我马上回来。</whisper>")])
        request.side_effect = [json_response({"choices": [{"message": {"content": json.dumps(candidate)}}]}), audit_response()]
        result = p.rewrite({"type": "openai_llm", "model": "m"}, "我马上回来。", [], {"rewrite_enabled": True})
        self.assertTrue(result["needs_review"])
        self.assertFalse(result["audit"]["passed"])

    @patch.object(p.requests, "Session")
    def test_fish_drama3_compiles_and_sends_real_api_shape_without_changing_plan(self, session):
        buffer = io.BytesIO()
        sf.write(buffer, np.ones(240, dtype=np.float32) * 0.1, 24000, format="WAV")
        response = Mock(status_code=200, content=buffer.getvalue())
        request = session.return_value.__enter__.return_value.request
        request.return_value = response
        provider = {"id": "fish", "type": "fish_tts", "model": "drama-3", "api_key": "test-key", "voice": "fallback"}
        candidate = plan([{"kind": "event", "event": "sigh"},
                          dict(speech("我会回来的。", "sad"), delivery="whisper", speed=0.8),
                          speech("我们明天见。")])
        candidate["directives"] = ["这段用耳语"]
        before = json.dumps(candidate, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as directory:
            result = p.synthesize(provider, candidate, {"provider_voice_ids": {"fish": "selected-voice"}},
                                  Path(directory) / "fish.wav", {})
            self.assertTrue(Path(result["path"]).is_file())
        self.assertEqual(json.dumps(candidate, ensure_ascii=False), before)
        self.assertEqual(request.call_count, 2)
        first = request.call_args_list[0]
        self.assertEqual(first.args, ("POST", "https://api.fish.audio/v1/tts"))
        self.assertEqual(first.kwargs["headers"], {"model": "drama-3", "Authorization": "Bearer test-key"})
        body = first.kwargs["json"]
        self.assertEqual(body["reference_id"], "selected-voice")
        self.assertEqual(body["text"], "[sighs] [sad] <whisper>我会回来的。</whisper>")
        self.assertEqual(body["prosody"]["speed"], 0.8)
        self.assertEqual(body["format"], "wav")
        self.assertEqual(body["sample_rate"], 24000)
        self.assertNotIn("这段用耳语", body["text"])
        self.assertEqual(request.call_args_list[1].kwargs["json"]["text"], "我们明天见。")

    @patch.object(p, "_speech")
    def test_fish_s2_uses_own_tags_and_unsupported_breath_holds(self, synth):
        synth.return_value = (np.ones(240, dtype=np.float32) * 0.1, 24000)
        provider = {"type": "fish_tts", "model": "s2.1-pro"}
        candidate = plan([{"kind": "event", "event": "sigh"}, dict(speech(), delivery="whisper")])
        with tempfile.TemporaryDirectory() as directory:
            p.synthesize(provider, candidate, None, Path(directory) / "s2.wav", {})
            self.assertEqual(synth.call_args.args[1], "[sighing] [whispering] 我想再试一次。")
            synth.reset_mock()
            with self.assertRaises(p.ProviderReviewRequired):
                p.synthesize(provider, plan([{"kind": "event", "event": "breath"}]), None, Path(directory) / "breath.wav", {})
            synth.assert_not_called()

    @patch.object(p, "_request")
    def test_fish_unknown_model_missing_key_or_voice_prevent_generation(self, request):
        base = {"type": "fish_tts", "model": "drama-3", "api_key": "test-key", "voice": "v"}
        for changes, voice in [({"model": "drama3-typo"}, "v"), ({"api_key": ""}, "v"), ({}, None)]:
            with self.subTest(changes=changes), self.assertRaises(p.ProviderError):
                p._speech({**base, **changes}, "你好", "neutral", 0, None, voice)
        request.assert_not_called()

    @patch.object(p.requests, "Session")
    def test_fish_connection_check_is_read_only_list_models(self, session):
        request = session.return_value.__enter__.return_value.request
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = {"items": [], "total": 0}
        result = p.test_provider({"type": "fish_tts", "model": "drama-3", "api_key": "test-key", "base_url": "https://api.fish.audio/v1"})
        self.assertTrue(result["ok"])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args, ("GET", "https://api.fish.audio/model"))
        self.assertEqual(request.call_args.kwargs["params"], {"page_size": 1})
        self.assertNotIn("json", request.call_args.kwargs)

    @patch.object(p, "_request")
    def test_llm_context_limit_and_invalid_json(self, request):
        request.side_effect = [json_response({"choices": [{"message": {"content": json.dumps(plan())}}]}), audit_response()]
        provider = {"type": "openai_llm", "model": "m"}
        history = [{"role": "user", "content": str(i)} for i in range(4)]
        p.rewrite(provider, "原话", history, {"rewrite_enabled": True, "context_limit": 2})
        self.assertEqual([m["content"] for m in request.call_args_list[0].kwargs["json"]["messages"][1:-1]], ["2", "3"])
        request.side_effect = None
        request.return_value = json_response({"choices": [{"message": {"content": "我想再试一次。"}}]})
        with self.assertRaisesRegex(p.ProviderError, "有效 JSON"):
            p.rewrite(provider, "原话", [], {"rewrite_enabled": True, "context_limit": 0})

    @patch.object(p, "_request")
    def test_llm_event_policy_and_intensity(self, request):
        result = plan([speech(emotion="sad"), {"kind": "event", "event": "sigh"}])
        request.side_effect = [json_response({"choices": [{"message": {"content": json.dumps(result)}}]}), audit_response()]
        actual = p.rewrite({"type": "openai_llm", "model": "m"}, "原话", [],
                           {"rewrite_enabled": True, "context_limit": 0, "allow_events": False, "emotion_intensity": 0.1})
        self.assertEqual(len(actual["segments"]), 1)
        self.assertEqual(actual["segments"][0]["intensity"], 0.1)
        self.assertTrue(actual["warnings"])

    @patch.object(p, "_speech")
    def test_exact_pause_and_unsupported_events(self, synth):
        synth.return_value = (np.full(240, 0.1, dtype=np.float32), 24000)
        segments = [speech("你好。"), {"kind": "pause", "duration_ms": 321}, {"kind": "event", "event": "sigh"}, speech("再见。")]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.wav"
            result = p.synthesize({"type": "gptsovits"}, plan(segments), None, output, {})
            audio, rate = sf.read(output)
            self.assertEqual(len(audio), 480 + round(0.321 * 24000))
            self.assertTrue(np.all(audio[240:-240] == 0))
            self.assertEqual(rate, 24000)
            self.assertTrue(any("sigh" in x for x in result["warnings"]))
            self.assertEqual(synth.call_count, 2)

    @patch.object(p, "_speech")
    def test_eleven_compiles_events_with_adjacent_speech(self, synth):
        synth.return_value = (np.ones(100, dtype=np.float32) * 0.1, 24000)
        with tempfile.TemporaryDirectory() as directory:
            p.synthesize({"type": "elevenlabs", "model": "eleven_v3"},
                         plan([{"kind": "event", "event": "sigh"}, speech(emotion="sad")]),
                         None, Path(directory) / "result.wav", {})
        self.assertIn("[sighs]", synth.call_args.args[1])
        self.assertIn("[sad]", synth.call_args.args[1])
        self.assertEqual(synth.call_count, 1)

    @patch.object(p, "_speech")
    def test_cancel_between_segments_prevents_next_request(self, synth):
        synth.return_value = (np.ones(100, dtype=np.float32) * 0.1, 24000)
        def cancel_check():
            if synth.call_count >= 1:
                raise InterruptedError("cancelled")
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(InterruptedError):
            p.synthesize({"type": "openai_tts"}, plan([speech("甲"), speech("乙")]), None,
                         Path(directory) / "result.wav", {"_cancel_check": cancel_check})
        self.assertEqual(synth.call_count, 1)

    @patch.object(p, "_request")
    def test_audit_failure_or_uncertainty_holds_draft(self, request):
        for second in [audit_response(False), audit_response(True, True), p.ProviderError("timeout")]:
            request.side_effect = [json_response({"choices": [{"message": {"content": json.dumps(plan())}}]}), second]
            result = p.rewrite({"type": "openai_llm", "model": "m"}, "原话", [], {"rewrite_enabled": True, "context_limit": 0})
            self.assertTrue(result["needs_review"])
            self.assertFalse(result["audit"]["passed"])
            self.assertEqual(result["text"], "我想再试一次。")

    @patch.object(p, "_request")
    def test_local_audit_catches_leaked_control_despite_remote_pass(self, request):
        candidate = plan([speech("这段语速快一点，我马上回来。")])
        request.side_effect = [json_response({"choices": [{"message": {"content": json.dumps(candidate)}}]}), audit_response()]
        result = p.rewrite({"type": "openai_llm", "model": "m"}, candidate["text"], [], {"rewrite_enabled": True, "context_limit": 0})
        self.assertTrue(result["needs_review"])
        self.assertIn("操作指令", result["audit"]["message"])

    @patch.object(p, "_request")
    def test_control_only_plan_valid_and_not_spoken(self, request):
        candidate = {"source_text": "这段用耳语", "text": "", "segments": [], "warnings": [],
                     "directives": ["这段用耳语"], "needs_review": False, "audit": {"passed": False, "message": "待审"}}
        request.side_effect = [json_response({"choices": [{"message": {"content": json.dumps(candidate)}}]}), audit_response()]
        result = p.rewrite({"type": "openai_llm", "model": "m"}, "这段用耳语", [], {"rewrite_enabled": True, "context_limit": 0})
        self.assertFalse(result["needs_review"])
        self.assertEqual(result["segments"], [])
        with self.assertRaises(p.ProviderReviewRequired):
            p.synthesize({"type": "breeze"}, result, None, Path("unused.wav"), {})

    @patch.object(p, "_speech")
    def test_event_only_eleven_generates_and_breeze_holds(self, synth):
        synth.return_value = (np.ones(2400, dtype=np.float32) * 0.1, 24000)
        candidate = {"source_text": "叹气一下", "text": "", "segments": [{"kind": "event", "event": "sigh"}], "warnings": []}
        with tempfile.TemporaryDirectory() as directory:
            result = p.synthesize({"type": "elevenlabs", "model": "eleven_v3"}, candidate, None,
                                  Path(directory) / "sigh.wav", {})
            self.assertTrue(Path(result["path"]).is_file())
            self.assertEqual(synth.call_args.args[1], "[sighs]")
            with self.assertRaises(p.ProviderReviewRequired) as error:
                p.synthesize({"type": "breeze"}, candidate, None, Path(directory) / "unused.wav", {})
            self.assertTrue(error.exception.plan["needs_review"])

    @patch.object(p, "_request")
    @patch.object(p, "_ensure")
    def test_local_asr_rejects_audio_over_sixty_seconds_before_start(self, ensure, request):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "long.wav"
            sf.write(source, np.zeros(16000 * 61), 16000)
            with self.assertRaisesRegex(p.ProviderError, "60 秒"):
                p.transcribe({"type": "qwen_local"}, source)
        ensure.assert_not_called()
        request.assert_not_called()

    def test_bypass_holds_control_instruction(self):
        result = p.rewrite({}, "这段用耳语，我马上回来。", [], {"rewrite_enabled": False})
        self.assertTrue(result["needs_review"])

    def test_model_specific_capabilities(self):
        self.assertFalse(p.capabilities({"type": "openai_tts", "model": "tts-1"})["whisper"])
        self.assertFalse(p.capabilities({"type": "elevenlabs", "model": "eleven_multilingual_v2"})["events"])
        self.assertIn("sigh", p.capabilities({"type": "elevenlabs", "model": "eleven_v3"})["events"])
        self.assertTrue(p.capabilities({"type": "breeze"})["reference_audio"])
        candidate = plan([dict(speech(), delivery="whisper")])
        checked = p.review_for_provider({"type": "gptsovits"}, candidate, {})
        self.assertFalse(checked["needs_review"])
        self.assertTrue(any("耳语" in w for w in checked["warnings"]))

    @patch.object(p, "_request")
    def test_openai_legacy_never_sends_instructions(self, request):
        buffer = io.BytesIO()
        sf.write(buffer, np.zeros(100), 24000, format="WAV")
        request.return_value = Mock(content=buffer.getvalue())
        p._speech({"type": "openai_tts", "model": "tts-1", "voice": "alloy"}, "你好", "sad", 0.5, None, "alloy")
        self.assertNotIn("instructions", request.call_args.kwargs["json"])

    @patch.object(p, "_request")
    def test_breeze_whisper_and_speed_serialization(self, request):
        request.return_value = Mock(content=b"\x00\x00" * 100, headers={"X-Sample-Rate": "24000"})
        p._speech({"type": "breeze"}, "你好", "neutral", 0.0, None, "v", delivery="whisper", speed=0.8)
        instruction = request.call_args.kwargs["data"]["instruction"]
        self.assertIn("耳语", instruction)
        self.assertIn("稍慢", instruction)

    @patch.object(p, "_breeze_voice")
    @patch.object(p, "_ensure")
    def test_cancel_during_model_start_prevents_voice_registration(self, ensure, register):
        stopped = [False]
        def cancel_check():
            if stopped[0]:
                raise InterruptedError("cancelled")
        ensure.side_effect = lambda *args, **kwargs: stopped.__setitem__(0, True)
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            sf.write(reference, np.zeros(1600), 16000)
            with self.assertRaises(InterruptedError):
                p.synthesize({"type": "breeze"}, plan(), {"path": str(reference), "transcript": "你好"},
                             Path(directory) / "result.wav", {"_cancel_check": cancel_check})
        register.assert_not_called()

    @patch("requests.Session.request")
    @patch.object(p, "_ensure")
    def test_cancel_during_qwen_start_prevents_asr_request(self, ensure, request):
        stopped = [False]
        def cancel_check():
            if stopped[0]:
                raise InterruptedError("cancelled")
        ensure.side_effect = lambda *args, **kwargs: stopped.__setitem__(0, True)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            sf.write(source, np.zeros(1600), 16000)
            with self.assertRaises(InterruptedError):
                p.transcribe({"type": "qwen_local", "_cancel_check": cancel_check}, source)
        request.assert_not_called()

    @patch.object(p, "_speech")
    @patch.object(p, "_clean_text")
    def test_cancel_before_final_tagged_flush_prevents_cloud_request(self, clean, synth):
        stopped = [False]
        def mark_stopped(text, warnings):
            stopped[0] = True
            return text
        def cancel_check():
            if stopped[0]:
                raise InterruptedError("cancelled")
        clean.side_effect = mark_stopped
        with self.assertRaises(InterruptedError):
            p.synthesize({"type": "elevenlabs", "model": "eleven_v3"}, plan(), None,
                         Path("unused.wav"), {"_cancel_check": cancel_check})
        synth.assert_not_called()

    @patch.object(p, "_request")
    def test_cancel_after_generation_prevents_audit_request(self, request):
        stopped = [False]
        def first_request(*args, **kwargs):
            stopped[0] = True
            return json_response({"choices": [{"message": {"content": json.dumps(plan())}}]})
        def cancel_check():
            if stopped[0]:
                raise InterruptedError("cancelled")
        request.side_effect = first_request
        with self.assertRaises(InterruptedError):
            p.rewrite({"type": "openai_llm", "model": "m"}, "原话", [],
                      {"rewrite_enabled": True, "context_limit": 0, "_cancel_check": cancel_check})
        self.assertEqual(request.call_count, 1)

    @patch.object(p, "_request")
    @patch.object(p, "_ensure")
    def test_qwen_serializes_audio_without_server_file_access(self, ensure, request):
        request.return_value = json_response({"choices": [{"message": {"content": "language Chinese<asr_text>你好。"}}]})
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "录音.wav"
            sf.write(source, np.zeros(1600), 16000)
            result = p.transcribe({"type": "qwen_local"}, source)
        payload = request.call_args.kwargs["json"]
        audio = payload["messages"][0]["content"][0]["input_audio"]
        self.assertIn("data", audio)
        self.assertNotIn("url", audio)
        self.assertEqual(result, "你好。")

    @patch.object(p, "_request")
    def test_breeze_pcm_decoding(self, request):
        response = Mock(content=np.array([0, 16384, -16384], dtype="<i2").tobytes(), headers={"X-Sample-Rate": "24000"})
        request.return_value = response
        audio, rate = p._speech({"type": "breeze"}, "你好", "happy", 0.5, None, "voice-cache")
        self.assertEqual(rate, 24000)
        np.testing.assert_allclose(audio, [0, 0.5, -0.5])
        self.assertEqual(request.call_args.kwargs["data"]["voice_id"], "voice-cache")

    def test_bad_audio_payload_rejected(self):
        with self.assertRaises(p.ProviderError):
            p._decode_audio(Mock(content=b'{"error":"failed"}'))
        with self.assertRaises(p.ProviderError):
            p._decode_audio(Mock(content=b"123"), raw_rate=24000)

    @patch("requests.Session.request")
    def test_network_errors_and_http_bodies_do_not_expose_secret(self, request):
        provider = {"type": "openai_llm", "base_url": "https://example.test/v1", "api_key": "private-test-key"}
        request.return_value = Mock(status_code=401, text="private-test-key")
        with self.assertRaises(p.ProviderError) as error:
            p._request(provider, "GET", "/v1/models")
        self.assertNotIn("private-test-key", str(error.exception))
        request.side_effect = requests.ConnectionError("private-test-key")
        with self.assertRaises(p.ProviderError) as error:
            p._request(provider, "GET", "/v1/models")
        self.assertNotIn("private-test-key", str(error.exception))

    @patch.object(p.local_runtime, "available", return_value=True)
    @patch.object(p.local_runtime, "healthy", return_value=False)
    @patch.object(p.local_runtime, "ensure")
    def test_health_check_does_not_start_model(self, ensure, healthy, available):
        result = p.test_provider({"type": "qwen_local"})
        self.assertTrue(result["ok"])
        ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
