import { useEffect, useRef, useState } from "react";
import { FolderOpen, HelpCircle, Minus, MoreHorizontal, Plus, RefreshCw, Server, Settings2, Unplug, Waypoints } from "lucide-react";
import { Modal } from "./components";
import { api } from "./types";
import { ModelEnvironmentForm } from "./ModelEnvironmentForm";

interface Integration { codex_toml: string; mcp_json: string; data_directory: string; port: number }
interface Services { services: { id: string; name: string; url: string; available: boolean; running: boolean; managed: boolean }[]; sensevoice: { available: boolean; mode: string } }

export function DesktopTools() {
  const native = "__voiceLoomDesktop" in window;
  const menu = useRef<HTMLDetailsElement>(null);
  const [dialog, setDialog] = useState<"mcp" | "models" | "environment" | null>(null);
  const [integration, setIntegration] = useState<Integration | null>(null);
  const [services, setServices] = useState<Services | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState("");
  const [copied, setCopied] = useState("");
  const [zoom, setZoom] = useState(1);
  useEffect(() => {
    const display = (event: Event) => setZoom((event as CustomEvent<{ zoom: number }>).detail.zoom);
    window.addEventListener("voice-loom-display", display);
    return () => window.removeEventListener("voice-loom-display", display);
  }, []);
  useEffect(() => {
    const outside = (event: PointerEvent) => { if (menu.current && !menu.current.contains(event.target as Node)) menu.current.open = false; };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, []);
  const closeMenu = () => { if (menu.current) menu.current.open = false; };
  async function open(value: "mcp" | "models") {
    closeMenu(); setDialog(value); setError(""); setCopied("");
    try {
      if (value === "mcp") setIntegration(await api<Integration>("/integrations/mcp"));
      else setServices(await api<Services>("/runtimes"));
    } catch (problem) { setError(String(problem)); }
  }
  async function modelAction(id: string, action: "start" | "stop") {
    setWorking(id); setError("");
    try {
      await api(`/runtimes/${id}/${action}`, { method: "POST" });
      setServices(await api<Services>("/runtimes"));
    } catch (problem) { setError(String(problem)); }
    finally { setWorking(""); }
  }
  async function copy(name: string, value: string) {
    try { await navigator.clipboard.writeText(value); setCopied(name); }
    catch { setError("复制未完成，可选中文字后按 Ctrl+C。"); }
  }
  return <>
    <details className="desktop-tools" ref={menu} onKeyDown={event => { if (event.key === "Escape") closeMenu(); }}>
      <summary aria-label="更多工具" title="更多工具"><MoreHorizontal size={20} /></summary>
      <div className="desktop-tools-menu">
        <button onClick={() => void open("models")}><Server size={16} />本机模型服务</button>
        <button onClick={() => void open("mcp")}><Waypoints size={16} />连接 AI 助手 · MCP</button>
        {native && <>
          <div className="desktop-menu-separator" />
          <a href="voiceloom://desktop/data" onClick={closeMenu}><FolderOpen size={16} />打开数据目录</a>
          <a href="voiceloom://desktop/compare" onClick={closeMenu}><Unplug size={16} />模型对比试听</a>
          <a href="voiceloom://desktop/logs" onClick={closeMenu}><Settings2 size={16} />诊断日志</a>
          <a href="voiceloom://desktop/help" onClick={closeMenu}><HelpCircle size={16} />使用说明</a>
          <div className="desktop-menu-separator" />
          <div className="desktop-zoom"><span>文字大小</span><a aria-label="缩小文字" href="voiceloom://desktop/zoom-out"><Minus size={15} /></a><a href="voiceloom://desktop/zoom-reset" title="恢复100%">{Math.round(zoom * 100)}%</a><a aria-label="放大文字" href="voiceloom://desktop/zoom-in"><Plus size={15} /></a></div>
        </>}
      </div>
    </details>
    {dialog === "mcp" && <Modal title="连接 AI 助手" wide onClose={() => setDialog(null)}>
      <p className="muted">让 Codex、Claude 等助手直接使用声织的音色、识别、合成和本机模型服务。MCP 通过本机标准输入输出连接。</p>
      {integration ? <>
        <div className="integration-config"><div className="row between"><h3>Codex</h3><button onClick={() => void copy("codex", integration.codex_toml)}>{copied === "codex" ? "已复制" : "复制配置"}</button></div><pre tabIndex={0}>{integration.codex_toml}</pre><small>添加到 Codex 的 MCP 设置，或用户目录的 .codex/config.toml，重新连接后即可使用。</small></div>
        <div className="integration-config"><div className="row between"><h3>其他 MCP 客户端</h3><button onClick={() => void copy("json", integration.mcp_json)}>{copied === "json" ? "已复制" : "复制 JSON"}</button></div><pre tabIndex={0}>{integration.mcp_json}</pre></div>
        <p className="muted">助手和当前窗口共用同一份数据。合成默认保存文件，播放和开启麦克风使用独立操作。MCP 可在窗口关闭时使用。</p>
      </> : <p>正在读取本机配置…</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
    </Modal>}
    {dialog === "models" && <Modal title="本机模型服务" wide onClose={() => setDialog(null)}>
      <div className="row between"><p className="muted">接入用户已有的 Python、模型程序和权重，生成时按需启动。</p><button disabled={!!working} onClick={() => { setDialog("environment"); setError(""); }}><Plus size={15} />导入启动环境</button><button disabled={!!working} onClick={() => void open("models")}><RefreshCw size={15} />刷新</button></div>
      {services ? <div className="model-service-list">{services.services.map(service => <div className="model-service-row" key={service.id}><div><b>{service.name}</b><small>{service.url}</small></div><span className={"service-badge " + (service.running ? "running" : "")}>{service.running ? "已就绪" : service.available ? "已部署" : "未部署"}</span><button disabled={!!working || !service.available || (service.running && !service.managed)} onClick={() => void modelAction(service.id, service.running ? "stop" : "start")}>{working === service.id ? "正在处理…" : service.running ? (service.managed ? "停止" : "外部启动") : "启动"}</button></div>)}<p className="muted">SenseVoice：{services.sensevoice.available ? services.sensevoice.mode : "尚未部署"}</p></div> : <p>正在检查本机服务…</p>}
      <p className="muted">切换本机 TTS 会释放声织登记的上一套 TTS。MCP 的模型服务操作支持在窗口外独立启动。</p>
      {error && <p className="notice error" role="alert">{error}</p>}
    </Modal>}
    {dialog === "environment" && <Modal title="导入本机模型环境" wide onClose={() => setDialog(null)}><ModelEnvironmentForm onSaved={() => void open("models")} onBack={() => void open("models")} /></Modal>}
  </>;
}
