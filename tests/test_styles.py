"""角色风格预设 schema 与真实 JSON 持久化；不启动模型或音频设备。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from backend.models import Config, StylePreset, default_style_presets
from backend.store import Store


class StylePresetTests(unittest.TestCase):
    def test_defaults_are_independent_and_preserve_live_prompts(self):
        first = Config()
        second = Config()
        self.assertEqual([preset.name for preset in first.style_presets], ["原意直通", "轻度整理", "自然角色", "鲜明角色"])
        self.assertEqual([preset.strength for preset in first.style_presets], ["off", "light", "balanced", "strong"])
        self.assertEqual(first.active_style_preset, "")
        self.assertEqual(first.style_strength, "light")
        first.style_presets[0].name = "我的直通"
        self.assertEqual(second.style_presets[0].name, "原意直通")
        original = (second.persona, second.prompt, second.style_strength)
        second.active_style_preset = "style-strong"
        self.assertEqual((second.persona, second.prompt, second.style_strength), original)

    def test_custom_preset_and_overwrite_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory, seed_reference=False)
            value = store.config()
            custom = StylePreset(id="my-character", name="我的角色", persona="我是温和而坚定的录音人。", prompt="保留我原话中的事实，以清楚的第一人称表达。", strength="balanced").model_dump()
            value["style_presets"].append(custom)
            value.update(active_style_preset=custom["id"], style_strength=custom["strength"], persona=custom["persona"], prompt=custom["prompt"])
            store.save_config(Config.model_validate(value))
            loaded = Store(directory, seed_reference=False).config()
            self.assertEqual(loaded["style_presets"][-1], custom)
            self.assertEqual(loaded["active_style_preset"], "my-character")
            self.assertEqual(loaded["persona"], custom["persona"])
            self.assertEqual(loaded["prompt"], custom["prompt"])
            self.assertEqual(loaded["style_strength"], "balanced")
            loaded["style_presets"][-1].update(name="角色修订", prompt="仅整理措辞，保留原意。", strength="light")
            store.save_config(Config.model_validate(loaded))
            again = Store(directory, seed_reference=False).config()
            self.assertEqual(again["style_presets"][-1]["name"], "角色修订")
            self.assertEqual(again["style_presets"][-1]["strength"], "light")
            # Editing a saved template does not implicitly replace the live fields.
            self.assertEqual(again["prompt"], custom["prompt"])

    def test_legacy_config_gains_defaults_without_replacing_prompts(self):
        legacy = Config().model_dump()
        for key in ("style_presets", "active_style_preset", "style_strength"):
            legacy.pop(key)
        legacy.update(persona="原来的人设内容", prompt="原来的重构规则", speech_prompt="原来的语音表演规则", rewrite_enabled=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            restored = Store(directory, seed_reference=False).config()
            self.assertEqual(restored["persona"], legacy["persona"])
            self.assertEqual(restored["prompt"], legacy["prompt"])
            self.assertEqual(restored["speech_prompt"], legacy["speech_prompt"])
            self.assertTrue(restored["rewrite_enabled"])
            self.assertEqual(len(restored["style_presets"]), 4)
            self.assertEqual(restored["active_style_preset"], "")
            self.assertEqual(restored["style_strength"], "light")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["style_presets"], restored["style_presets"])
            self.assertEqual(Store(directory, seed_reference=False).config()["prompt"], legacy["prompt"])

    def test_newer_config_fields_are_ignored_instead_of_blocking_startup(self):
        # 新版写入的字段被旧版读到时不应让后台直接退出。
        newer = Config().model_dump()
        newer.update(future_switch=True, persona="保留的人设")
        newer["active"]["future_role"] = "x"
        newer["audio"]["future_gain"] = 3
        newer["providers"][0]["future_option"] = 1
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config.json").write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")
            restored = Store(directory, seed_reference=False).config()
            self.assertEqual(restored["persona"], "保留的人设")
            self.assertNotIn("future_switch", restored)
            self.assertNotIn("future_role", restored["active"])
            self.assertNotIn("future_gain", restored["audio"])
            self.assertNotIn("future_option", restored["providers"][0])

    def test_duplicate_id_missing_active_and_invalid_strength_are_rejected(self):
        base = Config().model_dump()
        duplicate = copy.deepcopy(base)
        duplicate["style_presets"].append(copy.deepcopy(duplicate["style_presets"][0]))
        missing = {**base, "active_style_preset": "missing"}
        invalid_config_strength = {**base, "style_strength": "extreme"}
        invalid_preset_strength = copy.deepcopy(base)
        invalid_preset_strength["style_presets"][0]["strength"] = "extreme"
        for value in (duplicate, missing, invalid_config_strength, invalid_preset_strength):
            with self.subTest(value=value.get("active_style_preset")), self.assertRaises(ValidationError):
                Config.model_validate(value)

    def test_size_limits_and_empty_selection(self):
        base = Config().model_dump()
        sample = default_style_presets()[0].model_dump()
        too_many = {**base, "style_presets": [{**sample, "id": f"style-{index}"} for index in range(41)]}
        with self.assertRaises(ValidationError):
            Config.model_validate(too_many)
        for key, value in (("name", " "), ("name", "字" * 101), ("persona", "字" * 12001), ("prompt", "字" * 16001), ("id", "含空格 ID")):
            with self.subTest(field=key), self.assertRaises(ValidationError):
                StylePreset.model_validate({**sample, key: value})
        empty = Config.model_validate({**base, "style_presets": [], "active_style_preset": ""})
        self.assertEqual(empty.style_presets, [])


if __name__ == "__main__":
    unittest.main()
