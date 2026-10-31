"""Exercise hostile callers, bounded decoding, and failed persistence."""
from pathlib import Path
from types import SimpleNamespace
import asyncio
import io
import json
import tempfile
import unittest
from unittest.mock import patch

import httpx
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from test_service import create_app, FakeProviders, FakeAudio, wav_bytes
from backend.app import LocalOnlyMiddleware
from backend.store import Store


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.app = create_app(self.folder.name, providers=FakeProviders(), audio_factory=FakeAudio, seed_reference=False)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:17861", client=("127.0.0.1", 50000))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def authorize(self):
        return self.client.post("/api/auth/bootstrap", json={"session": self.app.state.access.browser_session})

    def test_no_authority_cannot_read_private_data_or_start_recording(self):
        for url in ("/api/config", "/api/state", "/api/references", "/api/audio/references/test.wav",
                    "/comparison/index.html", "/comparison/data.json", "/api/export/session"):
            self.assertEqual(self.client.get(url).status_code, 401, url)
        self.assertEqual(self.client.post("/api/listen/start").status_code, 401)
        self.assertEqual(self.app.state.pipeline.audio.start_calls, 0)
        self.assertEqual(self.app.state.store.records(), [])
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_health_proof_cannot_be_used_as_session_and_disk_secret_is_encrypted(self):
        response = self.client.get("/api/health?challenge=" + "a" * 64).json()
        self.assertEqual(len(response["proof"]), 64)
        self.assertNotIn("proof", self.client.get("/api/health?challenge=browser-session-v1").json())
        self.assertEqual(self.client.post("/api/auth/bootstrap", json={"session": response["proof"]}).status_code, 401)
        stored = Path(self.folder.name, ".local-access.json").read_text(encoding="utf-8")
        self.assertIn("dpapi:", stored)
        self.assertNotIn(self.app.state.access.browser_session, stored)

    def test_cookie_authorizes_same_origin_but_cross_site_and_null_origin_are_denied(self):
        result = self.authorize()
        self.assertEqual(result.status_code, 200)
        cookie = result.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertEqual(self.client.get("/api/config").status_code, 200)
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}, {"Host": "evil.example:17861"}):
            self.assertEqual(self.client.get("/api/state", headers=headers).status_code, 403)
            self.assertEqual(self.client.post("/api/listen/stop", headers=headers).status_code, 403)

    def test_remote_peer_cannot_spoof_loopback_host(self):
        with TestClient(self.app, base_url="http://127.0.0.1:17861", client=("198.51.100.10", 50000),
                        headers={"Authorization": "Bearer " + self.app.state.access.browser_session}) as remote:
            self.assertEqual(remote.get("/api/config").status_code, 403)

    def test_subtitle_capability_reads_only_subtitles(self):
        self.authorize()
        links = self.client.get("/api/access/outputs").json()
        self.client.cookies.clear()
        self.assertEqual(self.client.get(links["browser"]).status_code, 200)
        self.assertEqual(self.client.get(links["json"]).status_code, 200)
        suffix = "?access=" + self.app.state.access.subtitle_key
        self.assertEqual(self.client.get("/api/config" + suffix).status_code, 401)
        self.assertEqual(self.client.get("/api/audio/runs/test.wav" + suffix).status_code, 401)
        self.assertEqual(self.client.post("/api/subtitles/clear" + suffix).status_code, 401)
        self.assertEqual(self.client.get("/subtitles?access=wrong").status_code, 401)

    def test_malformed_cookie_and_non_ascii_token_remain_unauthorized(self):
        for value in ('voice_loom_session="bad', 'voice_loom_session="\\xff"', 'bad cookie'):
            self.assertEqual(self.client.get("/api/config", headers={"Cookie": value}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/bootstrap", json={"session": "俄语"}).status_code, 401)

    def test_actual_decoded_duration_is_checked_even_if_header_lies(self):
        self.authorize()
        buffer = io.BytesIO()
        sf.write(buffer, np.zeros(8000 * 61, dtype=np.float32), 8000, format="WAV", subtype="PCM_16")
        fake_info = SimpleNamespace(frames=800, samplerate=8000, channels=1, duration=.1)
        with patch("backend.app.sf.info", return_value=fake_info):
            result = self.client.post("/api/references", files={"file": ("header.wav", buffer.getvalue())})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(list(Path(self.folder.name, "references").glob("*.wav")), [])

    def test_high_rate_multichannel_import_uses_bounded_blocks(self):
        self.authorize()
        buffer = io.BytesIO()
        sf.write(buffer, np.zeros((192000, 8), dtype=np.float32), 192000, format="FLAC")
        with patch("backend.app.sf.read", side_effect=AssertionError("whole-file decoding is forbidden")):
            result = self.client.post("/api/references", files={"file": ("eight.flac", buffer.getvalue())})
        self.assertEqual(result.status_code, 200, result.text)
        actual = sf.info(self.app.state.store.reference(result.json()["id"], private=True)["path"])
        self.assertEqual((actual.channels, actual.frames, actual.samplerate), (1, 192000, 192000))

    def test_hidden_files_and_windows_stream_paths_are_rejected(self):
        self.authorize()
        for url in ("/.git/config", "/.env", "/api/audio/runs/x.wav:secret.wav", "/api/audio/runs/%5C..%5Cconfig.json"):
            self.assertEqual(self.client.get(url).status_code, 404, url)


class PersistenceTests(unittest.TestCase):
    def test_disk_failure_keeps_records_in_memory_and_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory, seed_reference=False)
            store.add_record({"id": "one", "status": "done"})
            old = store.records()
            saved = Path(directory, "records.json").read_bytes()
            for call in (lambda: store.add_record({"id": "two"}),
                         lambda: store.update_record("one", status="error"), store.clear_records):
                with patch("backend.store.atomic_json", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        call()
                self.assertEqual(store.records(), old)
                self.assertEqual(Path(directory, "records.json").read_bytes(), saved)


class ConcurrentUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_third_upload_is_rejected_without_entering_body_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, providers=FakeProviders(), audio_factory=FakeAudio, seed_reference=False)
            entered = 0
            ready, release = asyncio.Event(), asyncio.Event()

            async def held(scope, receive, send):
                nonlocal entered
                entered += 1
                if entered == 2:
                    ready.set()
                await release.wait()
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"{}"})

            guarded = LocalOnlyMiddleware(held, app.state.access)
            transport = httpx.ASGITransport(app=guarded, client=("127.0.0.1", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:17861",
                                         headers={"Authorization": "Bearer " + app.state.access.browser_session}) as client:
                first = asyncio.create_task(client.post("/api/references"))
                second = asyncio.create_task(client.post("/api/references"))
                await asyncio.wait_for(ready.wait(), 2)
                self.assertEqual((await client.post("/api/references")).status_code, 429)
                self.assertEqual(entered, 2)
                release.set()
                self.assertTrue(all(r.status_code == 200 for r in await asyncio.gather(first, second)))
