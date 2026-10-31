"""HTTP 边界和持久化的校验模型。"""
from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .language import TargetLanguage


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class Provider(Model):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    kind: Literal["asr", "llm", "tts"]
    type: Literal["qwen_local", "sensevoice", "openai_asr", "openai_llm", "breeze", "elevenlabs", "openai_tts", "gptsovits", "fish_tts", "drama3_local"]
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(default="", max_length=2048)
    model: str = Field(default="", max_length=250)
    voice: str = Field(default="", max_length=250)
    api_key: str = Field(default="", max_length=4096, repr=False)
    has_key: bool = False
    clear_key: bool = False
    response_mode: Literal["json_object", "json_schema", "text"] = "json_object"
    instructions: bool = True
    tag_style: Literal["none", "eleven"] = "none"
    options: dict[str, Any] = Field(default_factory=dict)
    performance_rules: dict[str, str] = Field(default_factory=dict, max_length=40)

    @field_validator("performance_rules")
    @classmethod
    def valid_performance_rules(cls, rules):
        if any(not k.strip() or len(k) > 250 or len(v) > 16000 for k, v in rules.items()):
            raise ValueError("语气规则的模型名须为1～250字，每条规则最多16000字")
        return rules

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value:
            url = urlsplit(value)
            if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("服务地址须为 http/https 地址，密钥请填入密钥栏")
        return value

    @model_validator(mode="after")
    def kind_matches(self):
        kinds = {"qwen_local": "asr", "sensevoice": "asr", "openai_asr": "asr", "openai_llm": "llm", "breeze": "tts", "elevenlabs": "tts", "openai_tts": "tts", "gptsovits": "tts", "fish_tts": "tts", "drama3_local": "tts"}
        if kinds[self.type] != self.kind:
            raise ValueError("适配器类型与 ASR/LLM/TTS 分类不符")
        return self


class Active(Model):
    asr: str = "qwen"
    llm: str = "cloud-llm"
    tts: str = "breeze"
    reference: str = ""


class AudioConfig(Model):
    input_device: int | None = Field(default=None, ge=0)
    output_device: int | None = Field(default=None, ge=0)
    pause_ms: int = Field(default=650, ge=300, le=2500)
    min_db: float = Field(default=-45, ge=-65, le=-20)
    max_seconds: int = Field(default=20, ge=5, le=60)
    skip_low_energy: bool = True
    energy_threshold_db: float = Field(default=-45, ge=-80, le=-10)
    min_active_ms: int = Field(default=120, ge=20, le=1000)
    suppress_during_playback: bool = True
    auto_play: bool = True


def default_providers() -> list[Provider]:
    values = [
        ("qwen", "asr", "qwen_local", "Qwen3-ASR · 本机", "http://127.0.0.1:18081", "", ""),
        ("sensevoice", "asr", "sensevoice", "SenseVoice · 本机", "", "", ""),
        ("cloud-asr", "asr", "openai_asr", "云端 ASR · OpenAI 兼容", "https://api.openai.com/v1", "gpt-4o-mini-transcribe", ""),
        ("cloud-llm", "llm", "openai_llm", "云端 LLM · OpenAI 兼容", "https://api.openai.com/v1", "gpt-4.1-mini", ""),
        ("breeze", "tts", "breeze", "Breeze · 本机", "http://127.0.0.1:7871", "", ""),
        ("elevenlabs", "tts", "elevenlabs", "ElevenLabs · 云端", "https://api.elevenlabs.io/v1", "eleven_v3", ""),
        ("cloud-tts", "tts", "openai_tts", "云端 TTS · OpenAI 兼容", "https://api.openai.com/v1", "gpt-4o-mini-tts", "alloy"),
        ("gptsovits", "tts", "gptsovits", "GPT-SoVITS · 本机", "http://127.0.0.1:9880", "", ""),
        ("drama3", "tts", "fish_tts", "Drama 3 · Fish Audio", "https://api.fish.audio", "drama-3", ""),
        ("drama3-local", "tts", "drama3_local", "Drama 3 · Q4 专家版", "http://127.0.0.1:17863", "drama-3", ""),
        ("drama3-full", "tts", "drama3_local", "Drama 3 · 原版权重", "http://127.0.0.1:17865", "drama-3", ""),
        ("gptsovits-v5turbo", "tts", "gptsovits", "GPT-SoVITS V5 Turbo · 本机", "http://127.0.0.1:17864", "v5turbo", ""),
        ("gptsovits-v5dev", "tts", "gptsovits", "GPT-SoVITS V5 Dev · 本机", "http://127.0.0.1:17864", "v5dev", ""),
    ]
    providers = [Provider(id=i, kind=k, type=t, name=n, base_url=u, model=m, voice=v) for i, k, t, n, u, m, v in values]
    providers[4].options = {"cfg_scale": 2.0, "seed": 42}
    providers[5].tag_style = "eleven"
    return providers


class Appearance(Model):
    accent: str = Field(default="#79d9bc", pattern=r"^#[0-9a-fA-F]{6}$")
    mode: Literal["dark", "light"] = "dark"


class SubtitleConfig(Model):
    enabled: bool = True
    display_seconds: int = Field(default=8, ge=2, le=60)


class StylePreset(Model):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    persona: str = Field(max_length=12000)
    prompt: str = Field(max_length=16000)
    strength: Literal["off", "light", "balanced", "strong"]

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("角色风格预设名称不能为空")
        return value.strip()


def default_style_presets() -> list[StylePreset]:
    return [
        StylePreset(
            id="style-off", name="原意直通", strength="off",
            persona="我是说话人本人，保持我原本的表达身份、第一人称视角和说话习惯。",
            prompt="逐句保留我的原话，只整理必要的标点和明显的转写错字，拆分语音控制指令，直接输出我的表达。",
        ),
        StylePreset(
            id="style-light", name="轻度整理", strength="light",
            persona="我保持自然鲜活的口语表达，保留第一人称视角与原本的态度。",
            prompt="顺畅口头表达中的无意义重复与碎词，保留原有的语气、态度、称呼和核心意思，不改成生硬书面语或客套腔。",
        ),
        StylePreset(
            id="style-balanced", name="自然角色", strength="balanced",
            persona="我说话有鲜明的个人气质与情绪起伏，顺着人设的说话习惯自然表达，不做作、不端着。",
            prompt="贴合人设调整句式、语气词与节奏，自然带出角色的性格色彩与情绪张力，保留说话人的第一人称视角与核心意图。",
        ),
        StylePreset(
            id="style-strong", name="鲜明角色", strength="strong",
            persona="我个性极具辨识度，说话带有人设专属的口癖、锋芒与戏剧张力，情绪直率鲜活，绝不温吞客套。我始终以第一人称表达自己的想法。",
            prompt="深度代入角色人设重写台词，大胆强化符合人设的口癖、句式节奏、攻击性或俏皮感，禁止将个性磨平为礼貌客服腔；始终保持说话人第一人称视角与核心意图，不代替听者回答。",
        ),
    ]


class Config(Model):
    providers: list[Provider] = Field(default_factory=default_providers, min_length=1, max_length=40)
    active: Active = Field(default_factory=Active)
    default_asr: str = "qwen"
    persona: str = Field(default="我说话自然鲜活、有个人风格，使用第一人称视角表达自己的想法与态度。", max_length=12000)
    prompt: str = Field(default="顺畅整理口头表达，贴合人设语气与节奏，保留核心意思与第一人称视角。", max_length=16000)
    speech_prompt: str = Field(default="按台词情绪与戏剧张力自然编排语气、耳语、停顿与叹气/轻笑等声音表现。", max_length=16000)
    context_limit: int = Field(default=6, ge=0, le=50)
    rewrite_enabled: bool = False
    target_language: TargetLanguage = "original"
    allow_events: bool = True
    emotion_intensity: float = Field(default=0.6, ge=0, le=1)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    appearance: Appearance = Field(default_factory=Appearance)
    subtitles: SubtitleConfig = Field(default_factory=SubtitleConfig)
    style_presets: list[StylePreset] = Field(default_factory=default_style_presets, max_length=40)
    active_style_preset: str = Field(default="", max_length=80)
    style_strength: Literal["off", "light", "balanced", "strong"] = "light"

    @model_validator(mode="after")
    def valid_selection(self):
        lookup = {p.id: p for p in self.providers}
        if len(lookup) != len(self.providers):
            raise ValueError("服务 ID 不能重复")
        for kind in ("asr", "llm", "tts"):
            selected = getattr(self.active, kind)
            if selected not in lookup or lookup[selected].kind != kind:
                raise ValueError(f"当前 {kind.upper()} 未指向对应服务")
        if self.default_asr not in lookup or lookup[self.default_asr].kind != "asr":
            raise ValueError("默认 ASR 未指向 ASR 服务")
        return self

    @model_validator(mode="after")
    def valid_style_selection(self):
        ids = {preset.id for preset in self.style_presets}
        if len(ids) != len(self.style_presets):
            raise ValueError("角色风格预设 ID 不能重复")
        if self.active_style_preset and self.active_style_preset not in ids:
            raise ValueError("当前角色风格预设不存在，请重新选择")
        return self


class Segment(Model):
    kind: Literal["speech", "event", "pause"]
    text: str | None = Field(default=None, max_length=12000)
    emotion: Literal["neutral", "happy", "sad", "angry", "calm", "thoughtful", "excited"] | None = None
    intensity: float | None = Field(default=None, ge=0, le=1)
    event: Literal["sigh", "laugh", "breath"] | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=10000)
    delivery: Literal["normal", "whisper"] = "normal"
    speed: float = Field(default=1, ge=0.5, le=2)

    @model_validator(mode="after")
    def required_content(self):
        if self.kind == "speech" and not (self.text or "").strip():
            raise ValueError("语音段文字不能为空")
        if self.kind == "event" and not self.event:
            raise ValueError("声音事件须选择 sigh/laugh/breath")
        if self.kind == "pause" and (self.duration_ms is None or self.duration_ms < 1):
            raise ValueError("停顿时长须大于 0")
        return self


class Audit(Model):
    passed: bool = True
    message: str = Field(default="", max_length=2000)


class Plan(Model):
    source_text: str = Field(max_length=12000)
    text: str = Field(max_length=12000)
    target_language: TargetLanguage = "original"
    segments: list[Segment] = Field(max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    directives: list[str] = Field(default_factory=list, max_length=100)
    needs_review: bool = False
    audit: Audit = Field(default_factory=Audit)

    @model_validator(mode="after")
    def valid_content(self):
        if not self.segments and self.directives:
            if self.text.strip():
                raise ValueError("纯控制指令的朗读文字应为空")
            return self
        if not any(s.kind in {"speech", "event"} for s in self.segments):
            raise ValueError("计划至少需要一段语音或声音事件")
        spoken = "".join(s.text or "" for s in self.segments if s.kind == "speech")
        if "".join(spoken.split()) != "".join(self.text.split()):
            raise ValueError("朗读文字须与语音片段一致")
        if sum(len(s.text or "") for s in self.segments) > 12000:
            raise ValueError("计划文字总长超过 12000 字")
        return self


class ReferencePatch(Model):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    transcript: str | None = Field(default=None, max_length=12000)
    language: str | None = Field(default=None, min_length=1, max_length=30)
    tags: list[str] | None = Field(default=None, max_length=20)
    pinned: bool | None = None
    favorite: bool | None = None
    provider_voice_ids: dict[str, str] | None = None

    @field_validator("tags")
    @classmethod
    def tags_size(cls, tags):
        if tags is not None and any(len(t) > 50 for t in tags):
            raise ValueError("单个标签最多 50 字")
        return tags


class TextRequest(Model):
    text: str = Field(min_length=1, max_length=12000)
    preview_only: bool = False
    play_audio: bool | None = None

    @field_validator("text")
    @classmethod
    def nonblank(cls, text):
        if not text.strip():
            raise ValueError("请输入文字")
        return text.strip()


class SynthesizeRequest(Model):
    plan: Plan
    play_audio: bool | None = None
