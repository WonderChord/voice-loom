"""Model-specific direction rules and tag compilation, independent of transport.

Speech plans always contain plain dialogue. Only these compiler functions emit
the syntax consumed by a particular synthesis model.
"""
from __future__ import annotations

import copy

FISH_MODELS = {"drama-3", "s2-pro", "s2.1-pro", "s2.1-pro-free", "s1"}
ELEVEN_TAG_MODELS = {"eleven_v3", "eleven_v4", "eleven_v4_turbo"}

PROFILES = {
    "fish_drama3": {
        "title": "Fish Drama3 · 局部表演标签",
        "rules": "按台词的情绪张力与戏剧感自然编排情绪和声音动作。局部耳语拆为独立 speech 片段并设置 delivery=whisper，适配器会用成对 <whisper>…</whisper> 限定该片段。情绪写入 emotion/intensity，叹气、笑声、呼吸单列 event；适配器会转换为方括号方向。停顿单列 pause，速度写入 speed。text 和 speech.text 保持纯台词。",
        "examples": ["delivery=whisper → <whisper>这句话轻声说。</whisper>", "emotion=sad → [sad] 我想再试一次。", "event=sigh → [sighs]"],
    },
    "fish_s2": {
        "title": "Fish S2 · 方括号表演标签",
        "rules": "按台词情绪自然编排语气。emotion/intensity 表达情绪，delivery=whisper 表达耳语，event 单列叹气或笑声，pause 表达精确停顿，speed 表达语速。适配器分别编译 [sad]、[whispering]、[sighing]、[laughing]；text 和 speech.text 保持纯台词。",
        "examples": ["delivery=whisper → [whispering] 这句话轻声说。", "event=sigh → [sighing]"],
    },
    "fish_drama3_local": {
        "title": "本地 Drama3 · 局部表演标签",
        "rules": "按台词的情绪张力自然编排情绪与声音动作。局部耳语拆为独立 speech 片段并设置 delivery=whisper，适配器用成对 <whisper>…</whisper> 限定该片段。情绪写入 emotion/intensity，叹气和笑声单列 event，停顿单列 pause。当前本地实现使用默认语速，speed 保持 1；text 和 speech.text 保持纯台词。",
        "examples": ["delivery=whisper → <whisper>这句话轻声说。</whisper>", "emotion=sad → [sad] 我想再试一次。", "event=sigh → [sighs]"],
    },
    "eleven_audio_tags": {
        "title": "ElevenLabs · 音频标签",
        "rules": "按台词情绪与表现力编排情绪和声音动作。用 emotion/intensity、delivery=whisper、event 结构字段，由适配器转换为 [sad]、[whispers]、[sighs]、[laughs]、[exhales]。停顿单列 pause，speed 使用 0.7～1.2。text 和 speech.text 保持纯台词。",
        "examples": ["delivery=whisper → [whispers] 这句话轻声说。", "event=sigh → [sighs]"],
    },
    "breeze_instructions": {
        "title": "Breeze · 自然语言表演指令",
        "rules": "通过 emotion/intensity、delivery 和 speed 编排语气，适配器将其转换为独立 instruction。速度表示快慢意图，停顿使用 pause。text 和 speech.text 保持纯台词。",
        "examples": ["delivery=whisper → instruction: 用轻柔的耳语说话", "speed=0.8 → instruction: 用稍慢的自然语速说话"],
    },
    "openai_instructions": {
        "title": "OpenAI · 独立表演指令",
        "rules": "用 emotion/intensity 和 delivery 编排表演语气，适配器通过独立 instructions 字段传递。speed 调整语速，pause 安排停顿。text 和 speech.text 保持纯台词。",
        "examples": ["delivery=whisper → instructions: 用轻柔的耳语说话", "emotion=calm → instructions: 平静温和地说话"],
    },
    "plain": {
        "title": "纯文本 · 按引擎能力自动适配",
        "rules": "使用纯台词和富有表现力的标点节奏。默认 neutral/intensity=0、delivery=normal；支持速度的引擎可用 speed，停顿单列 pause。超出引擎原生支持的情绪或耳语会在合成时自动降级适配。",
        "examples": ["text: 这句话自然地说。", "停顿 → pause: duration_ms=300"],
    },
}

_INLINE_EMOTION_TAGS = {
    "happy": "[happy]",
    "sad": "[sad]",
    "angry": "[angry]",
    "calm": "[calm]",
    "thoughtful": "[thoughtful]",
    "excited": "[excited]",
}

_INSTRUCTION_EMOTIONS = {
    "happy": "带着开心和轻快的语气说话。",
    "sad": "带着难过和低落的语气说话。",
    "angry": "带着生气和坚定的语气说话。",
    "calm": "平静温和地说话。",
    "thoughtful": "带着思考和斟酌的语气说话。",
    "excited": "带着兴奋的语气说话。",
}

TAG_SPECS = {
    "fish_drama3": {
        "mechanism": "inline_tags",
        "whisper_tag": "<whisper>{text}</whisper>",
        "emotion_tags": dict(_INLINE_EMOTION_TAGS),
        "event_tags": {"sigh": "[sighs]", "laugh": "[laughs]", "breath": "[breath]"},
        "pause_syntax": "{\"kind\": \"pause\", \"duration_ms\": 1..10000}",
        "speed_range": [0.5, 2.0],
        "plan_usage": "在 plan.segments 中保持 speech.text 为纯台词，使用 delivery='whisper'、emotion、kind='event' 结构字段声明；适配器会自动编译为 <whisper>…</whisper>、[sad]、[sighs] 等 Drama 3 原生标签。",
    },
    "fish_drama3_local": {
        "mechanism": "inline_tags",
        "whisper_tag": "<whisper>{text}</whisper>",
        "emotion_tags": dict(_INLINE_EMOTION_TAGS),
        "event_tags": {"sigh": "[sighs]", "laugh": "[laughs]"},
        "pause_syntax": "{\"kind\": \"pause\", \"duration_ms\": 1..10000}",
        "speed_range": None,
        "plan_usage": "在 plan.segments 中保持 speech.text 为纯台词，使用 delivery='whisper'、emotion、kind='event' 结构字段声明；本地适配器自动编译为 <whisper>…</whisper>、[sad]、[sighs]、[laughs] 原生标签。",
    },
    "fish_s2": {
        "mechanism": "inline_tags",
        "whisper_tag": "[whispering] {text}",
        "emotion_tags": dict(_INLINE_EMOTION_TAGS),
        "event_tags": {"sigh": "[sighing]", "laugh": "[laughing]"},
        "pause_syntax": "{\"kind\": \"pause\", \"duration_ms\": 1..10000}",
        "speed_range": [0.5, 2.0],
        "plan_usage": "在 plan.segments 中使用 delivery='whisper'、emotion、kind='event' 结构字段，适配器自动编译为 [whispering]、[sighing]、[laughing] 等方括号标签。",
    },
    "eleven_audio_tags": {
        "mechanism": "inline_tags",
        "whisper_tag": "[whispers] {text}",
        "emotion_tags": dict(_INLINE_EMOTION_TAGS),
        "event_tags": {"sigh": "[sighs]", "laugh": "[laughs]", "breath": "[exhales]"},
        "pause_syntax": "{\"kind\": \"pause\", \"duration_ms\": 1..10000}",
        "speed_range": [0.7, 1.2],
        "plan_usage": "在 plan.segments 中使用结构字段，适配器自动编译为 [whispers]、[sighs]、[laughs]、[exhales] 等标签。",
    },
    "breeze_instructions": {
        "mechanism": "instruction_field",
        "whisper_tag": "instruction: 用轻柔的耳语说话，声音贴近、低声而清晰。",
        "emotion_tags": dict(_INSTRUCTION_EMOTIONS),
        "event_tags": {},
        "pause_syntax": "{\"kind\": \"pause\", \"duration_ms\": 1..10000}",
        "speed_range": [0.25, 4.0],
        "plan_usage": "通过独立 instruction 自然语言字段传递情绪、耳语和语速意图，speech.text 保持纯台词。",
    },
    "openai_instructions": {
        "mechanism": "instruction_field",
        "whisper_tag": "instructions: 用轻柔的耳语说话，声音贴近、低声而清晰。",
        "emotion_tags": dict(_INSTRUCTION_EMOTIONS),
        "event_tags": {},
        "pause_syntax": "{\"kind\": \"pause\", \"duration_ms\": 1..10000}",
        "speed_range": [0.25, 4.0],
        "plan_usage": "通过独立 instructions 字段传递情绪与耳语描述，speech.text 保持纯台词。",
    },
    "plain": {
        "mechanism": "reference_or_plain",
        "whisper_tag": None,
        "emotion_tags": {},
        "event_tags": {},
        "pause_syntax": "{\"kind\": \"pause\", \"duration_ms\": 1..10000}",
        "speed_range": [0.25, 4.0],
        "plan_usage": "仅发送纯文本与语速参数，音色与主语气由参考音频或基础音色决定。",
    },
}

STYLE_RULES = {
    "off": "关闭角色措辞润色：保留原有表达，仅修正明显识别错误和标点并拆分控制指令。语音表演仍按独立表演规则处理。",
    "light": "轻微润色：保留原句骨架，顺畅口头语并微调语气词与节奏，让表达自然贴近人设。",
    "balanced": "适中润色：调整句式、用词与节奏以鲜明呈现角色性格与情绪起伏，保留说话人的核心意图与第一人称视角。",
    "strong": "深度角色化润色：充分代入人设的说话风格、口癖、情绪张力与态度（包括毒舌、傲娇、狂气、慵懒或俏皮等鲜明个性），禁止把鲜明人设磨平为温吞礼貌的客服腔；保留说话人的核心意图与第一人称视角，不以助手身份代答。",
}


def model_id(provider: dict) -> str:
    model = str(provider.get("model") or "").strip()
    return model or {"fish_tts": "drama-3", "drama3_local": "drama-3", "elevenlabs": "eleven_v3"}.get(provider.get("type"), "")


def profile_id(provider: dict) -> str:
    kind, model = provider.get("type"), model_id(provider)
    if kind == "drama3_local":
        return "fish_drama3_local"
    if kind == "fish_tts":
        if model == "drama-3":
            return "fish_drama3"
        if model in {"s2-pro", "s2.1-pro", "s2.1-pro-free"}:
            return "fish_s2"
    if kind == "elevenlabs" and model in ELEVEN_TAG_MODELS:
        return "eleven_audio_tags"
    if kind == "breeze":
        return "breeze_instructions"
    if kind == "openai_tts" and provider.get("instructions", True) and model not in {"tts-1", "tts-1-hd"}:
        return "openai_instructions"
    return "plain"


def describe_rules(provider: dict) -> dict:
    selected = profile_id(provider)
    profile = PROFILES[selected]
    key = str(provider.get("model") or "").strip() or "__default__"
    overrides = provider.get("performance_rules") or {}
    override = overrides.get(key, "") if isinstance(overrides, dict) else ""
    override = override.strip() if isinstance(override, str) else ""
    spec = copy.deepcopy(TAG_SPECS[selected])
    return {"rule_profile": selected, "rule_title": profile["title"], "rule_key": key,
            "default_rules": profile["rules"], "effective_rules": override or profile["rules"],
            "rules_customized": bool(override), "syntax_examples": list(profile["examples"]),
            "tag_spec": spec}


def is_tagged(provider: dict) -> bool:
    return profile_id(provider) in {"fish_drama3", "fish_drama3_local", "fish_s2", "eleven_audio_tags"}


def compile_speech(provider: dict, text: str, emotion: str, intensity: float, delivery: str) -> str:
    profile = profile_id(provider)
    if not is_tagged(provider):
        return text
    prefix = f"[{emotion}] " if emotion != "neutral" and intensity > 0 else ""
    if delivery == "whisper":
        if profile in {"fish_drama3", "fish_drama3_local"}:
            return prefix + "<whisper>" + text + "</whisper>"
        prefix += "[whispers] " if profile == "eleven_audio_tags" else "[whispering] "
    return prefix + text


def compile_event(provider: dict, event: str) -> str:
    mappings = {
        "fish_drama3": {"sigh": "[sighs]", "laugh": "[laughs]", "breath": "[breath]"},
        "fish_drama3_local": {"sigh": "[sighs]", "laugh": "[laughs]"},
        "fish_s2": {"sigh": "[sighing]", "laugh": "[laughing]"},
        "eleven_audio_tags": {"sigh": "[sighs]", "laugh": "[laughs]", "breath": "[exhales]"},
    }
    return mappings.get(profile_id(provider), {}).get(event, "")
