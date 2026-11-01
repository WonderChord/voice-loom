"""角色卡与读音词典：替换规则、与合成链路的衔接、持久化与切换。"""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf
from pydantic import ValidationError

from backend import pronunciation, providers as p
from backend.models import Config
from backend.store import Store


def entry(text, say, **extra):
    return {"text": text, "say": say, "case_sensitive": False, "enabled": True, "providers": [], "note": "", **extra}


class PronunciationTests(unittest.TestCase):
    def test_longest_match_single_pass_and_case_rules(self):
        entries = [entry("VTuber", "V tuber"), entry("V", "微"), entry("声织", "声织（zhī）"),
                   entry("API", "A P I", case_sensitive=True)]
        self.assertEqual(pronunciation.apply("我是vtuber，用 V 和 API 还有 api，声织", entries),
                         "我是V tuber，用 微 和 A P I 还有 api，声织（zhī）")
        # 替换结果里出现的「V」不会被再次替换成「微」。
        self.assertEqual(pronunciation.apply("VTuber", entries), "V tuber")
        self.assertEqual(pronunciation.apply("没有词条", []), "没有词条")

    def test_character_entries_override_global_and_provider_scope(self):
        config = {
            "pronunciations": [entry("乃爱", "nǎi ài"), entry("Breeze专用", "x", providers=["breeze"]),
                               entry("停用", "y", enabled=False)],
            "characters": [{"id": "noa", "pronunciations": [entry("乃爱", "乃·爱")]}],
            "active_character": "noa",
        }
        lexicon = pronunciation.effective(config, "elevenlabs")
        self.assertEqual(pronunciation.apply("乃爱 Breeze专用 停用", lexicon), "乃·爱 Breeze专用 停用")
        self.assertEqual(pronunciation.apply("Breeze专用", pronunciation.effective(config, "breeze")), "x")
        config["active_character"] = ""
        self.assertEqual(pronunciation.apply("乃爱", pronunciation.effective(config, "breeze")), "nǎi ài")

    @patch.object(p, "_request")
    def test_tts_receives_replaced_text_but_plan_keeps_original(self, request):
        output = io.BytesIO()
        sf.write(output, np.ones(2400, dtype=np.float32) * .1, 24000, format="WAV")
        request.return_value = Mock(content=output.getvalue(), headers={"Content-Type": "audio/wav"})
        provider = {"id": "cloud-tts", "type": "openai_tts", "base_url": "https://tts.example.test",
                    "model": "gpt-4o-mini-tts", "voice": "alloy", "api_key": "k"}
        plan = {"source_text": "乃爱上线", "text": "乃爱上线", "warnings": [], "segments": [
            {"kind": "speech", "text": "乃爱上线", "emotion": "neutral", "intensity": 0, "delivery": "normal", "speed": 1}]}
        config = {"_human_confirmed": True, "pronunciations": [entry("乃爱", "奶爱")]}
        with tempfile.TemporaryDirectory() as directory:
            p.synthesize(provider, plan, None, Path(directory) / "out.wav", config)
        self.assertEqual(request.call_args.kwargs["json"]["input"], "奶爱上线")
        self.assertEqual(plan["segments"][0]["text"], "乃爱上线")


class CharacterConfigTests(unittest.TestCase):
    def test_characters_validate_and_persist(self):
        value = Config().model_dump()
        card = {"id": "noa", "name": "乃爱", "tts": "breeze", "persona": "我是乃爱。", "prompt": "保持冷淡。",
                "style_strength": "strong", "target_language": "ja", "pronunciations": [entry("乃爱", "Noa")]}
        value.update(characters=[card], active_character="noa", pronunciations=[entry("声织", "声织")])
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory, seed_reference=False)
            store.save_config(Config.model_validate(value))
            loaded = Store(directory, seed_reference=False).config()
        self.assertEqual(loaded["characters"][0]["pronunciations"][0]["say"], "Noa")
        self.assertEqual(loaded["characters"][0]["emotion_intensity"], 0.6)
        self.assertEqual(loaded["pronunciations"][0]["text"], "声织")
        with self.assertRaises(ValidationError):
            Config.model_validate({**value, "characters": [{**card, "tts": "no-such"}]})
        with self.assertRaises(ValidationError):
            Config.model_validate({**value, "active_character": "ghost"})
        with self.assertRaises(ValidationError):
            Config.model_validate({**value, "pronunciations": [entry("  ", "x")]})

    def test_config_without_new_fields_still_loads(self):
        legacy = Config().model_dump()
        for key in ("characters", "active_character", "pronunciations"):
            legacy.pop(key)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "config.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            loaded = Store(directory, seed_reference=False).config()
        self.assertEqual((loaded["characters"], loaded["active_character"], loaded["pronunciations"]), ([], "", []))


if __name__ == "__main__":
    unittest.main()
