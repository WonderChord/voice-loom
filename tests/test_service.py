"""API 集成验证：使用假适配器，不录音、不播放、不请求云端。"""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

# Importing the production ASGI object must not touch the user's live data.
with tempfile.TemporaryDirectory() as bootstrap_dir:
    previous_data_dir = os.environ.get("VOICE_LOOM_DATA")
    os.environ["VOICE_LOOM_DATA"] = bootstrap_dir
    try:
        from backend.app import create_app
    finally:
        if previous_data_dir is None:
            os.environ.pop("VOICE_LOOM_DATA", None)
        else:
            os.environ["VOICE_LOOM_DATA"] = previous_data_dir
from backend.store import Store
from backend.hotkeys import NullHotkeys
import backend.app as app_module
import backend.store as store_module


def wav_bytes(samples=None, rate=16000):
    target = io.BytesIO()
    if samples is None:
        samples = .1 * np.sin(np.arange(3200) * 2 * np.pi * 200 / rate)
    sf.write(target, samples, rate, format="WAV", subtype="PCM_16")
    return target.getvalue()


class FakeAudio:
    def __init__(self, on_segment, on_status):
        self.on_segment, self.on_status = on_segment, on_status
        self.is_playing = False
        self.play_calls = 0
        self.start_calls = 0

    def start(self, config):
        self.start_calls += 1
        self.on_status({"listening": True})

    def stop(self):
        self.on_status({"listening": False})
        self.stop_playback()

    def flush(self):
        self.on_segment(np.zeros(1600, dtype=np.float32), 16000)

    def play(self, path, device=None, blocking=True):
        self.play_calls += 1
        self.is_playing = False

    def stop_playback(self):
        self.is_playing = False


class FakeProviders:
    def __init__(self):
        self.calls = []
        self.hold = threading.Event()
        self.entered = threading.Event()
        self.block = False
        self.review = False
        self.directive = False
        self.malformed = False
        self.event_only = False
        self.fail_secret = ""

    def transcribe(self, provider, path):
        self.calls.append(("asr", provider["id"]))
        return "参考录音文字"

    def rewrite(self, provider, text, history, config):
        self.calls.append(("llm", text, copy.deepcopy(history), {key: value for key, value in config.items() if key != "_cancel_check"}))
        if self.block:
            self.entered.set()
            self.hold.wait(4)
        if self.fail_secret:
            raise RuntimeError("Authorization: Bearer " + self.fail_secret)
        if self.malformed:
            return {"source_text": text, "text": "", "segments": []}
        if self.directive:
            return {"source_text": text, "text": "", "segments": [], "directives": ["小声一点"], "warnings": []}
        if self.event_only:
            return {"source_text": text, "text": "", "segments": [{"kind": "event", "event": "sigh"}], "warnings": []}
        return {"source_text": text, "text": text + "。", "segments": [{"kind": "speech", "text": text + "。", "emotion": "calm", "intensity": 0.3}], "warnings": [], "needs_review": self.review, "audit": {"passed": not self.review, "message": "需人工确认" if self.review else "已通过"}}

    def synthesize(self, provider, plan, reference, output_path, config):
        config["_cancel_check"]()
        self.calls.append(("tts", plan["text"], config.get("_human_confirmed", False)))
        output_path.write_bytes(wav_bytes())
        return {"path": str(output_path), "warnings": []}

    def test_provider(self, provider):
        return {"ok": True, "message": "仅检查健康状态"}

    def capabilities(self, provider):
        return {"whisper": False}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.providers = FakeProviders()
        self.app = create_app(self.folder.name, providers=self.providers, audio_factory=FakeAudio, hotkey_factory=NullHotkeys, devices_fn=lambda: {"inputs": [], "outputs": [], "default_input": None, "default_output": None}, seed_reference=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:17861", client=("127.0.0.1", 50000), headers={"Authorization": "Bearer " + self.app.state.access.browser_session})
        self.client.__enter__()

    def tearDown(self):
        self.providers.hold.set()
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def wait_record(self, record_id, status=None):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            record = self.app.state.store.record(record_id)
            if record["status"] in (status or {"done", "review", "error", "cancelled", "skipped"}):
                return record
            time.sleep(0.01)
        self.fail("任务未在预期时间内完成")

    def submit(self, text="你好", preview=True):
        response = self.client.post("/api/process/text", json={"text": text, "preview_only": preview})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def test_defaults_and_strict_input(self):
        config = self.client.get("/api/config").json()
        self.assertFalse(config["rewrite_enabled"])
        self.assertEqual(config["active"]["asr"], "qwen")
        self.assertTrue(config["audio"]["skip_low_energy"])
        self.assertEqual(config["audio"]["energy_threshold_db"], -45)
        self.assertEqual(config["audio"]["min_active_ms"], 120)
        config["context_limit"] = 51
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 422)
        self.assertEqual(self.client.post("/api/process/text", json={"text": " "}).status_code, 422)
        self.assertEqual(self.client.get("/api/not-a-route").status_code, 404)
        self.assertEqual(self.client.post("/api/listen/flush").status_code, 422)

    def test_file_only_job_preserves_live_playback_preference(self):
        before=self.client.get('/api/config').json()['audio']['auto_play']
        response=self.client.post('/api/process/text',json={'text':'仅生成文件的合成测试','preview_only':False,'play_audio':False})
        self.assertEqual(response.status_code,200,response.text)
        record=self.wait_record(response.json()['id'])
        self.assertEqual(record['status'],'done',record)
        self.assertTrue(record['audio_url'])
        self.assertEqual(self.app.state.pipeline.audio.play_calls,0)
        self.assertEqual(self.client.get('/api/config').json()['audio']['auto_play'],before)

    def test_readiness_locates_missing_reference_and_fallback_without_starting_audio(self):
        pipeline = self.app.state.pipeline
        config = self.client.get("/api/config").json()
        issue = self.client.get("/api/listen/readiness").json()
        self.assertFalse(issue["ready"])
        self.assertEqual((issue["page"], issue["provider"]), ("voices", config["active"]["tts"]))
        denied = self.client.post("/api/listen/start")
        self.assertEqual(denied.status_code, 422)
        self.assertEqual(denied.json()["detail"], issue["message"])
        config["providers"].append({"id": "ready-local", "name": "就绪语音", "kind": "tts",
            "type": "openai_tts", "base_url": "http://127.0.0.1:19765", "model": "tts", "voice": "test"})
        config["active"].update(tts="ready-local", tts_fallback="elevenlabs")
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 200)
        issue = self.client.get("/api/listen/readiness").json()
        self.assertEqual((issue["ready"], issue["page"], issue["provider"]), (False, "models", "elevenlabs"))
        self.assertIn("备用 TTS", issue["message"])
        config["active"]["tts_fallback"] = ""
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 200)
        self.assertEqual(self.client.get("/api/listen/readiness").json(), {"ready": True})
        self.assertEqual(pipeline.audio.start_calls, 0)
        self.assertEqual(self.providers.calls, [])

    def test_dpapi_key_redaction_keep_and_clear(self):
        config = self.client.get("/api/config").json()
        provider = next(p for p in config["providers"] if p["id"] == "cloud-llm")
        key = "service-test-only-key-12345"
        provider["api_key"] = key
        saved = self.client.put("/api/config", json=config)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn(key, saved.text)
        self.assertNotIn(key, (Path(self.folder.name) / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(self.app.state.store.provider("cloud-llm")["api_key"], key)
        self.assertEqual(self.client.put("/api/config", json=saved.json()).status_code, 200)
        restored = Store(self.folder.name, seed_reference=False)
        self.assertEqual(restored.provider("cloud-llm")["api_key"], key)
        self.providers.fail_secret = key
        record = self.wait_record(self.submit())
        self.assertEqual(record["status"], "error")
        self.assertNotIn(key, json.dumps(self.client.get("/api/state").json()))
        cleaned = saved.json()
        next(p for p in cleaned["providers"] if p["id"] == "cloud-llm")["clear_key"] = True
        self.assertEqual(self.client.put("/api/config", json=cleaned).status_code, 200)
        self.assertEqual(self.app.state.store.provider("cloud-llm")["api_key"], "")

    def test_host_origin_and_upload_restrictions(self):
        for headers in ({"host": "evil.example"}, {"origin": "https://evil.example"}, {"origin": "http://localhost:9999"}, {"sec-fetch-site": "cross-site"}):
            self.assertEqual(self.client.post("/api/listen/start", headers=headers).status_code, 403)
        self.assertFalse(self.app.state.pipeline.listening)
        self.assertEqual(self.client.post("/api/process/audio", files={"file": ("bad.wav", b"not wave")}).status_code, 422)
        self.assertEqual(self.client.post("/api/process/audio", files={"file": ("bad.html", b"html")}).status_code, 415)
        self.assertEqual(self.client.post("/api/process/audio", content=b"x", headers={"Content-Length": str(30 * 1024 * 1024)}).status_code, 413)
        self.assertEqual(self.client.get("/api/audio/..%2Fconfig.json").status_code, 404)

    def test_chunked_oversized_body_returns_413_without_creating_data(self):
        text_body = json.dumps({"text": "x" * 2048, "preview_only": True}).encode()
        boundary = "voice-loom-isolated-test"
        file_body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"reference.wav\"\r\n"
                     "Content-Type: audio/wav\r\n\r\n").encode() + wav_bytes() + f"\r\n--{boundary}--\r\n".encode()
        for route, content_type, payload in [
            ("/api/process/text", "application/json", text_body),
            ("/api/references", "multipart/form-data; boundary=" + boundary, file_body),
        ]:
            with self.subTest(route=route), patch.object(app_module, "MAX_BODY", 1024):
                response = self.client.post(route, content=iter([payload[:900], payload[900:]]),
                                            headers={"content-type": content_type})
                self.assertNotIn("content-length", response.request.headers)
                self.assertEqual(response.status_code, 413, response.text)
                self.assertIn("文件过大", response.json()["detail"])
        self.assertEqual(self.app.state.store.records(), [])
        self.assertEqual(self.app.state.store.references(), [])
        self.assertEqual(list(self.app.state.store.references_dir.glob("*.wav")), [])
        self.assertEqual(self.providers.calls, [])

    def test_reference_library_and_selection(self):
        response = self.client.post("/api/references", files={"file": ("../../voice.wav", wav_bytes())}, data={"name": "试音", "transcript": "你好", "language": "zh"})
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()
        self.assertNotIn("path", item)
        self.assertEqual(self.client.get("/api/config").json()["active"]["reference"], item["id"])
        changed = self.client.patch("/api/references/" + item["id"], json={"name": "新名字", "pinned": True, "favorite": True, "tags": ["温柔"]}).json()
        self.assertTrue(changed["pinned"] and changed["favorite"])
        self.assertEqual(changed["name"], "新名字")
        self.assertEqual(self.client.post("/api/references/" + item["id"] + "/transcribe").json()["transcript"], "参考录音文字")
        self.assertEqual(self.client.get(item["audio_url"]).status_code, 200)
        self.assertEqual(self.client.delete("/api/references/" + item["id"]).status_code, 200)
        self.assertEqual(self.client.get("/api/config").json()["active"]["reference"], "")

    def test_reference_changes_remain_consistent_when_persistence_fails(self):
        write = store_module.atomic_json
        store = self.app.state.store
        for action in ("add", "delete"):
            if action == "delete":
                created = self.client.post("/api/references", files={"file": ("kept.wav", wav_bytes())},
                                           data={"name": "保留的参考", "transcript": "参考台词"}).json()
            for filename in ("config.json", "references.json"):
                with self.subTest(action=action, failed_write=filename):
                    before_references = store.references(private=True)
                    before_selection = store.config()["active"]["reference"]
                    before_files = set(store.references_dir.glob("*.wav"))
                    def fail_one_file(path, value):
                        if path.name == filename:
                            raise OSError("模拟写盘失败")
                        return write(path, value)
                    with patch.object(store_module, "atomic_json", side_effect=fail_one_file), \
                            patch.object(self.client._transport, "raise_server_exceptions", False):
                        if action == "add":
                            response = self.client.post("/api/references", files={"file": ("failed.wav", wav_bytes())},
                                                        data={"name": "失败的参考", "transcript": "参考台词"})
                        else:
                            response = self.client.delete("/api/references/" + created["id"])
                    self.assertEqual(response.status_code, 500, response.text)
                    self.assertEqual(store.references(private=True), before_references)
                    self.assertEqual(store.config()["active"]["reference"], before_selection)
                    self.assertEqual(set(store.references_dir.glob("*.wav")), before_files)
                    restored = Store(self.folder.name, seed_reference=False)
                    self.assertEqual(restored.references(private=True), before_references)
                    self.assertEqual(restored.config()["active"]["reference"], before_selection)
                    self.assertTrue(all(Path(reference["path"]).exists() for reference in restored.references(private=True)))

    def test_prompts_theme_and_context_strict_cap(self):
        config = self.client.get("/api/config").json()
        config.update(context_limit=3, persona="录音人的性格", prompt="措辞规则", speech_prompt="耳语表演规则", appearance={"mode": "light", "accent": "#aabbcc"})
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 200)
        for number in range(3):
            self.assertEqual(self.wait_record(self.submit(str(number), preview=False))["status"], "done")
        calls = [c for c in self.providers.calls if c[0] == "llm"]
        self.assertEqual(len(calls[-1][2]), 3)
        # 预览稿只试听，不进入上下文。
        history = list(self.app.state.pipeline.history)
        preview = self.wait_record(self.submit("只是预览"))
        self.assertFalse(preview["context_member"])
        self.assertEqual(self.app.state.pipeline.history, history)
        self.assertEqual(calls[-1][3]["speech_prompt"], "耳语表演规则")
        self.assertEqual(calls[-1][3]["persona"], "录音人的性格")
        config["context_limit"] = 0
        self.client.put("/api/config", json=config)
        self.wait_record(self.submit("零上下文"))
        self.assertEqual([c for c in self.providers.calls if c[0] == "llm"][-1][2], [])

    def test_stop_cancels_active_and_bounded_queue_without_tts(self):
        self.providers.block = True
        first = self.submit("阻塞中", preview=False)
        self.assertTrue(self.providers.entered.wait(2))
        queued = [self.submit(str(i), preview=False) for i in range(3)]
        self.assertEqual(self.client.post("/api/process/text", json={"text": "溢出"}).status_code, 409)
        self.client.post("/api/listen/stop")
        self.providers.hold.set()
        for item in [first, *queued]:
            self.assertEqual(self.wait_record(item)["status"], "cancelled")
        time.sleep(0.1)
        self.assertFalse(any(c[0] == "tts" for c in self.providers.calls))
        self.assertEqual(self.app.state.pipeline.audio.play_calls, 0)
        self.assertEqual(self.client.get("/api/state").json()["queue_size"], 0)

    def test_review_and_explicit_edit_confirmation(self):
        self.providers.review = True
        original_id = self.submit("不要把指令读出来", preview=False)
        original = self.wait_record(original_id)
        self.assertEqual(original["status"], "review")
        self.assertFalse(any(c[0] == "tts" for c in self.providers.calls))
        response = self.client.post(f"/api/records/{original_id}/synthesize", json={"plan": original["plan"]})
        self.assertEqual(response.status_code, 200, response.text)
        new = self.wait_record(response.json()["id"])
        self.assertEqual(new["status"], "done", new)
        self.assertNotEqual(new["id"], original_id)
        self.assertTrue([c for c in self.providers.calls if c[0] == "tts"][-1][2])
        self.assertEqual(self.app.state.store.record(original_id)["status"], "review")

    def test_pure_directive_has_no_tts(self):
        self.providers.directive = True
        result = self.wait_record(self.submit("小声一点", preview=False))
        self.assertEqual(result["status"], "done")
        self.assertIsNone(result["audio_url"])
        self.assertFalse(any(c[0] == "tts" for c in self.providers.calls))

    def test_invalid_plan_and_device_change_while_listening(self):
        self.providers.malformed = True
        self.assertEqual(self.wait_record(self.submit())["status"], "error")
        self.client.post("/api/references", files={"file": ("voice.wav", wav_bytes())}, data={"transcript": "参考台词"})
        self.client.post("/api/listen/start")
        config = self.client.get("/api/config").json()
        config["audio"]["input_device"] = 1
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 409)
        self.client.post("/api/listen/stop")
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 200)

    def test_start_validates_configuration_before_opening_microphone(self):
        response = self.client.post("/api/listen/start")
        self.assertEqual(response.status_code, 422)
        self.assertIn("参考音频", response.json()["detail"])
        self.assertEqual(self.app.state.pipeline.audio.start_calls, 0)
        reference = self.client.post("/api/references", files={"file": ("voice.wav", wav_bytes())}).json()
        response = self.client.post("/api/listen/start")
        self.assertEqual(response.status_code, 422)
        self.assertIn("台词", response.json()["detail"])
        self.client.patch("/api/references/" + reference["id"], json={"transcript": "准确台词"})
        config = self.client.get("/api/config").json()
        config["rewrite_enabled"] = True
        self.client.put("/api/config", json=config)
        response = self.client.post("/api/listen/start")
        self.assertEqual(response.status_code, 422)
        self.assertIn("密钥", response.json()["detail"])
        config["rewrite_enabled"] = False
        config["active"]["asr"] = "cloud-asr"
        self.client.put("/api/config", json=config)
        self.assertEqual(self.client.post("/api/listen/start").status_code, 422)
        config["active"]["asr"] = "qwen"
        config["active"]["tts"] = "cloud-tts"
        cloud_tts = next(p for p in config["providers"] if p["id"] == "cloud-tts")
        cloud_tts.update(base_url="http://127.0.0.1:9999/v1", voice="")
        self.client.put("/api/config", json=config)
        response = self.client.post("/api/listen/start")
        self.assertEqual(response.status_code, 422)
        self.assertIn("声音 ID", response.json()["detail"])
        self.assertEqual(self.app.state.pipeline.audio.start_calls, 0)
        self.assertEqual(self.providers.calls, [])
        config["active"]["tts"] = "breeze"
        self.client.put("/api/config", json=config)
        self.assertEqual(self.client.post("/api/listen/start").status_code, 200)
        self.assertEqual(self.app.state.pipeline.audio.start_calls, 1)

    def test_long_imports_are_rejected_before_asr(self):
        buffer = io.BytesIO()
        sf.write(buffer, np.zeros(16000 * 61, dtype=np.float32), 16000, format="WAV", subtype="PCM_16")
        for endpoint in ("/api/process/audio", "/api/references"):
            response = self.client.post(endpoint, files={"file": ("long.wav", buffer.getvalue())})
            self.assertEqual(response.status_code, 422)
            self.assertIn("60", response.json()["detail"])
        self.assertEqual(self.providers.calls, [])

    def test_energy_threshold_and_duration_settings_have_bounds(self):
        for field, value in (("energy_threshold_db", -81), ("energy_threshold_db", -9),
                             ("min_active_ms", 19), ("min_active_ms", 1001)):
            config = self.client.get("/api/config").json()
            config["audio"][field] = value
            self.assertEqual(self.client.put("/api/config", json=config).status_code, 422)

    def test_low_energy_file_skips_all_models_and_preserves_context(self):
        self.wait_record(self.submit("已有上下文"))
        history = copy.deepcopy(self.app.state.pipeline.history)
        calls = copy.deepcopy(self.providers.calls)
        response = self.client.post("/api/process/audio", files={"file": (
            "quiet.wav", wav_bytes(np.full(16000, .0001)))})
        self.assertEqual(response.status_code, 200, response.text)
        record = self.wait_record(response.json()["id"])
        self.assertEqual(record["status"], "skipped")
        self.assertEqual(record["energy_check"]["active_ms"], 0)
        self.assertIn("已跳过识别", record["skip_reason"])
        self.assertFalse(record["context_member"])
        self.assertIsNone(record["error"])
        self.assertEqual(self.providers.calls, calls)
        self.assertEqual(self.app.state.pipeline.history, history)
        self.assertEqual(self.app.state.pipeline.audio.play_calls, 0)
        state = self.client.get("/api/state").json()
        self.assertEqual(state["skipped_audio_count"], 1)
        self.assertEqual(state["last_skip_reason"], record["skip_reason"])
        self.assertIsNone(state["last_error"])
        self.client.post("/api/listen/stop")
        reloaded = Store(self.folder.name, seed_reference=False)
        self.assertEqual(reloaded.record(record["id"])["status"], "skipped")
        self.client.post("/api/session/clear")
        self.assertEqual(self.client.get("/api/state").json()["skipped_audio_count"], 0)

    def test_low_energy_microphone_segment_uses_the_same_gate(self):
        self.client.post("/api/references", files={"file": ("voice.wav", wav_bytes())},
                         data={"transcript": "参考台词"})
        self.assertEqual(self.client.post("/api/listen/start").status_code, 200)
        self.app.state.pipeline.audio.on_segment(np.full((16000, 1), .0001), 16000)
        record = self.wait_record(self.app.state.store.records()[-1]["id"])
        self.assertEqual(record["origin"], "mic")
        self.assertEqual(record["status"], "skipped")
        self.assertEqual(self.providers.calls, [])
        self.assertEqual(self.app.state.pipeline.history, [])

    def test_character_card_switches_voice_tts_style_and_pronunciation(self):
        first = self.client.post("/api/references", files={"file": ("a.wav", wav_bytes())},
                                 data={"transcript": "参考台词"}).json()
        second = self.client.post("/api/references", files={"file": ("b.wav", wav_bytes())},
                                  data={"transcript": "另一段"}).json()
        config = self.client.get("/api/config").json()
        config["characters"] = [{
            "id": "noa", "name": "乃爱", "reference": second["id"], "tts": "elevenlabs",
            "persona": "我是乃爱。", "prompt": "冷淡简短。", "style_strength": "strong",
            "rewrite_enabled": False, "target_language": "original", "emotion_intensity": 0.3,
            "pronunciations": [{"text": "乃爱", "say": "奶爱"}]}]
        config["pronunciations"] = [{"text": "声织", "say": "声之"}]
        config["active"]["reference"] = first["id"]
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 200)
        preview = self.client.post("/api/pronunciations/preview", json={"text": "乃爱用声织"}).json()
        self.assertEqual(preview["text"], "乃爱用声之")
        applied = self.client.post("/api/characters/noa/apply").json()["config"]
        self.assertEqual(applied["active_character"], "noa")
        self.assertEqual(applied["active"]["reference"], second["id"])
        self.assertEqual(applied["active"]["tts"], "elevenlabs")
        self.assertEqual((applied["persona"], applied["style_strength"], applied["emotion_intensity"]),
                         ("我是乃爱。", "strong", 0.3))
        preview = self.client.post("/api/pronunciations/preview", json={"text": "乃爱用声织"}).json()
        self.assertEqual(preview, {"provider": "elevenlabs", "text": "奶爱用声之"})
        self.assertEqual(self.client.post("/api/characters/ghost/apply").status_code, 404)
        # 删除卡上的参考语音后切换：保留当前音色并提示，不阻断切换。
        self.client.delete(f"/api/references/{second['id']}")
        result = self.client.post("/api/characters/noa/apply").json()
        self.assertEqual(result["warnings"], ["角色卡的参考语音已删除，保留当前音色"])
        self.assertEqual(result["config"]["active"]["reference"], first["id"])

    def test_remote_tts_is_probed_while_listening_and_local_tts_is_not(self):
        self.client.post("/api/references", files={"file": ("voice.wav", wav_bytes())},
                         data={"transcript": "参考台词"})
        self.assertEqual(self.client.post("/api/listen/start").status_code, 200)
        self.assertIsNone(self.client.get("/api/state").json()["tts_remote"])
        self.client.post("/api/listen/stop")
        config = self.client.get("/api/config").json()
        tts = next(p for p in config["providers"] if p["id"] == config["active"]["tts"])
        tts["base_url"] = "https://tts.example.test"
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 200)
        self.assertEqual(self.client.post("/api/listen/start").status_code, 200)
        deadline = time.monotonic() + 2
        while not self.client.get("/api/state").json()["tts_remote"] and time.monotonic() < deadline:
            time.sleep(0.01)
        status = self.client.get("/api/state").json()["tts_remote"]
        self.assertTrue(status["ok"])
        self.assertEqual(status["provider"], tts["name"])
        self.client.post("/api/listen/stop")
        self.app.state.pipeline.probe_thread.join(timeout=2)
        self.assertFalse(self.app.state.pipeline.probe_thread.is_alive())

    def test_processed_input_audio_is_removed_and_full_queue_drop_is_reported(self):
        self.client.post("/api/references", files={"file": ("voice.wav", wav_bytes())},
                         data={"transcript": "参考台词"})
        runs = self.app.state.store.runs
        response = self.client.post("/api/process/audio", files={"file": ("voice.wav", wav_bytes())})
        record = self.wait_record(response.json()["id"])
        self.assertEqual(record["status"], "done")
        # 上传的输入音频处理后删除，只留下合成结果（清理在记录标记完成之后执行）。
        deadline = time.monotonic() + 4
        while len(list(runs.glob("*.wav"))) > 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(sorted(p.name for p in runs.glob("*.wav")), [record["id"] + ".wav"])
        self.providers.block = True
        self.assertEqual(self.client.post("/api/listen/start").status_code, 200)
        first = self.submit("阻塞中", preview=False)
        self.assertTrue(self.providers.entered.wait(2))
        for number in range(3):
            self.submit(str(number), preview=False)
        before = set(runs.glob("*_input.wav"))
        self.app.state.pipeline.audio.on_segment(np.full((16000, 1), .1), 16000)
        state = self.client.get("/api/state").json()
        self.assertEqual(state["dropped_audio_count"], 1)
        self.assertIn("队列已满", state["last_error"])
        self.assertEqual(set(runs.glob("*_input.wav")), before)
        self.client.post("/api/listen/stop")
        self.providers.hold.set()
        self.wait_record(first)

    def test_restart_removes_reference_copies_and_keeps_recordings(self):
        runs = self.app.state.store.runs
        for name in ("abc.wav", "abc_reference.wav", "old_input.wav"):
            (runs / name).write_bytes(wav_bytes())
        Store(self.folder.name, seed_reference=False)
        # 参考副本可从参考库重建；旧版留下的输入录音可能是唯一一份，保留。
        self.assertEqual(sorted(p.name for p in runs.glob("*.wav")), ["abc.wav", "old_input.wav"])

    def test_low_energy_reference_keeps_transcript_and_returns_friendly_reason(self):
        response = self.client.post("/api/references", files={"file": (
            "silent.wav", wav_bytes(np.zeros(16000)))}, data={"transcript": "已有参考台词"})
        reference = response.json()
        response = self.client.post(f"/api/references/{reference['id']}/transcribe")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("已跳过识别", response.json()["detail"])
        self.assertEqual(self.providers.calls, [])
        self.assertEqual(self.app.state.store.reference(reference["id"])["transcript"], "已有参考台词")
        self.assertEqual(self.client.get("/api/state").json()["skipped_audio_count"], 1)
        self.assertEqual(self.app.state.store.records(), [])

    def test_long_silence_around_audible_audio_still_reaches_asr(self):
        samples = np.concatenate((np.zeros(16000 * 10), np.full(3200, .01), np.zeros(16000 * 10)))
        response = self.client.post("/api/process/audio", files={"file": ("voice.wav", wav_bytes(samples))})
        record = self.wait_record(response.json()["id"])
        self.assertEqual(record["status"], "done", record)
        self.assertTrue(record["energy_check"]["accepted"])
        self.assertEqual(record["energy_check"]["active_ms"], 200)
        self.assertEqual([call[0] for call in self.providers.calls], ["asr", "llm", "tts"])

    def test_energy_gate_can_be_explicitly_disabled(self):
        config = self.client.get("/api/config").json()
        config["audio"]["skip_low_energy"] = False
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 200)
        response = self.client.post("/api/process/audio", files={"file": (
            "silence.wav", wav_bytes(np.zeros(16000)))})
        record = self.wait_record(response.json()["id"])
        self.assertEqual(record["status"], "done", record)
        self.assertEqual([call[0] for call in self.providers.calls], ["asr", "llm", "tts"])
        self.assertEqual(self.client.get("/api/state").json()["skipped_audio_count"], 0)

    def test_event_only_plan_can_be_confirmed_and_synthesized(self):
        self.providers.event_only = True
        result = self.wait_record(self.submit("叹一口气", preview=False))
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(result["text"], "")
        self.assertTrue(result["audio_url"])
        self.assertTrue(any(c[0] == "tts" for c in self.providers.calls))

    def test_history_export_clear_and_restart_does_not_revive_queue(self):
        record = self.wait_record(self.submit("保留音频", preview=False))
        self.assertEqual(record["status"], "done")
        self.assertEqual(self.client.get(record["audio_url"]).status_code, 200)
        exported = self.client.get("/api/export/session")
        self.assertEqual(len(exported.json()["records"]), 1)
        self.assertNotIn("api_key", exported.text)
        self.app.state.store.add_record({"id": "interrupted", "status": "tts"})
        reloaded = Store(self.folder.name, seed_reference=False)
        self.assertEqual(reloaded.record("interrupted")["status"], "cancelled")
        self.client.post("/api/session/clear")
        self.assertEqual(self.client.get("/api/state").json()["records"], [])
        self.assertEqual(self.app.state.pipeline.history, [])
        self.assertEqual(self.client.get(record["audio_url"]).status_code, 200)

    def test_replay_http_keeps_listening_and_returns_404_for_missing_audio(self):
        config = self.client.get("/api/config").json()
        config["audio"]["auto_play"] = False
        self.assertEqual(self.client.put("/api/config", json=config).status_code, 200)
        record = self.wait_record(self.submit("回放已有音频", preview=False))
        provider_calls = len(self.providers.calls)
        self.assertEqual(self.app.state.pipeline.audio.play_calls, 0)
        self.assertEqual(self.client.post(f"/api/records/{record['id']}/play").status_code, 200)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.app.state.pipeline.audio.play_calls == 1 and self.app.state.pipeline.active is None:
                break
            time.sleep(.01)
        self.assertEqual(self.app.state.pipeline.audio.play_calls, 1)
        self.assertEqual(len(self.providers.calls), provider_calls)
        self.assertEqual(self.app.state.store.record(record["id"]), record)
        self.app.state.pipeline.listening = True
        self.assertEqual(self.client.post("/api/playback/stop").status_code, 200)
        self.assertTrue(self.app.state.pipeline.listening)
        self.assertFalse(self.app.state.pipeline.playing)
        preview = self.wait_record(self.submit("只有预览稿"))
        self.assertEqual(self.client.post(f"/api/records/{preview['id']}/play").status_code, 422)
        audio_path = self.app.state.store.audio_path(record["audio_url"].removeprefix("/api/audio/"))
        audio_path.unlink()
        missing = self.client.post(f"/api/records/{record['id']}/play")
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["detail"], "音频不存在")
        self.assertEqual(self.client.get(record["audio_url"]).status_code, 404)
        self.assertEqual(self.app.state.store.record(record["id"]), record)
        self.assertEqual(len(self.app.state.pipeline.jobs), 0)

    def test_repeated_stop_and_clear_are_idempotent_while_cancelled_request_returns(self):
        self.providers.block = True
        self.submit("旧请求仍在返回途中")
        self.assertTrue(self.providers.entered.wait(2))
        responses = []
        try:
            for endpoint in ("/api/session/clear", "/api/listen/stop", "/api/session/clear"):
                responses.append((endpoint, self.client.post(endpoint)))
            self.assertEqual(self.client.get("/api/state").json()["records"], [])
        finally:
            self.providers.hold.set()
            deadline = time.monotonic() + 4
            while self.app.state.pipeline.active is not None and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertIsNone(self.app.state.pipeline.active)
        for endpoint, response in responses:
            self.assertEqual(response.status_code, 200, f"{endpoint}: {response.text}")
        self.assertFalse(any(call[0] == "tts" for call in self.providers.calls))
        self.assertEqual(self.app.state.pipeline.history, [])
        self.providers.block = False
        fresh = self.wait_record(self.submit("新会话正常处理"))
        self.assertEqual(fresh["status"], "done")
        self.assertEqual([row["id"] for row in self.client.get("/api/state").json()["records"]], [fresh["id"]])


if __name__ == "__main__":
    unittest.main()
