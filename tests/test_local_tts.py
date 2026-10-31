"""Local model transport contracts, without loading GPU models."""
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from backend import providers as p


class LocalTtsTests(unittest.TestCase):
    @patch.object(p, "_ensure")
    @patch.object(p, "_request")
    def test_drama3_sends_local_reference_and_compiled_whisper(self, request, ensure):
        output = io.BytesIO()
        sf.write(output, np.ones(4410, dtype=np.float32) * .1, 44100, format="WAV")
        response = Mock()
        response.json.return_value = {"audio": base64.b64encode(output.getvalue()).decode("ascii"),
                                      "custom_schema_output": {"schema": "drama3_generation_v1",
                                                               "data": {"frames": 3, "reached_limit": False}}}
        references = []
        def inspect_reference(*args, **kwargs):
            path = Path(kwargs["json"]["request"]["voice_ref"])
            self.assertTrue(path.is_absolute())
            self.assertTrue(path.is_file())
            references.append(path)
            info = sf.info(path)
            self.assertEqual((info.samplerate, info.channels, info.subtype), (48000, 1, "PCM_16"))
            self.assertEqual(info.frames, 48000)
            return response
        request.side_effect = inspect_reference
        provider = {"id": "drama3-local", "type": "drama3_local", "model": "drama-3",
                    "base_url": "http://127.0.0.1:17863"}
        plan = {"source_text": "原话", "text": "我马上回来。", "segments": [
            {"kind": "event", "event": "sigh"},
            {"kind": "speech", "text": "我马上回来。", "emotion": "sad", "intensity": .5,
             "delivery": "whisper", "speed": 1}], "warnings": []}
        original = json.dumps(plan)
        with tempfile.TemporaryDirectory() as directory:
            ref = Path(directory) / "reference.wav"
            sf.write(ref, np.ones((48000, 2), dtype=np.float32) * .1, 48000)
            result = p.synthesize(provider, plan, {"path": str(ref), "transcript": "参考文字。"},
                                  Path(directory) / "out.wav", {})
            self.assertTrue(Path(result["path"]).exists())
        self.assertEqual(json.dumps(plan), original)
        ensure.assert_called_once()
        args, kwargs = request.call_args
        self.assertEqual(args[1:], ("POST", "/v1/tasks/run"))
        self.assertEqual(kwargs["timeout"], (5, 600))
        self.assertEqual(kwargs["json"]["model"], "drama-3")
        body = kwargs["json"]["request"]
        self.assertEqual(body["text"], "[sighs] [sad] <whisper>我马上回来。</whisper>")
        self.assertEqual(body["reference_text"], "参考文字。")
        self.assertEqual((body["max_tokens"], body["seed"]), (1024, 42))
        self.assertNotIn("speed", body)
        self.assertEqual(len(references), 1)
        self.assertFalse(references[0].exists())
        self.assertFalse(references[0].parent.exists())

    @patch.object(p, "_ensure")
    @patch.object(p, "_request")
    def test_local_drama_truncated_or_unverified_audio_is_not_saved(self, request, ensure):
        output = io.BytesIO()
        sf.write(output, np.ones(4410, dtype=np.float32) * .1, 44100, format="WAV")
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        invalid_metadata = [
            {"schema": "drama3_generation_v1", "data": {"reached_limit": True}},
            {"schema": "drama3_generation_v1", "data": {"reached_limit": "false"}},
            {"schema": "drama3_generation_v1", "data": {}},
            {"schema": "other_model", "data": {"reached_limit": False}},
            None,
        ]
        candidate = {"text": "完整的一句话。", "segments": [{"kind": "speech", "text": "完整的一句话。"}]}
        with tempfile.TemporaryDirectory() as directory:
            ref = Path(directory) / "ref.wav"
            sf.write(ref, np.ones(16000, dtype=np.float32) * .1, 16000)
            for index, metadata in enumerate(invalid_metadata):
                with self.subTest(metadata=metadata):
                    response = Mock()
                    response.json.return_value = {"audio": encoded, "custom_schema_output": metadata}
                    request.return_value = response
                    target = Path(directory) / f"rejected-{index}.wav"
                    with self.assertRaises(p.ProviderError):
                        p.synthesize({"type": "drama3_local"}, candidate,
                                     {"path": str(ref), "transcript": "参考。"}, target, {})
                    self.assertFalse(target.exists())
                    temporary = Path(request.call_args.kwargs["json"]["request"]["voice_ref"])
                    self.assertFalse(temporary.parent.exists())

    @patch.object(p, "_request")
    def test_local_drama_invalid_audio_and_request_failure_clean_reference(self, request):
        provider = {"type": "drama3_local"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ref.wav"
            sf.write(path, np.ones(16000, dtype=np.float32) * .1, 16000)
            reference = {"path": str(path), "transcript": "参考。"}
            response = Mock()
            response.json.return_value = {"audio": "not-valid-base64!",
                                          "custom_schema_output": {"schema": "drama3_generation_v1",
                                                                   "data": {"reached_limit": False}}}
            request.return_value = response
            with self.assertRaisesRegex(p.ProviderError, "无效音频编码"):
                p._speech(provider, "你好。", "neutral", 0, reference, None)
            temporary = Path(request.call_args.kwargs["json"]["request"]["voice_ref"])
            self.assertFalse(temporary.parent.exists())
            request.side_effect = InterruptedError("cancelled")
            with self.assertRaises(InterruptedError):
                p._speech(provider, "你好。", "neutral", 0, reference, None)
            temporary = Path(request.call_args.kwargs["json"]["request"]["voice_ref"])
            self.assertFalse(temporary.parent.exists())

    @patch.object(p, "_request")
    def test_local_drama_preserved_reference_still_obeys_five_mib_limit(self, request):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ref.wav"
            sf.write(path, np.zeros(3 * 1024 * 1024, dtype=np.float32), 48000)
            with self.assertRaisesRegex(p.ProviderError, "参考音频过大"):
                p._speech({"type": "drama3_local"}, "你好。", "neutral", 0,
                          {"path": str(path), "transcript": "参考。"}, None)
        request.assert_not_called()

    def test_local_drama_capabilities_adapt_unsupported_speed(self):
        provider = {"type": "drama3_local", "model": "drama-3"}
        caps = p.capabilities(provider)
        self.assertTrue(caps["whisper"])
        self.assertTrue(caps["reference_audio"])
        self.assertFalse(caps["speed"])
        self.assertEqual(caps["events"], ["sigh", "laugh"])
        self.assertEqual(caps["rule_profile"], "fish_drama3_local")
        plan = {"source_text": "原话", "text": "你好。", "segments": [
            {"kind": "speech", "text": "你好。", "speed": 1.2}], "warnings": []}
        checked = p.review_for_provider(provider, plan, {})
        self.assertFalse(checked["needs_review"])
        self.assertTrue(checked["audit"]["passed"])
        self.assertTrue(any("默认语速" in w for w in checked["warnings"]))

    @patch.object(p, "_request")
    def test_local_drama_rejects_remote_reference_transfer(self, request):
        with self.assertRaises(p.ProviderError):
            p._speech({"type": "drama3_local", "base_url": "https://example.com"},
                      "你好", "neutral", 0, None, None)
        request.assert_not_called()

    @patch.object(p, "_request")
    def test_v5_variant_switch_uses_matching_weights_and_sampling(self, request):
        output = io.BytesIO()
        sf.write(output, np.ones(4800, dtype=np.float32) * .1, 48000, format="WAV")
        for variant, steps, cfg in [("v5turbo", 4, 0.0), ("v5dev", 32, 1.3)]:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "ref.wav"
                sf.write(path, np.ones(16000, dtype=np.float32) * .1, 16000)
                health = Mock()
                health.json.return_value = {"version": "v5dev" if variant == "v5turbo" else "v5turbo"}
                request.reset_mock()
                request.side_effect = [health, Mock(), Mock(content=output.getvalue())]
                audio, rate = p._speech({"type": "gptsovits", "model": variant,
                                        "base_url": "http://127.0.0.1:17864"},
                                       "测试台词。", "neutral", 0,
                                       {"path": str(path), "transcript": "参考台词。"}, None)
                self.assertEqual(rate, 48000)
                self.assertEqual(len(audio), 4800)
                switch = request.call_args_list[1]
                self.assertEqual(switch.args[1:], ("GET", "/set_sovits_weights"))
                self.assertIn("s2G" + variant + ".pth", switch.kwargs["params"]["weights_path"])
                payload = request.call_args_list[2].kwargs["json"]
                self.assertEqual(payload["sample_steps"], steps)
                self.assertEqual(payload["cfg_rate"], cfg)
                self.assertEqual(payload["ref_audio_path"], str(path.resolve()))

    @patch.object(p, "_request")
    def test_v5_unknown_variant_does_not_silently_fall_back(self, request):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ref.wav"
            sf.write(path, np.ones(16000, dtype=np.float32) * .1, 16000)
            with self.assertRaises(p.ProviderError):
                p._speech({"type": "gptsovits", "model": "v5pro", "base_url": "http://127.0.0.1:17864"},
                          "你好", "neutral", 0, {"path": str(path), "transcript": "参考台词"}, None)
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
