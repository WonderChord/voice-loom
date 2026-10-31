"""Self-hosted TTS on a remote (cloud) instance: preset voices first, inline reference as fallback."""
import base64
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from backend import local_runtime, pipeline, providers as p

PLAN = {"source_text": "原话", "text": "我马上回来。", "segments": [
    {"kind": "speech", "text": "我马上回来。", "emotion": "neutral", "intensity": 0, "delivery": "normal", "speed": 1}],
    "warnings": []}


def wav_bytes(seconds=0.1, rate=24000):
    output = io.BytesIO()
    sf.write(output, np.ones(int(rate * seconds), dtype=np.float32) * .1, rate, format="WAV")
    return output.getvalue()


def audio_response(json_body=None):
    response = Mock()
    response.content = wav_bytes()
    response.headers = {"Content-Type": "audio/wav"}
    response.json.return_value = json_body
    return response


class CloudTtsTests(unittest.TestCase):
    def setUp(self):
        p._breeze_voice_ids.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.ref = Path(self.directory.name) / "ref.wav"
        sf.write(self.ref, np.ones(16000, dtype=np.float32) * .1, 16000)

    def tearDown(self):
        self.directory.cleanup()

    def out(self, name="out.wav"):
        return Path(self.directory.name) / name

    def test_remote_urls_are_not_managed_as_local_runtimes(self):
        self.assertIsNone(local_runtime.runtime_name("breeze", "https://tts.example.test"))
        self.assertIsNone(local_runtime.runtime_name("drama3_local", "https://tts.example.test"))
        self.assertEqual(local_runtime.runtime_name("breeze", "http://127.0.0.1:7871"), "breeze")

    @patch.object(p, "_request")
    def test_breeze_preset_voice_skips_upload_and_reference_file(self, request):
        request.return_value = audio_response()
        provider = {"id": "breeze-cloud", "type": "breeze", "base_url": "https://tts.example.test", "api_key": "k"}
        reference = {"path": "", "transcript": "", "provider_voice_ids": {"breeze-cloud": "raven"}}
        p.synthesize(provider, PLAN, reference, self.out(), {})
        paths = [call.args[2] for call in request.call_args_list]
        self.assertEqual(paths, ["/v1/audio/speech"])
        self.assertEqual(request.call_args.kwargs["data"]["voice_id"], "raven")

    @patch.object(p, "_request")
    def test_breeze_uploaded_voice_is_registered_once_and_reregistered_after_restart(self, request):
        ids = iter(["v1", "v2"])
        speech_calls = []

        def respond(provider, method, path, **kwargs):
            if path == "/v1/voices":
                response = Mock()
                response.json.return_value = {"id": next(ids)}
                return response
            speech_calls.append(kwargs["data"]["voice_id"])
            if len(speech_calls) == 3:
                raise p.ProviderHTTPError("接口或模型不存在（HTTP 404）。", 404)
            return audio_response()
        request.side_effect = respond
        provider = {"id": "breeze-cloud", "type": "breeze", "base_url": "https://tts.example.test"}
        reference = {"path": str(self.ref), "transcript": "参考文字。"}
        p.synthesize(provider, PLAN, reference, self.out("a.wav"), {})
        p.synthesize(provider, PLAN, reference, self.out("b.wav"), {})
        p.synthesize(provider, PLAN, reference, self.out("c.wav"), {})
        voices = [call for call in request.call_args_list if call.args[2] == "/v1/voices"]
        self.assertEqual(len(voices), 2)
        self.assertEqual(speech_calls, ["v1", "v1", "v1", "v2"])

    @patch.object(p, "_request")
    def test_breeze_missing_preset_reports_server_voice_name(self, request):
        request.side_effect = p.ProviderHTTPError("接口或模型不存在（HTTP 404）。", 404)
        provider = {"id": "breeze-cloud", "type": "breeze", "base_url": "https://tts.example.test"}
        reference = {"provider_voice_ids": {"breeze-cloud": "raven"}}
        with self.assertRaisesRegex(p.ProviderError, "raven"):
            p.synthesize(provider, PLAN, reference, self.out(), {})

    @patch.object(p, "_request")
    def test_remote_drama3_inlines_reference_on_speech_route(self, request):
        request.return_value = audio_response({"audio": base64.b64encode(wav_bytes()).decode("ascii"), "format": "wav"})
        provider = {"id": "drama-cloud", "type": "drama3_local", "model": "drama-3",
                    "base_url": "https://tts.example.test", "api_key": "k"}
        p.synthesize(provider, PLAN, {"path": str(self.ref), "transcript": "参考文字。"}, self.out(), {})
        args, kwargs = request.call_args
        self.assertEqual(args[1:], ("POST", "/v1/audio/speech"))
        body = kwargs["json"]
        self.assertEqual(body["voice_ref"]["type"], "base64")
        self.assertEqual(sf.info(io.BytesIO(base64.b64decode(body["voice_ref"]["data"]))).frames, 16000)
        self.assertEqual((body["input"], body["reference_text"], body["response_format"]),
                         ("我马上回来。", "参考文字。", "json"))

    @patch.object(p, "_ensure")
    @patch.object(p, "_request")
    def test_drama3_preset_voice_sends_name_only(self, request, ensure):
        request.return_value = audio_response({"audio": base64.b64encode(wav_bytes()).decode("ascii")})
        provider = {"id": "drama-cloud", "type": "drama3_local", "base_url": "https://tts.example.test"}
        p.synthesize(provider, PLAN, {"transcript": "别的文字", "provider_voice_ids": {"drama-cloud": "raven"}}, self.out(), {})
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["voice"], "raven")
        self.assertNotIn("voice_ref", body)
        self.assertNotIn("reference_text", body)

    @patch.object(p, "_request")
    def test_remote_gptsovits_uses_server_reference_path(self, request):
        request.return_value = audio_response()
        provider = {"id": "sovits-cloud", "type": "gptsovits", "base_url": "https://tts.example.test"}
        reference = {"transcript": "参考文字。", "provider_voice_ids": {"sovits-cloud": "/srv/voices/raven.wav"}}
        p.synthesize(provider, PLAN, reference, self.out(), {})
        body = request.call_args.kwargs["json"]
        self.assertEqual((body["ref_audio_path"], body["prompt_text"]), ("/srv/voices/raven.wav", "参考文字。"))
        with self.assertRaisesRegex(p.ProviderError, "服务器上的参考音频路径"):
            p.synthesize(provider, PLAN, {"path": str(self.ref), "transcript": "参考文字。"}, self.out("b.wav"), {})

    def test_listening_validation_accepts_cloud_preset_and_rejects_plain_http(self):
        config = {"providers": [
            {"id": "asr", "kind": "asr", "type": "sensevoice", "name": "ASR"},
            {"id": "llm", "kind": "llm", "type": "openai_llm", "name": "LLM"},
            {"id": "tts", "kind": "tts", "type": "breeze", "name": "云端 Breeze", "base_url": "https://tts.example.test"}],
            "active": {"asr": "asr", "llm": "llm", "tts": "tts"}, "translation": {"target_language": "original"},
            "rewrite_enabled": False, "audit_enabled": False}
        with patch.object(local_runtime, "available", return_value=True), \
                patch.object(pipeline.language, "requires_llm", return_value=False):
            pipeline.validate_listening_config(config, {"provider_voice_ids": {"tts": "raven"}})
            with self.assertRaisesRegex(ValueError, "预置音色"):
                pipeline.validate_listening_config(config, {})
            config["providers"][2]["base_url"] = "http://tts.example.test"
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                pipeline.validate_listening_config(config, {"provider_voice_ids": {"tts": "raven"}})


if __name__ == "__main__":
    unittest.main()
