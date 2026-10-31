import { useRef, useState } from "react";
import { FileUp, FolderCog } from "lucide-react";
import { Field } from "./components";
import { api } from "./types";

const empty = { id: "local-model", name: "我的本机模型", adapter: "openai_llm", executable: "", script: "", working_directory: "", arguments: [] as string[], model_files: [] as string[], base_url: "http://127.0.0.1:18082", health_path: "/v1/models", startup_seconds: 180 };
type Profile = typeof empty;
interface Inspection { checks: { name: string; path: string; ok: boolean }[]; command: string[]; ready: boolean }

export function ModelEnvironmentForm({ onSaved, onBack }: { onSaved: () => void; onBack: () => void }) {
  const [value, setValue] = useState<Profile>(empty);
  const [argumentsText, setArgumentsText] = useState("[]");
  const [modelsText, setModelsText] = useState("");
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const change = (patch: Partial<Profile>) => { setValue(previous => ({ ...previous, ...patch })); setInspection(null); };
  function payload() { return { ...value, arguments: JSON.parse(argumentsText), model_files: modelsText.split("\n").map(text => text.trim()).filter(Boolean) }; }
  async function load(input: File) {
    try {
      if (input.size > 256 * 1024) throw new Error("配置文件最多256KB");
      const loaded = JSON.parse(await input.text()) as Profile;
      setValue({ ...empty, ...loaded }); setArgumentsText(JSON.stringify(loaded.arguments || [], null, 2));
      setModelsText((loaded.model_files || []).join("\n")); setInspection(null); setError("");
    } catch { setError("配置文件格式有误，请选择声织模型环境 JSON。"); }
  }
  async function check() {
    setWorking(true); setError("");
    try { setInspection(await api<Inspection>("/model-environments/check", { method: "POST", body: JSON.stringify(payload()) })); }
    catch (problem) { setError(String(problem)); }
    finally { setWorking(false); }
  }
  async function save() {
    setWorking(true); setError("");
    try { await api("/model-environments", { method: "POST", body: JSON.stringify(payload()) }); onSaved(); }
    catch (problem) { setError(String(problem)); }
    finally { setWorking(false); }
  }
  return <div className="environment-form">
    <div className="row between"><p className="muted">接入已经部署的模型环境，检查后保存。保存时会保留现有 Python、模型及依赖文件。</p><button onClick={() => file.current?.click()}><FileUp size={15} />导入 JSON</button><input hidden type="file" ref={file} accept=".json,application/json" onChange={event => { const input = event.target.files?.[0]; if (input) void load(input); event.target.value = ""; }} /></div>
    <div className="environment-fields">
      <Field label="环境名称"><input value={value.name} onChange={event => change({ name: event.target.value })} /></Field>
      <Field label="环境 ID" note="以 local- 开头，保存同一 ID 可更新启动配置。"><input value={value.id} onChange={event => change({ id: event.target.value })} /></Field>
      <Field label="服务类型"><select value={value.adapter} onChange={event => change({ adapter: event.target.value })}><option value="openai_llm">LLM · OpenAI 兼容</option><option value="openai_asr">ASR · OpenAI 兼容</option><option value="openai_tts">TTS · OpenAI 兼容</option><option value="qwen_local">Qwen3-ASR</option><option value="breeze">Breeze TTS</option><option value="drama3_local">Drama3</option><option value="gptsovits">GPT-SoVITS</option></select></Field>
      <Field label="本机服务地址" note="模型连接页使用相同地址；部分接口可在地址后加 /v1。"><input value={value.base_url} onChange={event => change({ base_url: event.target.value })} /></Field>
      <Field label="Python 或模型程序" note="填写 python.exe、llama-server.exe 等程序的完整路径。"><input value={value.executable} placeholder="选择现有环境的可执行文件路径" onChange={event => change({ executable: event.target.value })} /></Field>
      <Field label="工作目录"><input value={value.working_directory} placeholder="模型服务运行时使用的目录" onChange={event => change({ working_directory: event.target.value })} /></Field>
      <Field label="Python 启动脚本（可选）"><input value={value.script} placeholder="Python 服务的 .py 文件；原生程序留空" onChange={event => change({ script: event.target.value })} /></Field>
      <Field label="健康检查路径"><input value={value.health_path} onChange={event => change({ health_path: event.target.value })} /></Field>
    </div>
    <Field label="启动参数" note={'使用 JSON 数组，例如 ["--host", "127.0.0.1", "--port", "18082"]。参数中的模型路径按原服务要求填写。'}><textarea rows={3} value={argumentsText} onChange={event => { setArgumentsText(event.target.value); setInspection(null); }} /></Field>
    <Field label="检查的模型文件（每行一个，可选）"><textarea rows={2} value={modelsText} placeholder="模型权重的完整路径" onChange={event => { setModelsText(event.target.value); setInspection(null); }} /></Field>
    {inspection && <div className="environment-inspection"><b>{inspection.ready ? "环境文件检查通过" : "请补齐以下文件或目录"}</b>{inspection.checks.map((item, index) => <div key={index} className={item.ok ? "" : "environment-missing"}><span>{item.ok ? "✓" : "○"} {item.name}</span><code>{item.path}</code></div>)}<small>保存后可在本机模型服务中启动；首次生成会检查健康接口。</small></div>}
    {error && <p className="notice error" role="alert">{error}</p>}
    <div className="modal-actions"><button onClick={onBack}>返回</button><button disabled={working} onClick={() => void check()}><FolderCog size={16} />检查环境</button><button className="primary" disabled={working || !inspection?.ready} onClick={() => void save()}>保存启动环境</button></div>
  </div>;
}
