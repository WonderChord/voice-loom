"""快捷键解析、真实 Windows 注册生命周期与隔离的流水线动作。"""
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock

from pydantic import ValidationError

from backend import hotkeys
from backend.models import Config
from backend.pipeline import Pipeline
from backend.store import Store
from test_service import FakeAudio, FakeProviders


class HotkeyTests(unittest.TestCase):
    def test_normalization_and_supported_keys(self):
        self.assertEqual(hotkeys.parse("shift+alt+control+f9"), (7, 0x78, "Ctrl+Alt+Shift+F9"))
        self.assertEqual(hotkeys.normalize(" ctrl + a "), "Ctrl+A")
        self.assertEqual(hotkeys.normalize(""), "")
        for key in ("F13", "F24", "Pause", "ScrollLock", "Alt+Numpad0", "Win+PgDn"):
            self.assertTrue(hotkeys.normalize(key))
        for key in ("F9", "Ctrl", "Ctrl+", "Ctrl+F25", "Ctrl+F12", "Ctrl+Unknown", "+A"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                hotkeys.parse(key)

    def test_duplicate_actions_and_character_keys_are_rejected_when_enabled(self):
        value = Config().model_dump()
        value["hotkeys"].update(enabled=True, flush="alt+control+f9")
        with self.assertRaisesRegex(ValidationError, "重复"):
            Config.model_validate(value)
        value["hotkeys"]["flush"] = "Ctrl+Alt+F10"
        value["characters"] = [{"id": "one", "name": "角色一", "hotkey": "Ctrl+Alt+F9"}]
        with self.assertRaisesRegex(ValidationError, "重复"):
            Config.model_validate(value)
        value["hotkeys"]["enabled"] = False
        Config.model_validate(value)
        self.assertEqual(hotkeys.bindings(value), {})

    def test_dispatch_configure_disable_and_cycle_without_audio_hardware(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder, seed_reference=False)
            pipeline = Pipeline(store, providers=FakeProviders(), audio_factory=FakeAudio, hotkey_factory=hotkeys.NullHotkeys)
            pipeline.start_worker()
            try:
                value = store.config()
                value["hotkeys"]["enabled"] = True
                value["characters"] = [{"id": "one", "name": "角色一", "hotkey": "F13"}, {"id": "two", "name": "角色二"}]
                pipeline.update_config(Config.model_validate(value))
                self.assertTrue(pipeline.state()["hotkeys"]["character:one"]["ok"])
                for action, expected in (("previous_character", "two"), ("next_character", "one"),
                                         ("next_character", "two"), ("character:one", "one")):
                    pipeline.hotkey(action)
                    self.assertEqual(store.config()["active_character"], expected)
                pipeline.listen = Mock()
                pipeline.hotkey("toggle_listen")
                pipeline.listen.assert_called_once()
                pipeline.listening = True
                pipeline.audio.flush = Mock()
                pipeline.hotkey("flush")
                pipeline.audio.flush.assert_called_once()
                pipeline.audio.stop_playback = Mock()
                pipeline.hotkey("stop_playback")
                pipeline.audio.stop_playback.assert_called_once()
                pipeline.hotkey("toggle_listen")
                self.assertFalse(pipeline.listening)
                pipeline.hotkey("flush")
                self.assertIn("快捷键操作失败", pipeline.state()["logs"][-1]["message"])
                value = store.config()
                value["hotkeys"]["enabled"] = False
                pipeline.update_config(Config.model_validate(value))
                self.assertEqual(pipeline.state()["hotkeys"], {})
                self.assertEqual(pipeline.hotkeys.binding, {})
            finally:
                pipeline.close()

    @unittest.skipUnless(os.name == "nt", "需要 Windows RegisterHotKey")
    def test_windows_register_dispatch_conflict_reconfigure_and_release(self):
        called = threading.Event()
        seen = []
        def dispatch(action):
            seen.append(action)
            called.set()
        first, second = hotkeys.WindowsHotkeys(dispatch), hotkeys.WindowsHotkeys(dispatch)
        try:
            combination = "Ctrl+Alt+Shift+F23"
            self.assertTrue(first.configure({"test": combination})["test"]["ok"])
            status = second.configure({"test": combination})["test"]
            self.assertFalse(status["ok"])
            self.assertIn("占用", status["message"])
            self.assertTrue(first.post(hotkeys.WM_HOTKEY, 1))
            self.assertTrue(called.wait(2))
            self.assertEqual(seen, ["test"])
            first.configure({"other": "Ctrl+Alt+Shift+F24"})
            self.assertTrue(second.configure({"test": combination})["test"]["ok"])
            first.close()
            self.assertFalse(first.post(hotkeys.WM_HOTKEY, 1))
            self.assertTrue(second.configure({"other": "Ctrl+Alt+Shift+F24"})["other"]["ok"])
        finally:
            first.close()
            second.close()
