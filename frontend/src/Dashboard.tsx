import { useEffect, useRef, useState } from "react";
import {
  Mic,
  ChevronRight,
  ArrowUpRight,
  Play,
  FileAudio,
  Send,
  PenLine,
  Download,
  Headphones,
  AudioLines,
  ArrowRight,
  Sparkles,
  Volume2,
  AlignLeft,
  Scissors,
  ShieldCheck,
  ShieldAlert,
  LoaderCircle,
  Captions,
} from "lucide-react";
import {
  api,
  type Config,
  type Runtime,
  type Voice,
  type Devices,
  type Entry,
  type Capabilities,
  type TargetLanguage,
  emotionNames,
  eventNames,
  statusNames,
  targetLanguageNames,
  processingModeLabel,
  llmConnectionIssue,
} from "./types";
import { Field, Toggle, AudioPreview } from "./components";
type Props = {
  config: Config;
  runtime: Runtime;
  voices: Voice[];
  devices: Devices;
  busy: boolean;
  navigate: (p: string) => void;
  quick: (p: Partial<Config>) => void;
  onEdit: (e: Entry) => void;
  onPlay: (e: Entry) => void;
  onRun: (f: () => Promise<unknown>) => void;
  notify: (s: string, error?: boolean) => void;
  save: () => Promise<Config>;
  dirty: boolean;
};
export function EntryCard({
  entry,
  onEdit,
  onPlay,
}: {
  entry: Entry;
  onEdit: (e: Entry) => void;
  onPlay: (e: Entry) => void;
}) {
  const active = ["asr", "llm", "audit", "tts", "playing", "queued"].includes(entry.status);
  const auditPending = entry.status === "audit" || (entry.status === "llm" && !!entry.plan);
  const approved = !auditPending && entry.plan?.audit?.passed && !entry.plan?.needs_review;
  const targetLanguage = entry.target_language ?? entry.plan?.target_language ?? "original";
  const translated = targetLanguage !== "original";
  if (entry.status === "skipped")
    return (
      <article className="entry entry-skipped">
        <div className="entry-meta">
          <time>{new Date(entry.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</time>
          <span className="status status-skipped">已跳过 · PASS</span>
          <span className="entry-origin">{entry.origin === "mic" ? "麦克风" : "音频文件"}</span>
        </div>
        <p>{entry.skip_reason || entry.error || "有效声音能量不足，已在 ASR 前跳过。"}</p>
        <small className="muted">本段已在转写前跳过。</small>
      </article>
    );
  return (
    <article className={"entry " + (active ? "processing" : "")}>
      <div className="entry-meta">
        <time>
          {new Date(entry.created_at).toLocaleTimeString("zh-CN", {
            hour12: false,
          })}
        </time>
        <span className={"status status-" + entry.status}>
          {entry.status === "llm" && translated
            ? "翻译与自检"
            : statusNames[entry.status] || entry.status}
        </span>
        {(entry.target_language || entry.plan?.target_language) && (
          <span className="chip">目标：{targetLanguageNames[targetLanguage]}</span>
        )}
        {entry.elapsed_ms > 0 && <span>{(entry.elapsed_ms / 1000).toFixed(1)}s</span>}
        <span className="entry-origin">
          {entry.origin === "mic" ? "麦克风" : entry.origin === "file" ? "音频文件" : "文字测试"}
        </span>
      </div>
      <div className="entry-columns">
        <div className="source-copy">
          <span className="eyebrow">
            原文 <small>{entry.origin === "mic" || entry.origin === "file" ? "ASR" : "TEXT"}</small>
          </span>
          <p>{entry.source_text || (active ? "正在识别这段声音…" : "—")}</p>
        </div>
        <ArrowRight className="entry-arrow" size={15} />
        <div className="output-copy">
          <span className="eyebrow">
            {translated
              ? approved
                ? "自检后的译文"
                : "候选译文"
              : approved
                ? "自检后的台词"
                : "候选表达"}{" "}
            <small>{approved ? "APPROVED" : "DRAFT"}</small>
          </span>
          <p>
            {entry.text ||
              (active
                ? "等待流水线处理…"
                : entry.plan?.directives?.length
                  ? "已提取表演指令，本条没有朗读台词。"
                  : "尚无输出")}
          </p>
          <div className="chips">
            {entry.plan?.segments.map((s, i) =>
              s.kind === "speech" ? (
                s.emotion && s.emotion !== "neutral" ? (
                  <span key={i} className="chip emotion">
                    {emotionNames[s.emotion] || s.emotion}
                  </span>
                ) : null
              ) : (
                <span key={i} className={"chip " + (s.kind === "event" ? "event" : "pause")}>
                  {s.kind === "event"
                    ? eventNames[s.event || ""] || s.event
                    : `停顿 ${s.duration_ms}ms`}
                </span>
              ),
            )}
          </div>
        </div>
      </div>
      {entry.plan?.segments
        .filter(
          (s) => s.kind === "speech" && (s.delivery === "whisper" || (s.speed && s.speed !== 1)),
        )
        .map((s, i) => (
          <div className="chips" key={i}>
            {s.delivery === "whisper" && <span className="chip event">耳语</span>}
            {s.speed && s.speed !== 1 ? <span className="chip pause">语速 {s.speed}×</span> : null}
          </div>
        ))}
      {entry.error && <div className="entry-error">{entry.error}</div>}
      {entry.plan?.warnings?.length ? (
        <div className="entry-warning">{entry.plan.warnings.join(" · ")}</div>
      ) : null}
      <div className="entry-directives">
        {entry.plan?.directives?.length ? (
          <span>表演指令（不朗读）：{entry.plan.directives.join(" · ")}</span>
        ) : null}
      </div>
      {entry.plan && (
        <div className={"entry-audit " + (auditPending ? "pending" : approved ? "passed" : "held")}>
          {auditPending ? (
            <LoaderCircle size={18} className="spin" />
          ) : approved ? (
            <ShieldCheck size={18} />
          ) : (
            <ShieldAlert size={18} />
          )}
          <div>
            <strong>{auditPending ? "正在自检" : approved ? "自检通过" : "自检待确认"}</strong>
            <p>
              {auditPending
                ? translated
                  ? "正在核对译文原意、目标语言、称呼和人称，自检通过后输出字幕。"
                  : "正在核对原意、角色措辞和控制指令，自检通过后输出字幕。"
                : entry.plan.audit?.message || "等待确认当前候选稿。"}
            </p>
          </div>
          {approved && entry.subtitle_event_id && (
            <span className="chip">
              <Captions size={12} /> 已发布字幕
            </span>
          )}
        </div>
      )}
      <div className="entry-tools">
        {entry.plan && (
          <button className="text-button" onClick={() => onEdit(entry)}>
            <PenLine size={13} />
            编辑编排
          </button>
        )}
        {entry.audio_url && (
          <>
            <a href={entry.audio_url} download className="text-button">
              <Download size={13} />
              音频
            </a>
            <button
              className="round-play"
              title="播放到当前输出设备"
              aria-label="播放本条音频"
              onClick={() => onPlay(entry)}
            >
              <Play size={13} fill="currentColor" />
            </button>
          </>
        )}
      </div>
    </article>
  );
}
export function Dashboard({
  config,
  runtime,
  voices,
  devices,
  busy,
  navigate,
  quick,
  onEdit,
  onPlay,
  onRun,
  notify,
  save,
  dirty,
}: Props) {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  useEffect(() => {
    let live = true;
    api<Record<string, Capabilities>>("/capabilities")
      .then((c) => {
        if (live) setCaps(c[config.active.tts] || null);
      })
      .catch(() => {
        if (live) setCaps(null);
      });
    return () => {
      live = false;
    };
  }, [config.active.tts, config.providers]);
  const [text, setText] = useState(""),
    [levels, setLevels] = useState<number[]>(Array(70).fill(0)),
    [preview, setPreview] = useState(false);
  const upload = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setLevels((v) => [
      ...v.slice(1),
      runtime.listening ? Math.max(0, Math.min(1, (runtime.level_db + 65) / 65)) : 0,
    ]);
  }, [runtime.level_db, runtime.listening]);
  const voice = voices.find((v) => v.id === config.active.reference),
    tts = config.providers.find((p) => p.id === config.active.tts),
    llm = config.providers.find((p) => p.id === config.active.llm && p.kind === "llm");
  const targetLanguage = config.target_language ?? "original";
  const translating = targetLanguage !== "original";
  const auditEnabled = config.audit_enabled ?? true;
  const requiresLlm = translating || config.rewrite_enabled;
  // 原文直通只在显式指定了自检 LLM 时才发起 LLM 自检，其余情况只做本地复核。
  const llmAudit = auditEnabled && (requiresLlm || !!config.active.audit_llm);
  const processingMode = processingModeLabel(targetLanguage, config.rewrite_enabled);
  const llmIssue = llmConnectionIssue(llm);
  const submit = () =>
    onRun(async () => {
      if (!text.trim()) return;
      if (dirty) await save();
      await api("/process/text", {
        method: "POST",
        body: JSON.stringify({ text: text.trim(), preview_only: preview }),
      });
      setText("");
      notify(preview ? "已提交编排预览" : "已加入转述队列");
    });
  const stage = runtime.stage;
  const stages = [
    {
      name: "声音输入",
      sub: "停顿自动分句",
      icon: Mic,
      active: runtime.listening,
    },
    {
      name: "语音识别",
      sub: config.providers.find((p) => p.id === config.active.asr)?.name || "未设置",
      icon: AlignLeft,
      active: stage === "asr",
    },
    {
      name: processingMode,
      sub: translating
        ? `${targetLanguageNames[targetLanguage]} · 保留原意`
        : config.rewrite_enabled
          ? "角色人设 · 台词重塑"
          : "保持原语言与措辞",
      icon: Sparkles,
      active: stage === "llm",
    },
    {
      name: "情绪编排",
      sub: config.rewrite_enabled ? "情绪 · 动作 · 停顿" : "保留原有语气",
      icon: AudioLines,
      active: stage === "llm" && config.rewrite_enabled,
    },
    {
      name: "输出自检",
      sub: llmAudit ? "LLM 防语气指令漏读" : "仅本地指令复核",
      icon: ShieldCheck,
      active: stage === "audit",
    },
    {
      name: "语音输出",
      sub: tts?.name || "未设置",
      icon: Volume2,
      active: stage === "tts" || runtime.playing,
    },
  ];
  return (
    <>
      <section className="pipeline-strip">
        {stages.map((s, i) => (
          <div className={"pipeline-step " + (s.active ? "active" : "")} key={s.name}>
            <div className="step-icon">
              <s.icon size={18} />
            </div>
            <div>
              <b>{s.name}</b>
              <small>{s.sub}</small>
            </div>
            {i < stages.length - 1 && <ChevronRight className="step-arrow" size={15} />}
          </div>
        ))}
      </section>
      <div className="studio-grid">
        <div className="studio-main">
          <section className="card input-card">
            <div className="card-heading">
              <h2>
                <span className={"mic-icon " + (runtime.listening ? "listening" : "")}>
                  <Mic size={18} />
                </span>
                实时输入
              </h2>
              <span className="muted small">
                {runtime.listening
                  ? runtime.playing && config.audio.suppress_during_playback
                    ? "播放中 · 防回授保护"
                    : "正在倾听…"
                  : "麦克风待命"}
              </span>
            </div>
            <div className="input-device-row">
              <select
                aria-label="快捷选择输入设备"
                value={config.audio.input_device ?? ""}
                disabled={runtime.listening || busy}
                onChange={(e) =>
                  quick({
                    audio: {
                      ...config.audio,
                      input_device: e.target.value === "" ? null : +e.target.value,
                    },
                  })
                }
              >
                <option value="">系统默认麦克风</option>
                {devices.inputs.map((d) => (
                  <option value={d.id} key={d.id}>
                    {d.name} · {d.hostapi}
                  </option>
                ))}
              </select>
              <button className="text-button" onClick={() => navigate("devices")}>
                音频设置
                <ArrowUpRight size={13} />
              </button>
            </div>
            <div
              className={"live-wave " + (runtime.listening ? "listening" : "")}
              aria-label={`当前音量${runtime.level_db.toFixed(0)}分贝`}
            >
              {levels.map((v, i) => (
                <i
                  key={i}
                  style={{
                    height: `${3 + v * 85}px`,
                    opacity: 0.23 + v * 0.77,
                  }}
                />
              ))}
              <span className="wave-axis" />
            </div>
            <div className="wave-footer">
              <span className="tiny">
                <span className={"dot " + (runtime.listening ? "live" : "")} />
                {runtime.listening ? "连续监听" : "等待开始"}
              </span>
              <span className="tiny">停顿 {config.audio.pause_ms}ms 后提交</span>
              <span className="mono">
                {runtime.listening ? `${runtime.level_db.toFixed(0)} dBFS` : "— dBFS"}
              </span>
            </div>
            {runtime.listening && (
              <button
                className="text-button flush"
                onClick={() => onRun(() => api("/listen/flush", { method: "POST" }))}
              >
                <Scissors size={13} />
                立即提交当前片段
              </button>
            )}
            {config.audio.skip_low_energy && (
              <div className="energy-status">
                <span>
                  低能量 PASS · {config.audio.energy_threshold_db} dBFS /{" "}
                  {config.audio.min_active_ms} ms
                </span>
                <span title={runtime.last_skip_reason || ""}>
                  已跳过 {runtime.skipped_audio_count || 0} 段
                </span>
                {!!runtime.dropped_audio_count && (
                  <span className="error-text">
                    队列已满丢弃 {runtime.dropped_audio_count} 段
                  </span>
                )}
              </div>
            )}
            {runtime.listening && runtime.tts_remote && (
              <div className="energy-status" title={runtime.tts_remote.message}>
                <span className={runtime.tts_remote.ok ? "" : "error-text"}>
                  云端 TTS {runtime.tts_remote.provider} ·{" "}
                  {runtime.tts_remote.ok ? `已连通 ${runtime.tts_remote.rtt_ms} ms` : "无法连接"}
                </span>
                <span>检查于 {new Date(runtime.tts_remote.checked_at).toLocaleTimeString()}</span>
              </div>
            )}
          </section>
          <section className="card conversation">
            <div className="conversation-heading">
              <div className="conversation-tab">
                转写与自检 <span>{runtime.records.length}</span>
              </div>
              <a href="/api/export/session" download className="text-button">
                <Download size={14} />
                导出会话
              </a>
            </div>
            <div className="entries">
              {runtime.records.length ? (
                runtime.records
                  .slice(0, 30)
                  .map((entry) => (
                    <EntryCard key={entry.id} entry={entry} onEdit={onEdit} onPlay={onPlay} />
                  ))
              ) : (
                <div className="empty-studio">
                  <div className="empty-emblem">
                    <AudioLines size={34} />
                  </div>
                  <h2>开口即入戏，声线随心切换</h2>
                  <p>
                    自然开口或输入台词，声织会自动剥离语气指令，
                    <br />
                    按你的角色设定与目标声线实时演绎。
                  </p>
                  <div className="onboarding">
                    <button onClick={() => navigate("models")}>
                      <span>01</span>连接模型
                      <ChevronRight size={13} />
                    </button>
                    <button onClick={() => navigate("prompts")}>
                      <span>02</span>设定角色
                      <ChevronRight size={13} />
                    </button>
                    <button onClick={() => navigate("voices")}>
                      <span>03</span>选择音色
                      <ChevronRight size={13} />
                    </button>
                  </div>
                </div>
              )}
            </div>
          </section>
          <section className="card composer">
            <div className="composer-top">
              <span>
                <PenLine size={14} />
                手动试一句
              </span>
              <label>
                <input
                  type="checkbox"
                  checked={preview}
                  onChange={(e) => setPreview(e.target.checked)}
                />
                只预览编排
              </label>
            </div>
            <div className="composer-row">
              <textarea
                aria-label="手动测试文本"
                placeholder="输入你想说的话，试试角色的表达方式…"
                value={text}
                maxLength={4000}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.ctrlKey && e.key === "Enter") submit();
                }}
              />
              <button className="primary" disabled={!text.trim() || busy} onClick={submit}>
                <Send size={16} />
                {preview ? "预览编排" : "合成播放"}
              </button>
            </div>
            <div className="composer-foot">
              <button
                className="text-button"
                onClick={() => upload.current?.click()}
                disabled={busy}
              >
                <FileAudio size={14} />
                导入录音
              </button>
              <small>录音 ≤60 秒 / 25MB · Ctrl + Enter · {text.length}/4000</small>
            </div>
            <input
              ref={upload}
              hidden
              type="file"
              accept="audio/*"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file)
                  onRun(async () => {
                    if (dirty) await save();
                    const data = new FormData();
                    data.append("file", file);
                    await api("/process/audio", { method: "POST", body: data });
                    notify("录音已加入转述队列");
                  });
              }}
            />
          </section>
        </div>
        <aside className="inspector">
          <section className="card voice-inspector">
            <div className="card-heading">
              <h2>当前音色</h2>
              <button className="icon" aria-label="更换参考音色" onClick={() => navigate("voices")}>
                <ChevronRight size={19} />
              </button>
            </div>
            <div className="voice-identity">
              <div className="avatar">{voice?.name[0] || "声"}</div>
              <div>
                <b>{voice?.name || "尚未选择音色"}</b>
                <small>
                  {voice ? `${voice.duration.toFixed(1)}s 参考音频` : "导入一段参考录音"}
                </small>
              </div>
            </div>
            {voice && (
              <AudioPreview src={voice.audio_url} duration={voice.duration} label="参考音频" />
            )}
            <div className="engine-note">
              <Headphones size={13} />
              {tts?.type === "elevenlabs" || tts?.type === "openai_tts"
                ? `云端音色 ID：${tts.voice || "待设置"}`
                : "使用参考录音的音色合成"}
            </div>
          </section>
          <section className="card compact-card">
            <div className="card-heading">
              <h2>模型与人设</h2>
              <button className="icon" aria-label="模型设置" onClick={() => navigate("models")}>
                <ChevronRight size={19} />
              </button>
            </div>
            {(["asr", "llm", "tts"] as const).map((kind) => (
              <div className="compact-field" key={kind}>
                <label htmlFor={"quick-" + kind}>{kind.toUpperCase()}</label>
                <select
                  id={"quick-" + kind}
                  value={config.active[kind]}
                  disabled={busy}
                  onChange={(e) =>
                    quick({
                      active: { ...config.active, [kind]: e.target.value },
                    })
                  }
                >
                  {config.providers
                    .filter((p) => p.kind === kind)
                    .map((p) => (
                      <option value={p.id} key={p.id}>
                        {p.name}
                      </option>
                    ))}
                </select>
              </div>
            ))}
            <div className="inspector-divider" />
            <Field label="目标语言（LLM翻译）" note="保留原意、称呼和人称。">
              <select
                aria-label="目标语言（LLM翻译）"
                value={targetLanguage}
                disabled={busy}
                onChange={(e) => quick({ target_language: e.target.value as TargetLanguage })}
              >
                {Object.entries(targetLanguageNames).map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Toggle
              label="角色润色（LLM 优化）"
              note={translating ? "关闭后仅翻译。" : undefined}
              value={config.rewrite_enabled}
              onChange={(value) => quick({ rewrite_enabled: value })}
              disabled={busy}
            />
            <Toggle
              label="防漏读自检（LLM 自检）"
              note={
                requiresLlm || config.active.audit_llm
                  ? "关闭后仅做本地正则指令复核。"
                  : "原文直通时只做本地复核；在模型连接页设了自检 LLM 才另发一次 LLM 自检。"
              }
              value={auditEnabled}
              onChange={(value) => quick({ audit_enabled: value })}
              disabled={busy}
            />
            <div className="context-stat">
              <span>处理方式</span>
              <b>{processingMode}</b>
            </div>
            {translating && (
              <p className={"hint " + (llmIssue ? "danger-text" : "")} role="status">
                {llmIssue || `翻译与独立自检使用 ${llm?.name}。`}
                {!config.rewrite_enabled && "仅翻译也需配置 LLM。"}{" "}
                <button className="text-button" onClick={() => navigate("models")}>
                  配置连接 <ArrowUpRight size={12} />
                </button>
              </p>
            )}
            <button className="persona-preview" onClick={() => navigate("prompts")}>
              <span>
                <NotebookIcon />
                目标角色人设
                <ArrowUpRight size={12} />
              </span>
              <p>{config.persona || "设定录音人想要的角色性格、措辞与习惯。"}</p>
            </button>
            <div className="context-stat">
              <span>上下文记忆</span>
              <b>
                {config.context_limit}
                <small> 条消息</small>
              </b>
            </div>
            {!requiresLlm && (
              <p className="hint">当前原文直通，保持原语言与措辞。</p>
            )}
          </section>
          <section className="card compact-card">
            <div className="card-heading">
              <h2>编排控制</h2>
              <span className="eyebrow">DIRECTION</span>
            </div>
            <div className="chips">
              <span className="chip">{caps?.whisper ? "支持耳语" : "正常说话"}</span>
              <span className="chip">
                {caps?.emotions?.length ? "支持情绪指令" : "跟随参考语气"}
              </span>
            </div>
            <Toggle
              label="叹气 / 轻笑 / 呼吸"
              disabled={!!caps && !caps.events.length}
              note={
                caps && !caps.events.length
                  ? "当前 TTS 未支持独立声音动作"
                  : "根据表演提示词安排声音动作"
              }
              value={config.allow_events && (!caps || caps.events.length > 0)}
              onChange={(value) => quick({ allow_events: value })}
            />
            <div className="context-stat">
              <span>情绪强度</span>
              <b>{Math.round(config.emotion_intensity * 100)}%</b>
            </div>
            <input
              className="full"
              aria-label="快捷情绪强度"
              disabled={!!caps && !caps.emotions.length}
              type="range"
              min="0"
              max="1"
              step=".05"
              defaultValue={config.emotion_intensity}
              key={config.emotion_intensity}
              onPointerUp={(e) => quick({ emotion_intensity: +e.currentTarget.value })}
              onKeyUp={(e) => {
                if (e.key.startsWith("Arrow")) quick({ emotion_intensity: +e.currentTarget.value });
              }}
            />
            <button className="text-button full direction-link" onClick={() => navigate("prompts")}>
              调整语音表演提示词
              <ArrowUpRight size={13} />
            </button>
          </section>
        </aside>
      </div>
    </>
  );
}
function NotebookIcon() {
  return <PenLine size={13} />;
}
