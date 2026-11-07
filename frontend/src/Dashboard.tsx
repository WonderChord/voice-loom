import { useEffect, useState } from "react";
import {
  Mic,
  ChevronRight,
  ArrowUpRight,
  Play,
  CircleAlert,
  PenLine,
  Download,
  Headphones,
  AudioLines,
  ArrowRight,
  Sparkles,
  Volume2,
  AlignLeft,
  ChevronDown,
  SlidersHorizontal,
  Route,
  Disc3,
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
  deliveryNames,
  statusNames,
  targetLanguageNames,
  processingModeLabel,
  llmConnectionIssue,
} from "./types";
import { Field, Toggle, AudioPreview } from "./components";
import { SessionMonitor } from "./SessionMonitor";
import { activeFailover } from "./StudioSwitches";
import { StudioComposer, type StudioDraft } from "./StudioComposer";

export type Readiness = { ready: true } | { ready: false; message: string; provider: string; page: string };
type Props = {
  config: Config;
  runtime: Runtime;
  voices: Voice[];
  devices: Devices;
  busy: boolean;
  navigate: (p: string, providerId?: string) => void;
  draft: StudioDraft;
  updateDraft: (patch: Partial<StudioDraft>) => void;
  online: boolean;
  readiness: Readiness | null;
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
      <article id={"entry-" + entry.id} tabIndex={-1} className="entry entry-skipped">
        <div className="entry-meta">
          <time>{new Date(entry.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</time>
          <span className="status status-skipped">已跳过 · 低音量</span>
          <span className="entry-origin">{entry.origin === "mic" ? "麦克风" : "音频文件"}</span>
        </div>
        <p>{entry.skip_reason || entry.error || "有效声音能量不足，已在 ASR 前跳过。"}</p>
        <small className="muted">本段已在转写前跳过。</small>
      </article>
    );
  return (
    <article id={"entry-" + entry.id} tabIndex={-1} className={"entry " + (active ? "processing" : "")}>
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
        {entry.preview_only && <span className="chip">编排预览</span>}
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
          (s) => s.kind === "speech" && (s.direction || (s.delivery && s.delivery !== "normal") || (s.speed && s.speed !== 1)),
        )
        .map((s, i) => (
          <div className="chips" key={i}>
            {s.delivery && s.delivery !== "normal" && <span className="chip event">{deliveryNames[s.delivery]}</span>}
            {s.speed && s.speed !== 1 ? <span className="chip pause">语速 {s.speed}×</span> : null}
            {s.direction && <span className="direction-summary">表演指导（不朗读）：{s.direction}</span>}
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
  draft,
  updateDraft,
  online,
  readiness,
}: Props) {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const failover = activeFailover(config, runtime);
  const outputId = failover?.fallback || config.active.tts;
  useEffect(() => {
    let live = true;
    api<Record<string, Capabilities>>("/capabilities")
      .then((c) => {
        if (live) setCaps(c[outputId] || null);
      })
      .catch(() => {
        if (live) setCaps(null);
      });
    return () => {
      live = false;
    };
  }, [outputId, config.providers]);
  const [pane, setPane] = useState<"voice" | "models" | "direction">("voice");
  const [rackOpen, setRackOpen] = useState(false);
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
  const outputTts = config.providers.find((p) => p.id === outputId);
  const mappedVoice = outputTts && (voice?.provider_voice_ids?.[outputTts.id] || voice?.provider_voice_ids?.[outputTts.type]);
  const serviceVoice = outputTts && ["elevenlabs", "fish_tts", "openai_tts"].includes(outputTts.type);
  const voiceId = mappedVoice || (serviceVoice ? outputTts?.voice : "");
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
      sub: failover ? failover.fallback_name + " · 备用" : tts?.name || "未设置",
      icon: Volume2,
      active: stage === "tts" || runtime.playing,
    },
  ];
  return (
    <>
      <SessionMonitor config={config} runtime={runtime} devices={devices} busy={busy}
        selectInput={(input_device) => quick({ audio: { ...config.audio, input_device } })}
        openDevices={() => navigate("devices")} openHistory={() => navigate("history")}
        flush={() => onRun(() => api("/listen/flush", { method: "POST" }))} />
      <details className="signal-route">
        <summary><Route size={16}/><b>处理路径</b>
          <span>{processingMode} <i>→</i> {failover ? failover.fallback_name + " · 备用" : tts?.name}</span>
          <ChevronDown size={14}/></summary>
        <ol>{stages.map((s) => <li key={s.name} className={s.active ? "active" : ""}>
          <s.icon size={15}/><div><b>{s.name}</b><small>{s.sub}</small></div>
        </li>)}</ol>
      </details>
      {failover && <div className="notice failover-banner" role="status">
        <b>正在使用备用 TTS · {failover.fallback_name}</b>
        <span>主 TTS {failover.primary_name} 不可达，自 {new Date(failover.since).toLocaleTimeString("zh-CN")} 起自动切换；恢复后切回。</span>
        <small>{failover.reason}</small>
      </div>}
      {readiness && !readiness.ready && !dirty && !runtime.listening && <div className="setup-strip" role="status">
        <CircleAlert size={16}/><div><b>监听前还需设置</b><span>{readiness.message}</span></div>
        <button onClick={() => navigate(readiness.page, readiness.provider)}>
          {readiness.page === "voices" ? "去选音色" : "配置服务"}<ArrowUpRight size={14}/>
        </button>
      </div>}
      <div className="desk-grid">
          <StudioComposer draft={draft} update={updateDraft} config={config} dirty={dirty} save={save}
            busy={busy} online={online} run={onRun} notify={notify}
            showResult={draft.lastId && runtime.records.slice(0, 30).some((r) => r.id === draft.lastId) ? () => {
              const result = document.getElementById("entry-" + draft.lastId);
              result?.focus({ preventScroll: true });
              result?.scrollIntoView({ block: "center" });
            } : undefined} />
          <section className="card conversation">
            <div className="conversation-heading">
              <div className="conversation-tab">
                会话记录 <span>{runtime.records.length}</span>
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
                  <h2>还没有处理记录</h2>
                  <p>在上方预览一句台词，或开始监听。结果会显示在这里。</p>
                </div>
              )}
            </div>
          </section>
        <aside className={"channel-rack card" + (rackOpen ? " rack-open" : "")}>
          <div className="rack-heading"><h2><SlidersHorizontal size={17}/>通道设置</h2><span className="port-tag">OUT 01</span>
            <button className="icon rack-toggle" aria-label={rackOpen ? "收起通道设置" : "展开通道设置"} aria-expanded={rackOpen}
              onClick={() => setRackOpen(!rackOpen)}><ChevronDown size={16}/></button>
          </div>
          <div className="rack-inner">
          <div className="rack-tabs" role="group" aria-label="通道设置分组">
            {([["voice","音色"],["models","模型"],["direction","表演"]] as const).map(([key,label]) =>
              <button key={key} aria-pressed={pane === key} onClick={() => setPane(key)}>{label}</button>)}
          </div>
          {pane === "voice" && <section className="rack-pane voice-inspector">
            <div className="card-heading">
              <h2>当前音色</h2>
              <button className="icon" aria-label="更换参考音色" onClick={() => navigate("voices")}>
                <ChevronRight size={19} />
              </button>
            </div>
            <div className="voice-identity">
              <div className="voice-disc" aria-hidden="true"><Disc3 size={33}/></div>
              <div>
                <b>{voice?.name || (voiceId ? "服务预设音色" : "尚未选择音色")}</b>
                <small>
                  {voice ? `${voice.duration.toFixed(1)}s 参考音频` : voiceId ? "使用模型连接里的声音 ID" : "选择或导入一段参考录音"}
                </small>
              </div>
            </div>
            {voice && (
              <AudioPreview src={voice.audio_url} duration={voice.duration} label="参考音频" />
            )}
            <div className="engine-note">
              <Headphones size={13} />
              {outputTts?.type === "fish_tts" && !voiceId && voice ? "上传当前参考录音 · Fish 零样本克隆" : serviceVoice ? "声音 ID：" + (voiceId || "待设置") : mappedVoice ? "已绑定服务端预置音色" : "使用参考录音的音色合成"}
            </div>
            <dl className="rack-readout">
              <div><dt>输出引擎</dt><dd>{outputTts?.name || "未设置"}</dd></div>
              <div><dt>目标语言</dt><dd>{targetLanguageNames[targetLanguage]}</dd></div>
            </dl>
            <button className="rack-link" onClick={() => navigate("voices")}>打开参考音色<ArrowUpRight size={14}/></button>
          </section>}
          {pane === "models" && <section className="rack-pane compact-card">
            <div className="card-heading">
              <h2>识别、润色与翻译</h2>
              <button className="icon" aria-label="模型设置" onClick={() => navigate("models", config.active.tts)}>
                <ChevronRight size={19} />
              </button>
            </div>
            {(["asr", "llm"] as const).map((kind) => (
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
                <button className="text-button" onClick={() => navigate("models", config.active.llm)}>
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
          </section>}
          {pane === "direction" && <section className="rack-pane compact-card">
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
          </section>}
          </div>
        </aside>
      </div>
    </>
  );
}
function NotebookIcon() {
  return <PenLine size={13} />;
}
