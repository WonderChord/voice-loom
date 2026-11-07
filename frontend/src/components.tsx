import { type ReactNode, useEffect, useRef, useState } from "react";
import { X, Plus, Trash2, Play, Pause, Volume2 } from "lucide-react";
import {
  api,
  type Plan,
  type Segment,
  type Capabilities,
  emotionNames,
  eventNames,
  deliveryNames,
  type Delivery,
} from "./types";
export function Field({
  label,
  note,
  children,
}: {
  label: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {note && <small>{note}</small>}
    </label>
  );
}
export function Toggle({
  label,
  note,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  note?: string;
  value: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="toggle-row">
      <div>
        <strong>{label}</strong>
        {note && <small>{note}</small>}
      </div>
      <button
        type="button"
        className={"toggle " + (value ? "on" : "")}
        role="switch"
        disabled={disabled}
        aria-checked={value}
        aria-label={label}
        onClick={() => onChange(!value)}
      >
        <span />
      </button>
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const dialog = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const focusable = () => Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]') || []).filter(element => element.getClientRects().length);
    focusable()[0]?.focus();
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close.current(); }
      if (event.key === "Tab") {
        const items = focusable();
        const index = items.indexOf(document.activeElement as HTMLElement);
        if (items.length && (index < 0 || (!event.shiftKey && index === items.length - 1) || (event.shiftKey && index === 0))) {
          event.preventDefault(); items[event.shiftKey ? items.length - 1 : 0].focus();
        }
      }
    };
    document.addEventListener("keydown", keys);
    return () => { document.removeEventListener("keydown", keys); previous?.focus(); };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        ref={dialog}
        className={"modal " + (wide ? "wide" : "")}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <h2>{title}</h2>
          <button className="icon" aria-label="关闭" onClick={onClose}>
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
export function PlanEditor({
  value,
  onChange,
  providerId,
}: {
  value: Plan;
  onChange: (v: Plan) => void;
  providerId: string;
}) {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  useEffect(() => {
    let alive = true;
    api<Record<string, Capabilities>>("/capabilities")
      .then((c) => {
        if (alive) setCaps(c[providerId] || null);
      })
      .catch(() => setCaps(null));
    return () => {
      alive = false;
    };
  }, [providerId]);
  const update = (segments: Segment[]) =>
    onChange({
      ...value,
      segments,
      text: segments
        .filter((s) => s.kind === "speech")
        .map((s) => s.text || "")
        .join(""),
    });
  const set = (i: number, patch: Partial<Segment>) =>
    update(value.segments.map((s, n) => (i === n ? { ...s, ...patch } : s)));
  return (
    <div className="plan-editor">
      {value.directives?.length ? (
        <div className="notice">
          <b>表演指令（不朗读）</b>
          <p>{value.directives.join(" · ")}</p>
        </div>
      ) : null}
      {value.needs_review && (
        <div className="notice error">
          {value.audit?.message || "此稿需要人工检查。"}
        </div>
      )}
      {value.warnings?.length ? (
        <p className="hint">{value.warnings.join(" · ")}</p>
      ) : null}
      {value.segments.map((s, i) => (
        <div className="segment" key={i}>
          <div className="segment-head">
            <span className="eyebrow">
              {String(i + 1).padStart(2, "0")} /{" "}
              {s.kind === "speech"
                ? "台词"
                : s.kind === "event"
                  ? "声音动作"
                  : "停顿"}
            </span>
            <button
              className="icon"
              aria-label={`删除第${i + 1}段`}
              onClick={() => update(value.segments.filter((_, n) => i !== n))}
            >
              <Trash2 size={15} />
            </button>
          </div>
          {s.kind === "speech" ? (
            <>
              <textarea
                aria-label={`第${i + 1}段台词`}
                value={s.text || ""}
                onChange={(e) => set(i, { text: e.target.value })}
              />
              {(caps?.direction || s.direction) && (
                <Field label="表演指导（不朗读）" note={caps?.direction ? "写声线、神态、距离或语气目的；无需写括号标签。" : "当前模型会略过这段指导，只合成台词。"}>
                  <input aria-label={`第${i + 1}段表演指导`} maxLength={240}
                    value={s.direction || ""} placeholder="例如：压低声音，像对老朋友说一个秘密"
                    onChange={(e) => set(i, { direction: e.target.value })} />
                </Field>
              )}
              <div className="row wrap">
                <select
                  aria-label={`第${i + 1}段情绪`}
                  value={s.emotion || "neutral"}
                  onChange={(e) => set(i, { emotion: e.target.value })}
                >
                  {Object.entries(emotionNames).map(([v, n]) => (
                    <option
                      key={v}
                      value={v}
                      disabled={
                        !!caps && v !== "neutral" && !caps.emotions.includes(v)
                      }
                    >
                      {n}
                      {caps && v !== "neutral" && !caps.emotions.includes(v)
                        ? "（模型未支持）"
                        : ""}
                    </option>
                  ))}
                </select>
                <select
                  aria-label={`第${i + 1}段说话方式`}
                  value={s.delivery || "normal"}
                  onChange={(e) =>
                    set(i, { delivery: e.target.value as Delivery })
                  }
                >
                  {(Object.entries(deliveryNames) as [Delivery, string][]).map(([key, label]) => {
                    const supported = !caps || (caps.deliveries || (caps.whisper ? ["normal", "whisper"] : ["normal"])).includes(key);
                    return <option key={key} value={key} disabled={!supported}>{label}{supported ? "" : "（模型未支持）"}</option>;
                  })}
                </select>
                <label className="inline-range">
                  强度
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step=".1"
                    value={s.intensity ?? 0.5}
                    onChange={(e) => set(i, { intensity: +e.target.value })}
                  />
                  {Math.round((s.intensity ?? 0.5) * 100)}%
                </label>
              </div>
              <div className="row segment-speed">
                <label className="inline-range">
                  语速
                  <input
                    aria-label={`第${i + 1}段语速`}
                    type="number"
                    min=".5"
                    max="2"
                    step=".05"
                    value={s.speed ?? 1}
                    disabled={!!caps && !caps.speed}
                    onChange={(e) => set(i, { speed: +e.target.value })}
                  />
                  倍
                </label>
                {caps && !caps.speed && (
                  <small className="muted">当前引擎未支持独立语速控制</small>
                )}
              </div>
            </>
          ) : s.kind === "event" ? (
            <select
              aria-label={`第${i + 1}段动作`}
              value={s.event || "sigh"}
              onChange={(e) => set(i, { event: e.target.value })}
            >
              {Object.entries(eventNames).map(([v, n]) => (
                <option
                  key={v}
                  value={v}
                  disabled={!!caps && !caps.events.includes(v)}
                >
                  {n}
                  {caps && !caps.events.includes(v) ? "（模型未支持）" : ""}
                </option>
              ))}
            </select>
          ) : (
            <label className="inline-range">
              时长
              <input
                type="number"
                min="1"
                max="5000"
                step="50"
                value={s.duration_ms ?? 300}
                onChange={(e) => set(i, { duration_ms: +e.target.value })}
              />
              毫秒
            </label>
          )}
        </div>
      ))}
      <div className="row">
        <button
          onClick={() =>
            update([
              ...value.segments,
              {
                kind: "speech",
                text: "",
                emotion: "neutral",
                intensity: 0.5,
                delivery: "normal",
                speed: 1,
              },
            ])
          }
        >
          <Plus size={15} />
          台词
        </button>
        <button
          disabled={!!caps && !caps.events.length}
          onClick={() =>
            update([
              ...value.segments,
              { kind: "event", event: caps?.events[0] || "sigh" },
            ])
          }
        >
          <Plus size={15} />
          声音动作
        </button>
        <button
          onClick={() =>
            update([...value.segments, { kind: "pause", duration_ms: 300 }])
          }
        >
          <Plus size={15} />
          停顿
        </button>
      </div>
      {caps && (
        <p className="hint">
          当前模型：{caps.whisper ? "支持耳语" : "正常说话"} ·{" "}
          {caps.events.length
            ? "支持" + caps.events.map((e) => eventNames[e] || e).join("、")
            : "声音动作将跳过并记录提示"}
          。切换 TTS 会同步更新可用设置。
        </p>
      )}
    </div>
  );
}
export function AudioPreview({
  src,
  duration = 0,
  label = "参考音频",
}: {
  src: string;
  duration?: number;
  label?: string;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false),
    [position, setPosition] = useState(0),
    [length, setLength] = useState(duration),
    [error, setError] = useState("");
  useEffect(() => {
    setPosition(0);
    setPlaying(false);
    setError("");
  }, [src]);
  const fmt = (v: number) =>
    `${Math.floor(v / 60)}:${String(Math.floor(v % 60)).padStart(2, "0")}`;
  return (
    <div className="audio-preview">
      <audio
        ref={audio}
        src={src}
        preload="metadata"
        onLoadedMetadata={() => setLength(audio.current?.duration || duration)}
        onTimeUpdate={() => setPosition(audio.current?.currentTime || 0)}
        onEnded={() => setPlaying(false)}
        onPause={() => setPlaying(false)}
        onPlay={() => setPlaying(true)}
        onError={() => setError("音频不可播放")}
      />
      <button
        className="icon audio-play"
        aria-label={`${playing ? "暂停" : "试听"}${label}`}
        onClick={() => {
          if (playing) audio.current?.pause();
          else audio.current?.play().catch(() => setError("请检查音频文件"));
        }}
      >
        {playing ? <Pause size={14} /> : <Play size={14} fill="currentColor" />}
      </button>
      <div className="audio-preview-line">
        <span style={{ width: `${length ? (position / length) * 100 : 0}%` }} />
      </div>
      <span>{error || `${fmt(position)} / ${fmt(length)}`}</span>
      <Volume2 size={12} />
    </div>
  );
}
