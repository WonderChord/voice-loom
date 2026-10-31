export type Kind = "asr" | "llm" | "tts";
export interface Provider {
  id: string;
  kind: Kind;
  type: string;
  name: string;
  base_url: string;
  model: string;
  voice: string;
  api_key?: string;
  has_key?: boolean;
  clear_key?: boolean;
  response_mode: string;
  instructions: boolean;
  tag_style: string;
  options: Record<string, unknown>;
  performance_rules: Record<string, string>;
}
export type StyleStrength = "off" | "light" | "balanced" | "strong";
export type TargetLanguage = "original" | "zh" | "en" | "ja" | "ko" | "yue" | "zh_en";
export const targetLanguageNames: Record<TargetLanguage, string> = {
  original: "保持原语言",
  zh: "中文普通话",
  en: "英语",
  ja: "日语",
  ko: "韩语",
  yue: "粤语",
  zh_en: "中英混说",
};
export function processingModeLabel(targetLanguage: TargetLanguage, rewriteEnabled: boolean) {
  if (targetLanguage !== "original") return rewriteEnabled ? "翻译+润色" : "仅翻译";
  return rewriteEnabled ? "角色润色" : "原文直通";
}
export function llmConnectionIssue(provider?: Provider) {
  if (!provider) return "请先在模型连接中选择并配置 LLM。";
  if (!provider.base_url.trim() || !provider.model.trim())
    return "请先在模型连接中补全 LLM 地址和模型。";
  let local = false;
  try {
    const hostname = new URL(provider.base_url).hostname;
    local = hostname === "localhost" || hostname === "[::1]" || /^127\./.test(hostname);
  } catch {
    return "请在模型连接中检查 LLM 服务地址。";
  }
  const hasKey = !!provider.api_key?.trim() || (!!provider.has_key && !provider.clear_key);
  if (!local && !hasKey) return "当前远程 LLM 尚未配置密钥，请前往模型连接设置。";
  return "";
}
export interface StylePreset {
  id: string;
  name: string;
  persona: string;
  prompt: string;
  strength: StyleStrength;
}
export interface Config {
  providers: Provider[];
  active: { asr: string; llm: string; audit_llm?: string; tts: string; reference: string };
  default_asr: string;
  persona: string;
  prompt: string;
  speech_prompt: string;
  style_presets: StylePreset[];
  active_style_preset: string;
  style_strength: StyleStrength;
  context_limit: number;
  target_language: TargetLanguage;
  rewrite_enabled: boolean;
  audit_enabled?: boolean;
  allow_events: boolean;
  emotion_intensity: number;
  appearance: { accent: string; mode: "dark" | "light" };
  subtitles: { enabled: boolean; display_seconds: number };
  audio: {
    input_device: number | null;
    output_device: number | null;
    pause_ms: number;
    min_db: number;
    max_seconds: number;
    suppress_during_playback: boolean;
    auto_play: boolean;
    skip_low_energy: boolean;
    energy_threshold_db: number;
    min_active_ms: number;
  };
}
export interface SubtitleState {
  event_id: string;
  event: "publish" | "clear";
  status: "approved" | "cleared" | "disabled";
  record_id: string | null;
  text: string;
  created_at: string;
  expires_at: string | null;
  reason: string;
  enabled: boolean;
  text_file: string;
  file_error?: string | null;
}
export interface Voice {
  id: string;
  name: string;
  transcript: string;
  language: string;
  tags: string[];
  pinned: boolean;
  favorite: boolean;
  duration: number;
  audio_url: string;
  provider_voice_ids: Record<string, string>;
}
export interface Segment {
  kind: "speech" | "event" | "pause";
  text?: string;
  emotion?: string;
  intensity?: number;
  event?: string;
  duration_ms?: number;
  delivery?: "normal" | "whisper";
  speed?: number;
}
export interface Plan {
  source_text: string;
  target_language?: TargetLanguage;
  text: string;
  segments: Segment[];
  warnings: string[];
  directives?: string[];
  needs_review?: boolean;
  audit?: { passed: boolean; message: string };
}
export interface TagSpec {
  mechanism: string;
  whisper_tag: string | null;
  emotion_tags: Record<string, string>;
  event_tags: Record<string, string>;
  pause_syntax: string;
  speed_range: [number, number] | null;
  plan_usage: string;
}
export interface Capabilities {
  emotions: string[];
  events: string[];
  whisper: boolean;
  speed: boolean;
  reference_audio: boolean;
  instructions: boolean;
  notes: string[];
  rule_profile: string;
  rule_title: string;
  rule_key: string;
  default_rules: string;
  effective_rules: string;
  syntax_examples: string[];
  tag_spec?: TagSpec;
}
export interface Entry {
  id: string;
  created_at: string;
  status: string;
  source_text: string;
  target_language?: TargetLanguage;
  text: string;
  plan: Plan | null;
  audio_url: string | null;
  error: string | null;
  elapsed_ms: number;
  origin: string;
  skip_reason?: string;
  subtitle_event_id?: string;
  subtitle_status?: string;
}
export interface Runtime {
  listening: boolean;
  playing: boolean;
  stage: string;
  level_db: number;
  queue_size: number;
  records_revision?: number;
  logs_revision?: number;
  records: Entry[];
  logs: { time: string; level: string; message: string }[];
  last_error: string | null;
  uptime_s: number;
  skipped_audio_count?: number;
  last_skip_reason?: string | null;
}
export interface Device {
  id: number;
  name: string;
  hostapi: string;
  sample_rate: number;
}
export interface Devices {
  inputs: Device[];
  outputs: Device[];
  default_input: number;
  default_output: number;
}
export const emotionNames: Record<string, string> = {
  neutral: "自然",
  happy: "开心",
  sad: "低落",
  angry: "生气",
  calm: "平静",
  thoughtful: "思考",
  excited: "兴奋",
};
export const eventNames: Record<string, string> = {
  sigh: "轻叹气",
  laugh: "轻笑",
  breath: "呼吸",
};
export const statusNames: Record<string, string> = {
  queued: "排队中",
  asr: "正在转写",
  llm: "文字处理与自检",
  audit: "正在自检",
  tts: "正在合成",
  playing: "播放中",
  done: "已完成",
  error: "失败",
  review: "待确认",
  cancelled: "已取消",
  skipped: "已跳过 · 低能量",
  idle: "就绪",
  listening: "监听中",
};
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const r = await fetch("/api" + path, { ...options, headers, credentials: "same-origin" });
  if (!r.ok) {
    const data = await r.json().catch(() => ({ detail: `请求失败 (${r.status})` }));
    const detail = data.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((x: { msg: string; loc: string[] }) => `${x.loc?.slice(1).join(".")}：${x.msg}`)
              .join("；")
          : data.message || `请求失败 (${r.status})`,
    );
  }
  return r.status === 204 ? (undefined as T) : r.json();
}
