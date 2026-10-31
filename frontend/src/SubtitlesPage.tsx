import { useEffect, useState } from "react";
import { Captions, Copy, ExternalLink, Radio, Trash2 } from "lucide-react";
import { Field, Toggle } from "./components";
import { api, type Config, type SubtitleState } from "./types";

export function SubtitlesPage({
  config,
  change,
  run,
  notify,
}: {
  config: Config;
  change: (patch: Partial<Config>) => void;
  run: (fn: () => Promise<unknown>) => void;
  notify: (text: string, error?: boolean) => void;
}) {
  const [current, setCurrent] = useState<SubtitleState | null>(null);
  const [connected, setConnected] = useState(false);
  const [links, setLinks] = useState<{browser:string;json:string;events:string}|null>(null);
  const settings = config.subtitles || { enabled: true, display_seconds: 8 };
  const root = location.origin;
  useEffect(() => { void api<{browser:string;json:string;events:string}>("/access/outputs").then(setLinks).catch(() => notify("字幕连接地址读取失败，请重新打开此页", true)); }, [notify]);
  useEffect(() => {
    if (!current?.text || !current.expires_at) return;
    const eventId = current.event_id;
    const remaining = Math.max(0, Date.parse(current.expires_at) - Date.now());
    const timer = setTimeout(
      () => setCurrent((value) => (value?.event_id === eventId ? { ...value, text: "" } : value)),
      remaining,
    );
    return () => clearTimeout(timer);
  }, [current?.event_id, current?.expires_at]);
  useEffect(() => {
    const stream = new EventSource("/api/subtitles/events");
    const receive = (event: MessageEvent) => {
      try {
        setCurrent(JSON.parse(event.data));
        setConnected(true);
      } catch {
        setConnected(false);
      }
    };
    stream.addEventListener("subtitle", receive as EventListener);
    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    return () => stream.close();
  }, []);
  const copy = (value: string, name: string) =>
    run(async () => {
      await navigator.clipboard.writeText(value);
      notify(`${name}已复制`);
    });
  const outputs = [
    {
      title: "OBS 浏览器源",
      value: links?.browser || "正在读取字幕地址…",
      note: "在 OBS 添加“浏览器”来源，粘贴地址，建议宽 1920、高 1080。透明背景、白字描边，字幕自动更新。",
    },
    {
      title: "OBS 文本文件",
      value: current?.text_file || "正在读取输出路径…",
      note: "在 OBS 添加“文本 (GDI+)”，勾选“从文件读取”，选择此文件；字体、大小与位置可在 OBS 调整。",
    },
    {
      title: "当前字幕 · JSON",
      value: links?.json || `${root}/api/subtitles/current`,
      note: "供字幕制作软件读取，包含最终台词、审核状态、记录编号与到期时间。",
    },
    {
      title: "实时推送 · SSE",
      value: links?.events || `${root}/api/subtitles/events`,
      note: "接收 subtitle 事件；publish 更新台词，clear 清屏。支持自动重连和事件编号去重。",
    },
  ];
  return (
    <>
      <div className="page-intro">
        <div>
          <h2>让通过自检的表达，出现在直播画面</h2>
          <p>审核通过后立即输出纯台词；语气标签和声音动作保留在语音编排中。</p>
        </div>
        <Captions size={30} />
      </div>
      <section className="card settings-card">
        <div className="card-heading">
          <h2>字幕输出</h2>
          <span className={"chip " + (connected ? "active-chip" : "")}>
            <Radio size={13} /> {connected ? "实时连接" : "正在连接"}
          </span>
        </div>
        <Toggle
          label="输出审核通过的字幕"
          note="开关与停留时间在“保存更改”后生效。停止监听或新建会话时自动清屏。"
          value={settings.enabled}
          onChange={(enabled) => change({ subtitles: { ...settings, enabled } })}
        />
        <Field
          label="字幕停留时间（秒）"
          note="新字幕会替换上一句；超过此时间后透明画面和文本文件一起清空。"
        >
          <input
            type="number"
            min="2"
            max="60"
            value={settings.display_seconds}
            onChange={(e) =>
              change({ subtitles: { ...settings, display_seconds: +e.target.value } })
            }
          />
        </Field>
        <div className="subtitle-preview" aria-label="实时字幕预览" aria-live="polite">
          {current?.text ? (
            <p>{current.text}</p>
          ) : (
            <span>
              {current?.enabled === false ? "字幕输出已关闭" : "等待下一句通过自检的台词"}
            </span>
          )}
        </div>
        <div className="row between subtitle-preview-footer">
          <small>棋盘格表示透明区域 · 与浏览器源同步</small>
          <button
            onClick={() =>
              run(async () => {
                await api("/subtitles/clear", { method: "POST" });
                notify("当前字幕已清空");
              })
            }
          >
            <Trash2 size={15} /> 清空字幕
          </button>
        </div>
      </section>
      <div className="subtitle-outputs">
        {outputs.map((output, index) => (
          <section className="card settings-card" key={output.title}>
            <h2>{output.title}</h2>
            <p className="muted">{output.note}</p>
            <div className="subtitle-address">
              <input aria-label={output.title + "地址"} readOnly value={output.value} />
              <button
                aria-label={"复制" + output.title}
                disabled={index === 1 && !current?.text_file}
                onClick={() => copy(output.value, output.title)}
              >
                <Copy size={15} /> 复制
              </button>
            </div>
            {index === 0 && (
              <a className="text-button" href={links?.browser || "/subtitles"} target="_blank" rel="noreferrer">
                <ExternalLink size={14} /> 打开透明字幕页
              </a>
            )}
          </section>
        ))}
      </div>
      {current?.file_error && (
        <div className="notice error">文本文件暂未更新：{current.file_error}</div>
      )}
    </>
  );
}
