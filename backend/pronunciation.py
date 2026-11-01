"""读音词典：合成前把专有名词替换成 TTS 读得准的写法；字幕与记录保留原文。"""
from __future__ import annotations

import re


def effective(config: dict, provider_id: str) -> list[dict]:
    """全局词条叠加当前角色卡词条（同一原词以角色卡为准），只保留对该 TTS 生效的启用词条。"""
    merged: dict[str, dict] = {}
    cards = {card["id"]: card for card in config.get("characters") or []}
    character = cards.get(config.get("active_character") or "")
    for entry in [*(config.get("pronunciations") or []), *((character or {}).get("pronunciations") or [])]:
        if not entry.get("enabled", True) or not entry.get("text"):
            continue
        if entry.get("providers") and provider_id not in entry["providers"]:
            continue
        merged[entry["text"] if entry.get("case_sensitive") else entry["text"].casefold()] = entry
    return list(merged.values())


def apply(text: str, entries: list[dict]) -> str:
    """单次扫描、长词优先，替换结果不会再被其他词条二次替换。"""
    if not entries or not text:
        return text
    exact = {e["text"]: e["say"] for e in entries if e.get("case_sensitive")}
    folded = {e["text"].casefold(): e["say"] for e in entries if not e.get("case_sensitive")}
    keys = sorted({*exact, *(e["text"] for e in entries if not e.get("case_sensitive"))}, key=len, reverse=True)
    pattern = re.compile("|".join(
        re.escape(key) if key in exact else "(?i:" + re.escape(key) + ")" for key in keys))

    def replace(match: re.Match) -> str:
        found = match.group(0)
        return exact[found] if found in exact else folded.get(found.casefold(), found)

    return pattern.sub(replace, text)
