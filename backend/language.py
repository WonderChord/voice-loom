"""Output-language policy shared by validation, planning and TTS routing."""
from __future__ import annotations

from typing import Literal, get_args

TargetLanguage = Literal["original", "zh", "en", "ja", "ko", "yue", "zh_en"]
TARGET_LANGUAGES = get_args(TargetLanguage)
LABELS = {
    "original": "原语言", "zh": "普通话", "en": "英语", "ja": "日语",
    "ko": "韩语", "yue": "粤语", "zh_en": "中英混说",
}


def selected(config: dict, plan: dict | None = None) -> str:
    value = (plan or {}).get("target_language", config.get("target_language", "original"))
    if value not in TARGET_LANGUAGES:
        raise ValueError("目标语言须为原语言、普通话、英语、日语、韩语、粤语或中英混说")
    return value


def requires_llm(config: dict) -> bool:
    return bool(config.get("rewrite_enabled", False)) or selected(config) != "original"


def translation_rule(target: str) -> str:
    if target == "original":
        return ""
    rule = (
        f"目标语言固定为{LABELS[target]}（{target}）。source_text 保留输入原文；"
        "text 和全部 speech.text 使用目标语言，保持事实、数字、称呼、意图、第一人称和叙述视角。"
        "原文已符合目标语言时保留其表达。正文保持纯台词；目标语言说明写在规则中。"
    )
    if target == "yue":
        rule += "使用自然口语粤语措辞和粤语汉字，按粤语表达语气。"
    elif target == "zh_en":
        rule += "使用自然的中英混说，保留原有中英文切换；纯单语输入在合适的完整短句间切换中英文，每项信息只表达一次。"
    return rule


def speech_rule(target: str) -> str:
    if target == "original":
        return ""
    if target == "zh_en":
        return "按台词中的中英文自然切换发音，中文部分用普通话；完整朗读现有台词。"
    return f"请使用{LABELS[target]}自然发音，完整朗读现有台词。"
