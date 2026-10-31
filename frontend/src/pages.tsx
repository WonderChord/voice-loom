import { useState, useRef, useEffect } from "react";
import {
  Plus,
  Search,
  Star,
  Pin,
  PenLine,
  Trash2,
  Upload,
  Check,
  RefreshCw,
  Cloud,
  Server,
  ShieldCheck,
  Download,
  Palette,
  Sun,
  Moon,
  AudioLines,
  Mic,
  Volume2,
} from "lucide-react";
import {
  api,
  type Config,
  type Provider,
  type Voice,
  type Runtime,
  type Devices,
  type Entry,
  type Kind,
} from "./types";
import { Field, Toggle, Modal, AudioPreview } from "./components";
import { EntryCard } from "./Dashboard";
type Run = (fn: () => Promise<unknown>) => void;
type Notify = (text: string, error?: boolean) => void;
type Change = (patch: Partial<Config>) => void;
export function VoicesPage({
  config,
  voices,
  onChange,
  refresh,
  run,
  notify,
  quick,
}: {
  config: Config;
  voices: Voice[];
  onChange: Change;
  refresh: () => Promise<void>;
  run: Run;
  notify: Notify;
  quick: Change;
}) {
  const [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all"),
    [editing, setEditing] = useState<Voice | null>(null),
    [removing, setRemoving] = useState<Voice | null>(null),
    [importing, setImporting] = useState(false),
    [file, setFile] = useState<File | null>(null),
    [name, setName] = useState(""),
    [transcript, setTranscript] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const patch = (voice: Voice, data: Partial<Voice>) =>
    run(async () => {
      await api(`/references/${voice.id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      });
      await refresh();
    });
  const list = voices
    .filter(
      (v) =>
        (filter !== "favorite" || v.favorite) &&
        (filter !== "pinned" || v.pinned) &&
        `${v.name} ${v.tags.join(" ")}`.toLowerCase().includes(query.toLowerCase()),
    )
    .sort((a, b) => Number(b.pinned) - Number(a.pinned));
  return (
    <>
      <div className="page-intro">
        <div>
          <h2>每一种声音，都有自己的位置</h2>
          <p>收好参考录音、准确台词和音色标签，随时换回熟悉的表达。</p>
        </div>
        <button className="primary" onClick={() => setImporting(true)}>
          <Plus size={17} />
          导入参考语音
        </button>
      </div>
      <div className="toolbar">
        <div className="segmented">
          {[
            ["all", "全部音色"],
            ["pinned", "置顶"],
            ["favorite", "收藏"],
          ].map(([v, n]) => (
            <button className={filter === v ? "active" : ""} key={v} onClick={() => setFilter(v)}>
              {n}
              {v === "all" && <small>{voices.length}</small>}
            </button>
          ))}
        </div>
        <div className="search-box">
          <Search size={16} />
          <input
            aria-label="搜索参考音色"
            placeholder="搜索名称或标签"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      <div className="voice-grid">
        {list.map((v, i) => (
          <article
            className={"card voice-card " + (v.id === config.active.reference ? "chosen" : "")}
            key={v.id}
          >
            <div className="row between">
              <div className={"avatar tone-" + (i % 4)}>{v.name[0]}</div>
              <div className="row tight">
                <button
                  className={"icon " + (v.pinned ? "highlight" : "")}
                  aria-label={v.pinned ? "取消置顶" : "置顶音色"}
                  title="置顶"
                  onClick={() => patch(v, { pinned: !v.pinned })}
                >
                  <Pin size={16} fill={v.pinned ? "currentColor" : "none"} />
                </button>
                <button
                  className={"icon " + (v.favorite ? "highlight" : "")}
                  aria-label={v.favorite ? "取消收藏" : "收藏音色"}
                  title="收藏"
                  onClick={() => patch(v, { favorite: !v.favorite })}
                >
                  <Star size={17} fill={v.favorite ? "currentColor" : "none"} />
                </button>
              </div>
            </div>
            <h3>{v.name}</h3>
            <div className="muted small">
              {v.duration.toFixed(1)} 秒 · {v.language === "zh" ? "中文" : v.language}{" "}
              {v.pinned ? "· 已置顶" : ""}
            </div>
            <div className="chips">
              {v.tags.map((t) => (
                <span className="chip" key={t}>
                  {t}
                </span>
              ))}
            </div>
            <AudioPreview src={v.audio_url} duration={v.duration} label={v.name} />
            <p className="reference-text">
              {v.transcript || "尚未填写参考台词，可在编辑中自动识别。"}
            </p>
            <div className="voice-card-foot">
              <button
                className={v.id === config.active.reference ? "selected-button" : "primary"}
                onClick={() => quick({ active: { ...config.active, reference: v.id } })}
              >
                {v.id === config.active.reference ? (
                  <>
                    <Check size={15} />
                    当前使用
                  </>
                ) : (
                  "使用此音色"
                )}
              </button>
              <button
                className="icon"
                aria-label={`编辑${v.name}`}
                onClick={() => setEditing(structuredClone(v))}
              >
                <PenLine size={16} />
              </button>
              <button
                className="icon danger-text"
                aria-label={`删除${v.name}`}
                onClick={() => setRemoving(v)}
              >
                <Trash2 size={16} />
              </button>
            </div>
          </article>
        ))}
        <button className="import-tile" onClick={() => setImporting(true)}>
          <span>
            <Plus size={24} />
          </span>
          <b>添加新声音</b>
          <small>WAV / MP3 / FLAC / OGG</small>
        </button>
      </div>
      <p className="hint">
        参考录音用于支持声音克隆的本地引擎。云端音色 ID 可在“编辑音色”中关联，也可在模型连接中配置。
      </p>
      {importing && (
        <Modal title="导入参考语音" onClose={() => setImporting(false)}>
          <button className="upload-zone" onClick={() => input.current?.click()}>
            <Upload size={28} />
            <b>{file?.name || "点击选择参考录音"}</b>
            <small>清晰、单人的短录音 · 最长 60 秒 / 25 MB</small>
          </button>
          <input
            ref={input}
            hidden
            type="file"
            accept="audio/*"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) {
                setFile(f);
                if (!name) setName(f.name.replace(/\.[^.]+$/, ""));
              }
            }}
          />
          <Field label="音色名称">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：温柔 / 日常"
            />
          </Field>
          <Field label="参考台词" note="填写录音中实际说出的文字；也可以导入后自动识别。">
            <textarea rows={3} value={transcript} onChange={(e) => setTranscript(e.target.value)} />
          </Field>
          <div className="modal-actions">
            <button onClick={() => setImporting(false)}>取消</button>
            <button
              className="primary"
              disabled={!file || !name.trim()}
              onClick={() =>
                run(async () => {
                  const data = new FormData();
                  data.append("file", file!);
                  data.append("name", name);
                  data.append("transcript", transcript);
                  data.append("language", "zh");
                  await api("/references", { method: "POST", body: data });
                  await refresh();
                  setImporting(false);
                  setFile(null);
                  setName("");
                  setTranscript("");
                  notify("参考音色已保存");
                })
              }
            >
              导入音色
            </button>
          </div>
        </Modal>
      )}
      {editing && (
        <Modal title="编辑参考音色" onClose={() => setEditing(null)}>
          <Field label="名称">
            <input
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
            />
          </Field>
          <Field label="标签（用逗号分隔）">
            <input
              value={editing.tags.join(",")}
              onChange={(e) =>
                setEditing({
                  ...editing,
                  tags: e.target.value.split(/[,，]/).map((v) => v.trim()),
                })
              }
            />
          </Field>
          <Field label="参考台词">
            <textarea
              rows={4}
              value={editing.transcript}
              onChange={(e) => setEditing({ ...editing, transcript: e.target.value })}
            />
          </Field>
          <button
            onClick={() =>
              run(async () => {
                const v = await api<Voice>(`/references/${editing.id}/transcribe`, {
                  method: "POST",
                });
                setEditing({ ...editing, transcript: v.transcript });
                await refresh();
                notify("已使用当前 ASR 识别参考台词");
              })
            }
          >
            <Mic size={15} />
            自动识别参考台词
          </button>
          <Field label="语言">
            <select
              value={editing.language}
              onChange={(e) => setEditing({ ...editing, language: e.target.value })}
            >
              <option value="zh">中文</option>
              <option value="en">英文</option>
              <option value="ja">日文</option>
              <option value="ko">韩文</option>
              <option value="yue">粤语</option>
              <option value="auto">自动</option>
            </select>
          </Field>
          {config.providers
            .filter((p) => p.kind === "tts" && ["elevenlabs", "openai_tts", "fish_tts"].includes(p.type))
            .map((p) => (
              <Field
                key={p.id}
                label={`${p.name} · 音色 ID`}
                note="已在该服务创建的音色 ID；留空使用模型配置。"
              >
                <input
                  value={editing.provider_voice_ids[p.id] || ""}
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      provider_voice_ids: {
                        ...editing.provider_voice_ids,
                        [p.id]: e.target.value,
                      },
                    })
                  }
                />
              </Field>
            ))}
          <div className="modal-actions">
            <button onClick={() => setEditing(null)}>取消</button>
            <button
              className="primary"
              onClick={() =>
                run(async () => {
                  const { name, transcript, language, tags, provider_voice_ids } = editing;
                  await api(`/references/${editing.id}`, {
                    method: "PATCH",
                    body: JSON.stringify({
                      name,
                      transcript,
                      language,
                      tags: tags.filter(Boolean),
                      provider_voice_ids,
                    }),
                  });
                  await refresh();
                  setEditing(null);
                  notify("音色已更新");
                })
              }
            >
              保存音色
            </button>
          </div>
        </Modal>
      )}
      {removing && (
        <Modal title="删除参考音色" onClose={() => setRemoving(null)}>
          <p>删除“{removing.name}”及工作台中的参考录音副本？原始导入文件会保留。</p>
          <div className="modal-actions">
            <button onClick={() => setRemoving(null)}>取消</button>
            <button
              className="danger"
              onClick={() =>
                run(async () => {
                  await api(`/references/${removing.id}`, { method: "DELETE" });
                  await refresh();
                  const fresh = await api<Config>("/config");
                  if (config.active.reference === removing.id)
                    onChange({
                      active: {
                        ...config.active,
                        reference: fresh.active.reference,
                      },
                    });
                  setRemoving(null);
                  notify("音色已删除");
                })
              }
            >
              删除音色
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
const typeNames: Record<string, string> = {
  qwen_local: "Qwen3-ASR 本机",
  sensevoice: "SenseVoice 本机",
  openai_asr: "OpenAI 兼容 ASR",
  openai_llm: "OpenAI 兼容 LLM",
  breeze: "Breeze 本机",
  elevenlabs: "ElevenLabs 云端",
  openai_tts: "OpenAI 兼容 TTS",
  gptsovits: "GPT-SoVITS",
  fish_tts: "Fish Audio · Drama 3",
  drama3_local: "Drama 3 · 本地推理",
};
export function ModelsPage({
  config,
  change,
  save,
  run,
  notify,
}: {
  config: Config;
  change: Change;
  save: () => Promise<Config>;
  run: Run;
  notify: Notify;
}) {
  const [kind, setKind] = useState<Kind>("asr"),
    [selected, setSelected] = useState(config.active.asr),
    [results, setResults] = useState<Record<string, { ok: boolean; message: string }>>({});
  const provider =
    config.providers.find((p) => p.id === selected && p.kind === kind) ||
    config.providers.find((p) => p.kind === kind)!;
  const set = (patch: Partial<Provider>) =>
    change({
      providers: config.providers.map((p) => (p.id === provider.id ? { ...p, ...patch } : p)),
    });
  const add = () => {
    const p: Provider = {
      id: `${kind}-${Date.now()}`,
      kind,
      type: kind === "asr" ? "openai_asr" : kind === "llm" ? "openai_llm" : "openai_tts",
      name: `新的 ${kind.toUpperCase()} 连接`,
      base_url: "https://api.openai.com/v1",
      model: "",
      voice: "",
      response_mode: "json_object",
      instructions: true,
      tag_style: "none",
      options: {},
      performance_rules: {},
    };
    change({ providers: [...config.providers, p] });
    setSelected(p.id);
  };
  return (
    <>
      <div className="page-intro">
        <div>
          <h2>模型各司其职，连接随时可换</h2>
          <p>每个服务独立保存地址、模型和密钥。可添加多个云端连接并随时切换。</p>
        </div>
        <button onClick={add}>
          <Plus size={16} />
          添加连接
        </button>
      </div>
      <div className="segmented model-tabs">
        {(["asr", "llm", "tts"] as const).map((k) => (
          <button
            className={kind === k ? "active" : ""}
            onClick={() => {
              setKind(k);
              setSelected(config.active[k]);
            }}
            key={k}
          >
            {k.toUpperCase()}{" "}
            <small>{k === "asr" ? "语音识别" : k === "llm" ? "角色润色" : "语音合成"}</small>
          </button>
        ))}
      </div>
      <div className="model-layout">
        <div className="model-list">
          {config.providers
            .filter((p) => p.kind === kind)
            .map((p) => (
              <button
                className={"model-item " + (p.id === provider.id ? "selected" : "")}
                key={p.id}
                onClick={() => setSelected(p.id)}
              >
                <span className="model-icon">
                  {p.base_url.includes("127.0.0.1") ||
                  p.base_url.includes("localhost") ||
                  p.base_url.includes("[::1]") ||
                  p.type === "sensevoice" ? (
                    <Server size={20} />
                  ) : (
                    <Cloud size={20} />
                  )}
                </span>
                <span>
                  <b>{p.name}</b>
                  <small>{typeNames[p.type]}</small>
                </span>
                {config.active[kind] === p.id && <Check size={16} />}
              </button>
            ))}
        </div>
        <section className="card model-detail">
          <div className="card-heading">
            <div>
              <div className="eyebrow">{kind.toUpperCase()} CONNECTION</div>
              <h2>{provider.name}</h2>
            </div>
            <div className="row">
              {config.active[kind] === provider.id ? (
                <span className="chip active-chip">当前使用</span>
              ) : (
                <button
                  className="primary"
                  onClick={() =>
                    change({
                      active: { ...config.active, [kind]: provider.id },
                    })
                  }
                >
                  设为当前
                </button>
              )}
              {kind === "asr" && (
                <button
                  onClick={() => change({ default_asr: provider.id })}
                  disabled={config.default_asr === provider.id}
                >
                  {config.default_asr === provider.id ? (
                    <>
                      <Check size={14} />
                      默认 ASR
                    </>
                  ) : (
                    "设为默认 ASR"
                  )}
                </button>
              )}
              {kind === "llm" && (
                <button
                  onClick={() =>
                    change({
                      active: {
                        ...config.active,
                        audit_llm: config.active.audit_llm === provider.id ? "" : provider.id,
                      },
                    })
                  }
                >
                  {(config.active.audit_llm || config.active.llm) === provider.id ? (
                    <>
                      <Check size={14} />
                      {config.active.audit_llm === provider.id ? "独立自检 LLM" : "自检跟随当前"}
                    </>
                  ) : (
                    "设为自检 LLM"
                  )}
                </button>
              )}
            </div>
          </div>
          <div className="form-grid">
            <Field label="显示名称">
              <input value={provider.name} onChange={(e) => set({ name: e.target.value })} />
            </Field>
            <Field label="服务类型">
              <select
                value={provider.type}
                onChange={(e) =>
                  set({
                    type: e.target.value,
                    tag_style: e.target.value === "elevenlabs" ? "eleven" : "none",
                    ...(e.target.value === "fish_tts"
                      ? { base_url: "https://api.fish.audio", model: "drama-3" }
                      : e.target.value === "drama3_local"
                        ? { base_url: "http://127.0.0.1:17863", model: "drama-3" }
                        : {}),
                  })
                }
              >
                {Object.entries(typeNames)
                  .filter(([t]) =>
                    kind === "asr"
                      ? ["qwen_local", "sensevoice", "openai_asr"].includes(t)
                      : kind === "llm"
                        ? t === "openai_llm"
                        : [
                            "breeze",
                            "elevenlabs",
                            "openai_tts",
                            "gptsovits",
                            "fish_tts",
                            "drama3_local",
                          ].includes(t),
                  )
                  .map(([v, n]) => (
                    <option key={v} value={v}>
                      {n}
                    </option>
                  ))}
              </select>
            </Field>
          </div>
          <Field
            label="服务地址（Base URL）"
            note="地址包含服务要求的版本前缀，例如 https://api.openai.com/v1。"
          >
            <input
              spellCheck={false}
              value={provider.base_url}
              placeholder="https://…/v1"
              onChange={(e) => set({ base_url: e.target.value })}
            />
          </Field>
          <div className="form-grid">
            <Field label="模型名称 / Model ID">
              <input
                spellCheck={false}
                value={provider.model}
                onChange={(e) => set({ model: e.target.value })}
                placeholder={
                  provider.type === "breeze" || provider.type === "qwen_local"
                    ? "本机预设模型，可留空"
                    : "填写服务提供的准确模型 ID"
                }
              />
            </Field>
            <Field
              label="API Key"
              note={provider.has_key ? "已安全保存，留空保留现有密钥。" : "密钥在本机加密保存。"}
            >
              <input
                type="password"
                autoComplete="new-password"
                value={provider.api_key || ""}
                placeholder={provider.has_key ? "•••••••• 已保存" : "输入密钥（本机服务可留空）"}
                onChange={(e) => set({ api_key: e.target.value, clear_key: false })}
              />
            </Field>
          </div>
          {provider.has_key && (
            <button
              className="text-button danger-text"
              onClick={() => set({ clear_key: !provider.clear_key, api_key: "" })}
            >
              {provider.clear_key ? "已标记清除，点击撤销" : "清除保存的密钥"}
            </button>
          )}
          {kind === "llm" && (
            <Field
              label="结构化输出方式"
              note="优先 JSON Object；支持严格结构化输出的服务可以选择 JSON Schema。"
            >
              <select
                value={provider.response_mode}
                onChange={(e) => set({ response_mode: e.target.value })}
              >
                <option value="json_object">JSON Object</option>
                <option value="json_schema">JSON Schema（严格）</option>
                <option value="text">文本 JSON（兼容模式）</option>
              </select>
            </Field>
          )}
          {kind === "tts" && (
            <>
              {!["breeze", "gptsovits", "drama3_local"].includes(provider.type) && (
                <Field label="云端音色 ID / Voice" note="云端 TTS 使用服务上的音色 ID。">
                  <input
                    value={provider.voice}
                    onChange={(e) => set({ voice: e.target.value })}
                    placeholder="例如 alloy 或服务分配的音色 ID"
                  />
                </Field>
              )}
              {["breeze", "gptsovits", "drama3_local"].includes(provider.type) && (
                <p className="hint">使用工作台当前选择的参考音色及对应台词，在“参考音色”中更换。</p>
              )}
              {provider.type === "openai_tts" && (
                <Toggle
                  label="发送语音风格指令"
                  note="适合支持 instructions 的模型；tts-1 系列会自动省略。"
                  value={provider.instructions}
                  onChange={(instructions) => set({ instructions })}
                />
              )}
              <Capabilities providerId={provider.id} refreshKey={config} />
            </>
          )}
          {results[provider.id] && (
            <div className={"notice " + (results[provider.id].ok ? "success" : "error")}>
              {results[provider.id].message}
            </div>
          )}
          <div className="model-actions">
            <button
              onClick={() =>
                run(async () => {
                  await save();
                  const r = await api<{ ok: boolean; message: string }>(
                    `/providers/${provider.id}/test`,
                    { method: "POST" },
                  );
                  setResults({ ...results, [provider.id]: r });
                  notify(r.message, !r.ok);
                })
              }
            >
              <RefreshCw size={15} />
              测试连接
            </button>
            <span className="muted small">只检查连接，实际生成在工作台进行。</span>
            <button
              className="icon danger-text"
              title="删除此连接"
              aria-label="删除模型连接"
              disabled={config.active[kind] === provider.id || config.default_asr === provider.id}
              onClick={() => {
                change({
                  providers: config.providers.filter((p) => p.id !== provider.id),
                });
                setSelected(config.active[kind]);
              }}
            >
              <Trash2 size={16} />
            </button>
          </div>
        </section>
      </div>
      <p className="save-hint">
        <ShieldCheck size={15} />
        设置更改后，点击右上角“保存更改”。本机模型首次使用时加载。
      </p>
    </>
  );
}
interface Caps {
  emotions: string[];
  events: string[];
  whisper: boolean;
  speed: boolean;
  reference_audio: boolean;
  instructions: boolean;
  notes: string[];
  tag_spec?: {
    whisper_tag: string | null;
    event_tags: Record<string, string>;
  };
}
export function Capabilities({
  providerId,
  refreshKey,
}: {
  providerId: string;
  refreshKey?: unknown;
}) {
  const [caps, setCaps] = useState<Caps | null>(null);
  useEffect(() => {
    let live = true;
    api<Record<string, Caps>>("/capabilities")
      .then((c) => {
        if (live) setCaps(c[providerId] || null);
      })
      .catch(() => setCaps(null));
    return () => {
      live = false;
    };
  }, [providerId, refreshKey]);
  return caps ? (
    <div className="capability-box">
      <div className="eyebrow">当前模型的表演能力</div>
      <div className="chips">
        <span className={"chip " + (caps.whisper ? "active-chip" : "")}>
          耳语 {caps.whisper ? "支持" : "未支持"}
        </span>
        <span className={"chip " + (caps.speed ? "active-chip" : "")}>
          语速 {caps.speed ? "支持" : "未支持"}
        </span>
        <span className="chip">{caps.reference_audio ? "参考音频" : "云端音色 ID"}</span>
        {caps.events?.map((e) => (
          <span className="chip event" key={e}>
            {({ sigh: "叹气", laugh: "轻笑", breath: "呼吸" } as Record<string, string>)[e] || e}
          </span>
        ))}
      </div>
      <small>
        情绪：
        {caps.emotions
          ?.map(
            (e) =>
              (
                ({
                  neutral: "自然",
                  happy: "开心",
                  sad: "低落",
                  angry: "生气",
                  calm: "平静",
                  thoughtful: "思考",
                  excited: "兴奋",
                }) as Record<string, string>
              )[e] || e,
          )
          .join(" / ") || "自然语气"}
      </small>
      {caps.tag_spec?.whisper_tag && (
        <small>耳语规范：{caps.tag_spec.whisper_tag}</small>
      )}
      {caps.notes?.map((n, i) => (
        <p key={i}>{n}</p>
      ))}
    </div>
  ) : (
    <div className="hint">保存模型设置后，将显示该模型的情绪与耳语能力。</div>
  );
}
export { PromptsPage } from "./PromptsPage";
export function DevicesPage({
  config,
  devices,
  change,
  refresh,
  listening,
}: {
  config: Config;
  devices: Devices;
  change: Change;
  refresh: () => void;
  listening: boolean;
}) {
  const audio = config.audio;
  const update = (p: Partial<Config["audio"]>) => change({ audio: { ...audio, ...p } });
  return (
    <>
      <div className="page-intro">
        <div>
          <h2>声音从哪里来，要到哪里去</h2>
          <p>选择麦克风、耳机或虚拟声卡，调整持续监听的分句节奏。</p>
        </div>
        <button onClick={refresh}>
          <RefreshCw size={16} />
          刷新设备
        </button>
      </div>
      {listening && <div className="notice">正在监听。请先停止监听，再保存音频设置。</div>}
      <div className="prompt-grid">
        {(["input", "output"] as const).map((k) => (
          <section className="card settings-card" key={k}>
            <div className="device-heading">
              {k === "input" ? <Mic size={24} /> : <Volume2 size={24} />}
              <h2>{k === "input" ? "声音输入" : "语音输出"}</h2>
            </div>
            <Field
              label={k === "input" ? "输入麦克风" : "输出设备"}
              note={
                k === "input"
                  ? "使用实际录音设备；后台通过本机音频接口采集。"
                  : "可选耳机或虚拟声卡；对端软件选择对应的虚拟麦克风。"
              }
            >
              <select
                disabled={listening}
                value={audio[`${k}_device`] ?? ""}
                onChange={(e) =>
                  update({
                    [`${k}_device`]: e.target.value === "" ? null : +e.target.value,
                  })
                }
              >
                <option value="">系统默认</option>
                {devices[k === "input" ? "inputs" : "outputs"].map((d) => (
                  <option value={d.id} key={d.id}>
                    {d.name} · {d.hostapi}
                  </option>
                ))}
              </select>
            </Field>
            <div className="device-detail">
              {devices[k === "input" ? "inputs" : "outputs"]
                .filter((d) => d.id === audio[`${k}_device`])
                .map((d) => (
                  <span key={d.id}>
                    {d.hostapi} / {d.sample_rate} Hz
                  </span>
                ))}
            </div>
          </section>
        ))}
      </div>
      <section className="card settings-card">
        <h2>低能量直接跳过</h2>
        <Toggle
          label="ASR 前检查音频能量"
          note="低于门槛或有效声音过短的片段直接 PASS，覆盖持续录音和导入音频。"
          value={audio.skip_low_energy}
          onChange={(skip_low_energy) => update({ skip_low_energy })}
        />
        <div className="form-grid">
          <Field
            label={`能量门槛 ${audio.energy_threshold_db} dBFS`}
            note="默认 −45 dBFS；耳语较轻时适当降低。"
          >
            <input
              aria-label="ASR 能量门槛"
              type="range"
              min="-80"
              max="-10"
              step="1"
              disabled={!audio.skip_low_energy}
              value={audio.energy_threshold_db}
              onChange={(e) => update({ energy_threshold_db: +e.target.value })}
            />
          </Field>
          <Field
            label="最低有效声音时长（毫秒）"
            note="累计达到门槛的声音需超过此时长，用于过滤短促尖峰。"
          >
            <input
              aria-label="最低有效声音时长"
              type="number"
              min="20"
              max="1000"
              step="20"
              disabled={!audio.skip_low_energy}
              value={audio.min_active_ms}
              onChange={(e) => update({ min_active_ms: +e.target.value })}
            />
          </Field>
        </div>
        <p className="hint">
          按短帧逐段检查能量，长录音前后的静音不拉低中间正常语音。被跳过的片段会记录原因。
        </p>
      </section>
      <section className="card settings-card">
        <h2>连续监听与分句</h2>
        <div className="form-grid">
          <Field
            label={`停顿 ${audio.pause_ms} 毫秒后提交`}
            note="短停顿更及时，长停顿更容易保留完整句子。"
          >
            <input
              type="range"
              min="300"
              max="2500"
              step="50"
              value={audio.pause_ms}
              onChange={(e) => update({ pause_ms: +e.target.value })}
            />
          </Field>
          <Field
            label={`断句触发音量 ${audio.min_db} dBFS`}
            note="控制麦克风何时开始收集一句话；数值越低越灵敏。"
          >
            <input
              type="range"
              min="-65"
              max="-20"
              step="1"
              value={audio.min_db}
              onChange={(e) => update({ min_db: +e.target.value })}
            />
          </Field>
          <Field label="单段最长时长（秒）" note="长句达到上限自动分段，后续继续监听。">
            <input
              type="number"
              min="5"
              max="60"
              value={audio.max_seconds}
              onChange={(e) => update({ max_seconds: +e.target.value })}
            />
          </Field>
        </div>
        <Toggle
          label="播放时暂停收音"
          note="使用扬声器时可避免把合成语音再次转述；耳机或隔离的虚拟声卡可按需关闭。"
          value={audio.suppress_during_playback}
          onChange={(suppress_during_playback) => update({ suppress_during_playback })}
        />
        <Toggle
          label="合成完成后自动播放"
          note="关闭后仍会完成合成，点击对话记录的播放按钮试听。"
          value={audio.auto_play}
          onChange={(auto_play) => update({ auto_play })}
        />
      </section>
    </>
  );
}
export function AppearancePage({ config, change }: { config: Config; change: Change }) {
  const a = config.appearance || { accent: "#79d9bc", mode: "dark" };
  const colors = [
    ["薄荷", "#79d9bc"],
    ["鸢尾", "#b8a0ee"],
    ["暮蓝", "#80b8ef"],
    ["琥珀", "#e8ba77"],
    ["蔷薇", "#e79fb1"],
    ["青柠", "#bfd87a"],
  ];
  return (
    <>
      <div className="page-intro">
        <div>
          <h2>给工作台，换一种心情</h2>
          <p>主题色会应用到按钮、选中状态、音量显示和工作流。保存后下次自动恢复。</p>
        </div>
        <Palette size={30} />
      </div>
      <section className="card settings-card">
        <h2>工作台模式</h2>
        <div className="theme-modes">
          {(["dark", "light"] as const).map((m) => (
            <button
              key={m}
              className={"theme-mode " + (a.mode === m ? "selected" : "")}
              onClick={() => change({ appearance: { ...a, mode: m } })}
            >
              {m === "dark" ? <Moon size={21} /> : <Sun size={21} />}
              <div>
                <b>{m === "dark" ? "深夜模式 · 雾蓝" : "浅色工作台"}</b>
                <small>
                  {m === "dark" ? "柔和暗蓝底色，适合长时间工作" : "清晰、轻盈，适合明亮环境"}
                </small>
              </div>
              {a.mode === m && <Check size={17} />}
            </button>
          ))}
        </div>
      </section>
      <section className="card settings-card">
        <h2>主题强调色</h2>
        <div className="color-grid">
          {colors.map(([n, c]) => (
            <button
              key={c}
              className={"color-choice " + (a.accent.toLowerCase() === c ? "selected" : "")}
              onClick={() => change({ appearance: { ...a, accent: c } })}
            >
              <span style={{ background: c }}>
                {a.accent.toLowerCase() === c && <Check size={18} />}
              </span>
              {n}
            </button>
          ))}
        </div>
        <Field label="自选颜色">
          <div className="row">
            <input
              aria-label="自选主题色"
              type="color"
              value={a.accent}
              onChange={(e) => change({ appearance: { ...a, accent: e.target.value } })}
            />
            <code>{a.accent.toUpperCase()}</code>
          </div>
        </Field>
        <div className="theme-preview">
          <AudioLines size={26} />
          <div>
            <b>你的表达，你的颜色</b>
            <p>主题预览 · Voice Loom</p>
          </div>
          <span className="chip active-chip">当前主题</span>
        </div>
      </section>
    </>
  );
}
export function HistoryPage({
  runtime,
  onEdit,
  onPlay,
}: {
  runtime: Runtime;
  onEdit: (e: Entry) => void;
  onPlay: (e: Entry) => void;
}) {
  const [tab, setTab] = useState("records"),
    [query, setQuery] = useState("");
  return (
    <>
      <div className="page-intro">
        <div>
          <h2>每一次表达，都有迹可循</h2>
          <p>查看原始转写、编排结果与运行状态，定位出错的环节。</p>
        </div>
        <a className="button" href="/api/export/session" download>
          <Download size={16} />
          导出会话
        </a>
      </div>
      <div className="toolbar">
        <div className="segmented">
          <button className={tab === "records" ? "active" : ""} onClick={() => setTab("records")}>
            对话记录
          </button>
          <button className={tab === "logs" ? "active" : ""} onClick={() => setTab("logs")}>
            运行日志
          </button>
        </div>
        <div className="search-box">
          <Search size={16} />
          <input
            aria-label="搜索记录"
            placeholder="搜索记录"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      <section className="card history-card">
        {tab === "records" ? (
          runtime.records
            .filter((r) => `${r.source_text}${r.text}${r.error || ""}`.includes(query))
            .map((e) => <EntryCard key={e.id} entry={e} onEdit={onEdit} onPlay={onPlay} />)
        ) : (
          <div className="log-list">
            {runtime.logs
              .filter((l) => l.message.includes(query))
              .map((l, i) => (
                <div className={"log-line " + l.level} key={i}>
                  <time>
                    {new Date(l.time).toLocaleTimeString("zh-CN", {
                      hour12: false,
                    })}
                  </time>
                  <span>{l.level}</span>
                  <p>{l.message}</p>
                </div>
              ))}
          </div>
        )}
        {!(tab === "records" ? runtime.records.length : runtime.logs.length) && (
          <div className="empty-small">
            <AudioLines size={27} />
            <p>运行后，记录会出现在这里。</p>
          </div>
        )}
      </section>
    </>
  );
}
