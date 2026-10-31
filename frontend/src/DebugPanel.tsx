import { useEffect, useRef, useState } from "react";
import { Bug, ChevronDown, X } from "lucide-react";
import { api, type Runtime, statusNames } from "./types";

interface ModelState { model: string; name?: string; stage: string; message: string; time: number; started_at: number; elapsed_seconds: number; pid?: number; log_tail?: string }
interface DebugState { models: ModelState[]; events: ModelState[] }
const names: Record<string, string> = { launching: "正在启动", waiting: "等待就绪", ready: "已就绪", error: "启动失败", stopped: "已停止", cancelled: "已取消等待" };

export function DebugPanel({ runtime }: { runtime: Runtime }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<DebugState>({ models: [], events: [] });
  const shown = useRef(new Set<string>());
  const activeTask = runtime.stage !== "idle" || runtime.queue_size > 0;
  useEffect(() => {
    let disposed = false;
    const update = async () => {
      if (!open && !activeTask && document.hidden) return;
      try {
        const value = await api<DebugState>(`/runtime/debug?include_logs=${open}`);
        if (disposed) return;
        setState(value);
        const recent = value.models.find(item => ["launching", "waiting", "error"].includes(item.stage) && Date.now() / 1000 - item.time < 30);
        if (recent) {
          const key = `${recent.model}:${recent.started_at}:${recent.stage === "error" ? "error" : "start"}`;
          if (!shown.current.has(key)) { shown.current.add(key); setOpen(true); }
        }
      } catch { /* Connection state is reported by the main workspace. */ }
    };
    void update();
    const intervalMs = open || activeTask ? 1500 : 6000;
    const timer = window.setInterval(() => void update(), intervalMs);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [open, activeTask]);
  const starting = state.models.find(item => ["launching", "waiting"].includes(item.stage));
  return <div className="debug-panel">
    <button className={"debug-toggle " + (starting ? "starting" : "")} aria-expanded={open} onClick={() => setOpen(!open)}><Bug size={15} />{starting ? "模型启动中" : "调试"}<ChevronDown size={13} /></button>
    {open && <section className="debug-drawer" aria-label="模型与任务调试" onKeyDown={event => { if (event.key === "Escape") setOpen(false); }}>
      <header><div><b>运行调试</b><span>{statusNames[runtime.stage] || runtime.stage} · 队列 {runtime.queue_size}</span></div><button className="icon" aria-label="收起调试栏" onClick={() => setOpen(false)}><X size={17} /></button></header>
      <div className="debug-body">
        {state.models.length > 0 && <div className="debug-models">{state.models.map(item => <div key={item.model} className={"debug-model " + item.stage}><div className="row between"><b>{item.name || item.model}</b><span>{names[item.stage] || item.stage} · {item.elapsed_seconds.toFixed(1)} 秒</span></div><p>{item.message}</p>{item.log_tail && <details><summary>查看模型日志</summary><pre>{item.log_tail}</pre></details>}</div>)}</div>}
        {state.events.length === 0 && runtime.logs.length === 0 && <p className="muted">等待任务。模型开始加载后，会显示启动、健康检查和失败原因。</p>}
        <div className="debug-events">{state.events.slice(0, 20).map((item, index) => <div key={`${item.model}-${item.time}-${index}`} className={item.stage === "error" ? "debug-error" : ""}><time>{new Date(item.time * 1000).toLocaleTimeString()}</time><b>{item.name || item.model}</b><span>{item.message}</span></div>)}{runtime.logs.slice(-15).reverse().map((item, index) => <div key={`${item.time}-${index}`} className={item.level === "error" ? "debug-error" : ""}><time>{new Date(item.time).toLocaleTimeString()}</time><b>工作台</b><span>{item.message}</span></div>)}</div>
      </div>
      {"__voiceLoomDesktop" in window && <footer><a href="voiceloom://desktop/data">打开数据与模型日志目录 ↗</a></footer>}
    </section>}
  </div>;
}
