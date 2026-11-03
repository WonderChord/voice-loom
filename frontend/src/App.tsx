import { useCallback, useEffect, useRef, useState } from "react";
import {
  AudioLines,
  Mic,
  Square,
  Users,
  Boxes,
  NotebookPen,
  SlidersHorizontal,
  History,
  Palette,
  Save,
  Check,
  AlertCircle,
  LoaderCircle,
  Volume2,
  Headphones,
  Download,
  Plus,
  ChevronRight,
  Captions,
  Contact,
} from "lucide-react";
import {
  api,
  type Config,
  type Voice,
  type Runtime,
  type Devices,
  type Entry,
  type Plan,
} from "./types";
import { Modal, PlanEditor } from "./components";
import { Dashboard } from "./Dashboard";
import { applyTheme } from "./theme";
import { SubtitlesPage } from "./SubtitlesPage";
import { DesktopTools } from "./DesktopTools";
import { DebugPanel } from "./DebugPanel";
import { CharactersPage } from "./CharactersPage";
import {
  VoicesPage,
  ModelsPage,
  PromptsPage,
  DevicesPage,
  AppearancePage,
  HistoryPage,
} from "./pages";
const nav = [
  { id: "studio", label: "工作台", icon: AudioLines },
  { id: "voices", label: "参考音色", icon: Users },
  { id: "models", label: "模型连接", icon: Boxes },
  { id: "characters", label: "角色卡与读音", icon: Contact },
  { id: "prompts", label: "人设与提示词", icon: NotebookPen },
  { id: "devices", label: "音频设备", icon: SlidersHorizontal },
  { id: "subtitles", label: "直播字幕", icon: Captions },
  { id: "history", label: "运行记录", icon: History },
  { id: "appearance", label: "外观主题", icon: Palette },
];
const emptyState: Runtime = {
  listening: false,
  playing: false,
  stage: "idle",
  level_db: -90,
  queue_size: 0,
  records: [],
  logs: [],
  last_error: null,
  uptime_s: 0,
};
export default function App() {
  const [page, setPage] = useState("studio"),
    [config, setConfig] = useState<Config | null>(null),
    [voices, setVoices] = useState<Voice[]>([]),
    [devices, setDevices] = useState<Devices>({
      inputs: [],
      outputs: [],
      default_input: -1,
      default_output: -1,
    }),
    [runtime, setRuntime] = useState(emptyState),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false),
    [online, setOnline] = useState(true),
    [toast, setToast] = useState<{ text: string; error: boolean } | null>(null),
    [editing, setEditing] = useState<{ entry: Entry; plan: Plan } | null>(null),
    [clearConfirm, setClearConfirm] = useState(false);
  const dirtyRef = useRef(false);
  useEffect(() => {
    document.documentElement.dataset.unsavedChanges = String(dirty);
    const guard = (event: BeforeUnloadEvent) => {
      if (dirty && !("__voiceLoomDesktop" in window)) {
        event.preventDefault(); event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  const notify = useCallback((text: string, error = false) => setToast({ text, error }), []);
  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await fn();
      } catch (e) {
        notify(e instanceof Error ? e.message : String(e), true);
      } finally {
        setBusy(false);
      }
    },
    [notify],
  );
  const refreshVoices = useCallback(async () => setVoices(await api<Voice[]>("/references")), []);
  const refreshDevices = useCallback(async () => setDevices(await api<Devices>("/devices")), []);
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [page]);
  useEffect(() => {
    void Promise.all([
      api<Config>("/config").then(setConfig),
      refreshVoices(),
      refreshDevices(),
    ]).catch((e) => notify(e.message, true));
  }, [notify, refreshVoices, refreshDevices]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const sync = async () => {
      try {
        if (!dirtyRef.current && !document.hidden) {
          const [saved, references] = await Promise.all([
            api<Config>("/config"), api<Voice[]>("/references"),
          ]);
          if (alive && !dirtyRef.current) {
            setConfig((previous) => JSON.stringify(previous) === JSON.stringify(saved) ? previous : saved);
            setVoices((previous) => JSON.stringify(previous) === JSON.stringify(references) ? previous : references);
          }
        }
      } catch {
        // The regular state poll displays connection failures.
      } finally {
        if (alive) timer = setTimeout(sync, 4000);
      }
    };
    timer = setTimeout(sync, 4000);
    return () => { alive = false; clearTimeout(timer); };
  }, []);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    let recordsRev: number | null = null;
    let logsRev: number | null = null;
    const poll = async () => {
      try {
        const params = new URLSearchParams();
        if (recordsRev !== null) params.set("since_records_rev", String(recordsRev));
        if (logsRev !== null) params.set("since_logs_rev", String(logsRev));
        const query = params.toString();
        const s = await api<Partial<Runtime> & Pick<Runtime, "listening" | "playing" | "stage" | "level_db" | "queue_size" | "last_error" | "uptime_s">>(
          query ? `/state?${query}` : "/state",
        );
        if (alive) {
          if (typeof s.records_revision === "number") recordsRev = s.records_revision;
          if (typeof s.logs_revision === "number") logsRev = s.logs_revision;
          setRuntime((prev) => ({
            ...s,
            records: s.records ?? prev.records,
            logs: s.logs ?? prev.logs,
          }));
          setOnline(true);
        }
      } catch {
        if (alive) setOnline(false);
      } finally {
        if (alive) timer = setTimeout(poll, 500);
      }
    };
    void poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    if (runtime.last_error) notify(runtime.last_error, true);
  }, [runtime.last_error, notify]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), toast.error ? 10000 : 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    if (!config) return;
    const a = config.appearance || { accent: "#79d9bc", mode: "dark" };
    applyTheme(a.accent, a.mode);
  }, [config?.appearance]);
  const change = (patch: Partial<Config>) => {
    dirtyRef.current = true;
    setConfig((c) => (c ? { ...c, ...patch } : c));
    setDirty(true);
  };
  const save = async (next = config) => {
    if (!next) throw new Error("设置尚未读取");
    const saved = await api<Config>("/config", {
      method: "PUT",
      body: JSON.stringify(next),
    });
    setConfig(saved);
    dirtyRef.current = false;
    setDirty(false);
    return saved;
  };
  const quick = async (patch: Partial<Config>) => {
    if (!config) return;
    await save({ ...config, ...patch });
  };
  const startStop = () =>
    run(async () => {
      if (runtime.listening) {
        await api("/listen/stop", { method: "POST" });
        notify("监听、队列和播放已停止");
      } else {
        if (dirty) await save();
        await api("/listen/start", { method: "POST" });
        notify("已开始持续监听，停顿后自动转述");
      }
    });
  const applyCharacter = (id: string) =>
    run(async () => {
      if (dirty) await save();
      const result = await api<{ config: Config; warnings: string[] }>(`/characters/${id}/apply`, {
        method: "POST",
      });
      setConfig(result.config);
      dirtyRef.current = false;
      setDirty(false);
      const name = result.config.characters.find((c) => c.id === id)?.name || "";
      notify(result.warnings.length ? result.warnings.join("；") : `已切换到角色「${name}」`, result.warnings.length > 0);
    });
  const editEntry = (entry: Entry) => {
    if (entry.plan) setEditing({ entry, plan: structuredClone(entry.plan) });
  };
  const play = (entry: Entry) =>
    run(async () => {
      await api(`/records/${entry.id}/play`, { method: "POST" });
    });
  if (!config)
    return (
      <div className="loading-screen">
        <AudioLines size={36} />
        <h1>声织</h1>
        <p>正在连接本机工作台…</p>
        {toast && <div className="notice error">{toast.text}</div>}
        <button onClick={() => location.reload()}>重新连接</button>
      </div>
    );
  const activeVoice = voices.find((v) => v.id === config.active.reference),
    activeTts = config.providers.find((p) => p.id === config.active.tts);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("studio");
          }}
        >
          <span className="brand-mark">
            <AudioLines size={30} />
          </span>
          <span>
            <b>声织</b>
            <small>VOICE LOOM</small>
          </span>
        </a>
        <div className="nav-label">创作空间</div>
        <nav>
          {nav.map((n) => (
            <button
              className={page === n.id ? "selected" : ""}
              key={n.id}
              onClick={() => setPage(n.id)}
            >
              <n.icon size={19} />
              <span>{n.label}</span>
              {page === n.id && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="session-card">
          <div className="row between">
            <span>当前会话</span>
            <span className={"dot " + (runtime.listening ? "live" : "")} />
          </div>
          <b>{runtime.listening ? "正在倾听你的表达" : "准备好，自然地说"}</b>
          <div className="session-count">
            <span>
              已完成 <strong>{runtime.records.filter((r) => r.status === "done").length}</strong>
            </span>
            <span>
              排队 <strong>{runtime.queue_size}</strong>
            </span>
          </div>
          <button className="subtle full" onClick={() => setClearConfirm(true)}>
            <Plus size={16} />
            新建会话
          </button>
        </div>
        <div className="local-note">
          <span className={"dot " + (online ? "live" : "bad")} />{" "}
          {online ? "本机工作空间" : "服务已断开"}
          <span>v1.3</span>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div>
            <div className="eyebrow">
              VOICE WORKSPACE <span>/</span>{" "}
              {String(nav.findIndex((n) => n.id === page) + 1).padStart(2, "0")}
            </div>
            <h1>{nav.find((n) => n.id === page)?.label}</h1>
          </div>
          <div className="top-actions">
            <DesktopTools />
            {dirty && (
              <button
                className="save-button"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await save();
                    notify("设置已保存");
                  })
                }
              >
                <Save size={16} />
                保存更改
              </button>
            )}
            <span className="mode-badge">
              <span className="dot live" />
              持续转述
            </span>
            <button
              className={runtime.listening ? "stop-button" : "primary"}
              disabled={busy || !online}
              onClick={startStop}
            >
              {busy ? (
                <LoaderCircle className="spin" size={17} />
              ) : runtime.listening ? (
                <Square size={16} />
              ) : (
                <Mic size={17} />
              )}{" "}
              {runtime.listening ? "停止监听" : "开始监听"}
            </button>
          </div>
        </header>
        {!online && (
          <div className="connection-banner">
            <AlertCircle size={16} />
            本机服务已断开。请重新运行启动程序，页面会自动重连。
          </div>
        )}
        <main className={"page-content " + (page === "studio" ? "studio-content" : "")}>
          {page === "studio" && (
            <Dashboard
              config={config}
              runtime={runtime}
              voices={voices}
              devices={devices}
              busy={busy}
              navigate={setPage}
              quick={(patch) => run(() => quick(patch))}
              onEdit={editEntry}
              onPlay={play}
              onRun={run}
              notify={notify}
              save={save}
              dirty={dirty}
              applyCharacter={applyCharacter}
            />
          )}
          {page === "voices" && (
            <VoicesPage
              config={config}
              voices={voices}
              onChange={change}
              refresh={refreshVoices}
              run={run}
              notify={notify}
              quick={(patch) => run(() => quick(patch))}
            />
          )}
          {page === "models" && (
            <ModelsPage config={config} change={change} save={save} run={run} notify={notify} />
          )}
          {page === "characters" && (
            <CharactersPage
              config={config}
              hotkeyStatus={runtime.hotkeys}
              voices={voices}
              change={change}
              save={save}
              applyCharacter={applyCharacter}
              dirty={dirty}
              run={run}
              notify={notify}
            />
          )}
          {page === "prompts" && (
            <PromptsPage config={config} change={change} save={save} run={run} notify={notify} />
          )}
          {page === "devices" && (
            <DevicesPage
              config={config}
              devices={devices}
              change={change}
              refresh={() => run(refreshDevices)}
              listening={runtime.listening}
              hotkeyStatus={runtime.hotkeys}
            />
          )}
          {page === "appearance" && <AppearancePage config={config} change={change} />}
          {page === "subtitles" && (
            <SubtitlesPage config={config} change={change} run={run} notify={notify} />
          )}
          {page === "history" && <HistoryPage runtime={runtime} onEdit={editEntry} onPlay={play} />}
        </main>
        <footer className="transport">
          <div className="transport-label">
            <span className="transport-icon">
              <Volume2 size={19} />
            </span>
            <div>
              <b>{runtime.playing ? "正在输出" : "语音输出"}</b>
              <small>{runtime.tts_failover ? `${runtime.tts_failover.fallback_name} · 备用` : activeTts?.name || "选择合成引擎"}</small>
            </div>
          </div>
          <span className="divider" />
          <Headphones size={16} />
          <select
            aria-label="快捷选择输出设备"
            value={config.audio.output_device ?? ""}
            disabled={runtime.listening || busy}
            onChange={(e) =>
              run(() =>
                quick({
                  audio: {
                    ...config.audio,
                    output_device: e.target.value === "" ? null : +e.target.value,
                  },
                }),
              )
            }
          >
            <option value="">系统默认输出</option>
            {devices.outputs.map((d) => (
              <option value={d.id} key={d.id}>
                {d.name} · {d.hostapi}
              </option>
            ))}
          </select>
          <div className="transport-wave">
            {Array.from({ length: 37 }, (_, i) => (
              <i
                key={i}
                style={{
                  height: `${runtime.playing ? 8 + ((i * 13) % 27) : 3}px`,
                }}
                className={runtime.playing ? "active" : ""}
              />
            ))}
          </div>
          <span className="transport-voice">{activeVoice?.name || "未选参考音色"}</span>
          <button
            className="icon"
            aria-label="停止播放"
            title="停止播放"
            onClick={() =>
              run(async () => {
                await api("/playback/stop", { method: "POST" });
              })
            }
          >
            <Square size={16} />
          </button>
          <span className="queue-pill">{runtime.queue_size} 条待处理</span>
          <DebugPanel runtime={runtime} />
        </footer>
      </div>
      {toast && (
        <div className={"toast " + (toast.error ? "error" : "")} role="status">
          {toast.error ? <AlertCircle size={18} /> : <Check size={18} />}
          <span>{toast.text}</span>
          <button className="icon" aria-label="关闭提示" onClick={() => setToast(null)}>
            ×
          </button>
        </div>
      )}
      {editing && (
        <Modal title="编辑表演稿" wide onClose={() => setEditing(null)}>
          <p className="muted">保留原始转写，调整台词、情绪与动作后生成一个新版本。</p>
          <blockquote>{editing.entry.source_text}</blockquote>
          <PlanEditor
            providerId={config.active.tts}
            value={editing.plan}
            onChange={(plan) => setEditing({ ...editing, plan })}
          />
          <div className="modal-actions">
            <button onClick={() => setEditing(null)}>取消</button>
            <button
              className="primary"
              disabled={
                busy ||
                !editing.plan.segments.some(
                  (s) => s.kind === "event" || (s.kind === "speech" && s.text?.trim()),
                )
              }
              onClick={() =>
                run(async () => {
                  if (dirty) await save();
                  await api(`/records/${editing.entry.id}/synthesize`, {
                    method: "POST",
                    body: JSON.stringify({ plan: editing.plan }),
                  });
                  setEditing(null);
                  notify("已加入合成队列");
                })
              }
            >
              <AudioLines size={16} />
              确认台词 · 按支持能力合成
            </button>
          </div>
        </Modal>
      )}
      {clearConfirm && (
        <Modal title="开始新会话" onClose={() => setClearConfirm(false)}>
          <p>当前监听会停止，界面记录与 LLM 上下文将清空。已生成音频继续保存在本机。</p>
          <a className="text-link" href="/api/export/session" download>
            <Download size={15} />
            先导出当前会话
          </a>
          <div className="modal-actions">
            <button onClick={() => setClearConfirm(false)}>返回</button>
            <button
              className="primary"
              onClick={() =>
                run(async () => {
                  await api("/session/clear", { method: "POST" });
                  setClearConfirm(false);
                  notify("新会话已就绪");
                })
              }
            >
              新建会话
              <ChevronRight size={16} />
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
