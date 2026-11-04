import { useCallback, useEffect, useRef, useState } from "react";
import {
  AudioLines,
  Mic,
  Square,
  Save,
  Check,
  AlertCircle,
  LoaderCircle,
  Headphones,
  Download,
  ChevronRight,
  Undo2,
} from "lucide-react";
import {
  api,
  type Config,
  type Voice,
  type Runtime,
  type Devices,
  type Entry,
  type Plan,
  statusNames,
} from "./types";
import { Modal, PlanEditor } from "./components";
import { Dashboard, type Readiness } from "./Dashboard";
import { type StudioDraft } from "./StudioComposer";
import { applyTheme } from "./theme";
import { SubtitlesPage } from "./SubtitlesPage";
import { DesktopTools } from "./DesktopTools";
import { DebugPanel } from "./DebugPanel";
import { PageHeading, PageList, StudioMast } from "./StudioNav";
import { activeFailover, StudioSwitches, type OutputTarget } from "./StudioSwitches";
import { CharactersPage } from "./CharactersPage";
import {
  VoicesPage,
  ModelsPage,
  PromptsPage,
  DevicesPage,
  AppearancePage,
  HistoryPage,
} from "./pages";
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
    [menuOpen, setMenuOpen] = useState(false),
    [modelTarget, setModelTarget] = useState({ id: "", visit: 0 }),
    [draft, setDraft] = useState<StudioDraft>({ text: "", preview: false, file: null }),
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
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  useEffect(() => {
    let current = true;
    setReadiness(null);
    if (config && online && !dirty && !runtime.listening) {
      api<Readiness>("/listen/readiness")
        .then((value) => { if (current) setReadiness(value); })
        .catch(() => { /* A connection error is already shown by the working session. */ });
    }
    return () => { current = false; };
  }, [config, voices, dirty, online, runtime.listening]);
  const savedConfig = useRef<Config | null>(null);
  const revision = useRef(0);
  const operations = useRef(0);
  const navigate = (next: string, providerId = "") => {
    if (next === "models") setModelTarget((last) => ({ id: providerId || config?.active.tts || "", visit: last.visit + 1 }));
    setPage(next);
    setMenuOpen(false);
  };
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
      operations.current += 1;
      setBusy(true);
      try {
        await fn();
      } catch (e) {
        notify(e instanceof Error ? e.message : String(e), true);
      } finally {
        if (--operations.current === 0) setBusy(false);
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
    let current = true;
    void Promise.all([
      api<Config>("/config").then((value) => {
        if (current) { savedConfig.current = value; setConfig(value); }
      }),
      refreshVoices(),
      refreshDevices(),
    ]).catch((e) => { if (current) notify(e.message, true); });
    return () => { current = false; };
  }, [notify, refreshVoices, refreshDevices]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const sync = async () => {
      try {
        if (!dirtyRef.current && !document.hidden) {
          const before = revision.current;
          const [saved, references] = await Promise.all([
            api<Config>("/config"), api<Voice[]>("/references"),
          ]);
          if (alive && !dirtyRef.current && before === revision.current) {
            savedConfig.current = saved;
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
    const a = config.appearance || { accent: "#efb56c", mode: "dark" };
    applyTheme(a.accent, a.mode);
  }, [config?.appearance]);
  const change = (patch: Partial<Config>) => {
    revision.current += 1;
    dirtyRef.current = true;
    setConfig((c) => (c ? { ...c, ...patch } : c));
    setDirty(true);
  };
  const save = useCallback(async (next = config) => {
    if (!next) throw new Error("设置尚未读取");
    const before = ++revision.current;
    const saved = await api<Config>("/config", {
      method: "PUT",
      body: JSON.stringify(next),
    });
    savedConfig.current = saved;
    if (before === revision.current) {
      revision.current += 1;
      setConfig(saved);
      dirtyRef.current = false;
      setDirty(false);
    }
    return saved;
  }, [config]);
  const saveDraft = useCallback(() => run(async () => {
    await save();
    notify(dirtyRef.current ? "已保存，仍有刚修改的设置" : "设置已保存");
  }), [run, save, notify]);
  const discard = () => {
    if (!savedConfig.current) return;
    revision.current += 1;
    setConfig(savedConfig.current);
    dirtyRef.current = false;
    setDirty(false);
    notify("已恢复上次保存的设置");
  };
  useEffect(() => {
    const shortcut = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.key.toLowerCase() !== "s") return;
      e.preventDefault();
      if (dirtyRef.current && !operations.current && online && !e.repeat && !document.querySelector('[aria-modal="true"]')) void saveDraft();
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [online, saveDraft]);
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
  const selectOutput = (target: OutputTarget, id: string) =>
    run(async () => {
      if (dirtyRef.current) throw new Error("请先保存或撤销更改，再切换");
      const result = await api<{ config: Config; warnings: string[] }>("/output/select", {
        method: "POST", body: JSON.stringify({ target, id }),
      });
      const baseline = savedConfig.current;
      savedConfig.current = result.config;
      revision.current += 1;
      const hasNewEdits = dirtyRef.current;
      setConfig((pending) => {
        if (!hasNewEdits || !baseline || !pending) return result.config;
        // Keep edits started on another page while the switch was saving.
        const edits = Object.entries(pending).filter(([key, value]) =>
          JSON.stringify(value) !== JSON.stringify(baseline[key as keyof Config]));
        return { ...result.config, ...Object.fromEntries(edits) };
      });
      const name = (target === "character" ? result.config.characters : result.config.providers).find((p) => p.id === id)?.name;
      const message = target === "character" ? (id ? `已切换到角色「${name}」` : "已停用角色卡，保留当前声音和人设设置") :
        target === "tts" ? `主 TTS 已切换为「${name}」` : id ? `备用 TTS 已设为「${name}」` : "已关闭备用 TTS";
      notify([message, ...result.warnings].join("；"), result.warnings.length > 0);
    });
  const applyCharacter = (id: string) => selectOutput("character", id);
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
  const liveConfig = (dirty && savedConfig.current) || config;
  const activeVoice = voices.find((v) => v.id === liveConfig.active.reference),
    activeTts = liveConfig.providers.find((p) => p.id === liveConfig.active.tts);
  const failover = activeFailover(liveConfig, runtime);
  const sessionState = !online ? "已断开" : runtime.playing ? "播放中" :
    runtime.stage !== "idle" ? (statusNames[runtime.stage] || "处理中") :
    runtime.listening ? "监听中" : "待机";
  const setupIssue = !dirty && !runtime.listening && readiness && !readiness.ready ? readiness : null;
  return (
    <div className="app-shell">
      <header className="studio-mast">
        <StudioMast page={page} go={navigate} menuOpen={menuOpen} setMenuOpen={setMenuOpen}
          online={online} newSession={() => setClearConfirm(true)}>
          <DesktopTools />
          <button className={runtime.listening ? "stop-button mast-listen" : "primary mast-listen"}
            disabled={busy || !online} title={setupIssue?.message}
            onClick={() => setupIssue ? navigate(setupIssue.page, setupIssue.provider) : startStop()}>
            {busy ? <LoaderCircle className="spin" size={16}/> : runtime.listening ? <Square size={14}/> : <Mic size={16}/>}
            {runtime.listening ? "停止监听" : setupIssue ? "设置监听" : dirty ? "保存并监听" : "开始监听"}
          </button>
        </StudioMast>
          {dirty && <div className="settings-changes" aria-label="未保存的设置">
            <span><i />设置未保存</span>
            <button type="button" title="恢复上次保存的设置" disabled={busy} onClick={discard}><Undo2 size={14} />撤销更改</button>
            <button type="button" className="save-button" title="保存更改 (Ctrl + S)" disabled={busy || !online} onClick={saveDraft}>
              <Save size={14} />保存更改
            </button>
          </div>}
      </header>
      <div className="studio-shell-body">
        {!online && (
          <div className="connection-banner">
            <AlertCircle size={16} />
            本机服务已断开。请重新运行启动程序，页面会自动重连。
          </div>
        )}
        <div className="studio-layout">
        <PageList page={page} go={navigate} />
        <main className={"page-content " + (page === "studio" ? "studio-content" : "")}>
          <PageHeading page={page}>
            <span className={"session-badge" + (runtime.listening || runtime.playing ? " live" : "")}><i/>{sessionState}</span>
          </PageHeading>
          {page === "studio" && (
            <>
            <StudioSwitches config={liveConfig} runtime={runtime} busy={busy} online={online}
              dirty={dirty} navigate={navigate} select={selectOutput} />
            <Dashboard
              config={config}
              runtime={runtime}
              voices={voices}
              devices={devices}
              busy={busy}
              navigate={navigate}
              draft={draft}
              updateDraft={(patch) => setDraft((previous) => ({ ...previous, ...patch }))}
              online={online}
              readiness={readiness}
              quick={(patch) => run(() => quick(patch))}
              onEdit={editEntry}
              onPlay={play}
              onRun={run}
              notify={notify}
              save={save}
              dirty={dirty}
            />
            </>
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
            <ModelsPage key={modelTarget.visit} initialProviderId={modelTarget.id} config={config} change={change} save={save} run={run} notify={notify} />
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
              canSwitch={!dirty && !busy && online}
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
        </div>
        <footer className="transport booth-transport" aria-label="输出控制">
          <div className="transport-label">
            <span className="output-tag" aria-hidden="true">OUT</span>
            <div>
              <b>{runtime.playing ? "正在输出" : "语音输出"}</b>
              <small>{failover ? `${failover.fallback_name} · 备用` : activeTts?.name || "选择合成引擎"}</small>
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
          <div className="bus-line" aria-hidden="true" />
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
          <p>当前监听会停止，本场记录、对话上下文和本场用量将清空。已生成音频、累计用量和未提交草稿会保留。</p>
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
