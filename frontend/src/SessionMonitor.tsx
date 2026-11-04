import { useMemo } from "react";
import { ArrowUpRight, Mic, Scissors, SlidersHorizontal } from "lucide-react";
import { type Config, type Devices, type Runtime } from "./types";

export function SessionMonitor({
  config,
  runtime,
  devices,
  busy,
  selectInput,
  openDevices,
  openHistory,
  flush,
}: {
  config: Config;
  runtime: Runtime;
  devices: Devices;
  busy: boolean;
  selectInput: (id: number | null) => void;
  openDevices: () => void;
  openHistory: () => void;
  flush: () => void;
}) {
  const counts = useMemo(() => {
    let done = 0,
      review = 0;
    for (const item of runtime.records) {
      if (item.status === "done") done++;
      if (item.status === "review") review++;
    }
    return [done, runtime.queue_size, review];
  }, [runtime.records, runtime.queue_size]);
  const db = Number.isFinite(runtime.level_db) ? runtime.level_db : -90;
  const units = (value: number) => Math.min(1, Math.max(0, (value + 60) / 60));
  const active = runtime.listening ? Math.round(units(db) * 36) : 0;
  return (
    <section className="monitor-panel" aria-label="本场监听与记录">
      <div className="meter-block">
        <div className="meter-heading">
          <h2>
            <span className="port-tag">IN 01</span>输入监听
          </h2>
          <div className="monitor-device">
            <Mic size={14} aria-hidden="true" />
            <select
              aria-label="快捷选择输入设备"
              disabled={busy || runtime.listening}
              value={config.audio.input_device ?? ""}
              onChange={(e) => selectInput(e.target.value === "" ? null : +e.target.value)}
            >
              <option value="">系统默认麦克风</option>
              {devices.inputs.map((d) => (
                <option value={d.id} key={d.id}>
                  {d.name} · {d.hostapi}
                </option>
              ))}
            </select>
            <button className="icon" aria-label="音频设置" title="音频设置" onClick={openDevices}>
              <SlidersHorizontal size={15} />
            </button>
          </div>
        </div>
        <div className="meter-readout">
          <div className="db-value">
            <b>{runtime.listening ? Math.round(db) : "—"}</b>
            <small>dBFS</small>
          </div>
          <div
            className="meter-scale"
            role="img"
            aria-label={
              runtime.listening ? "输入电平 " + Math.round(db) + " dBFS" : "输入电平：麦克风待机"
            }
          >
            <div className="level-bank" aria-hidden="true">
              {Array.from({ length: 36 }, (_, i) => (
                <i
                  key={i}
                  className={
                    (i < active ? "lit " : "") + (i >= 32 ? "peak" : i >= 27 ? "warm" : "")
                  }
                />
              ))}
              {config.audio.skip_low_energy && (
                <span
                  className="gate-line"
                  style={{ left: units(config.audio.energy_threshold_db) * 100 + "%" }}
                  title={"低音量阈值 " + config.audio.energy_threshold_db + " dBFS"}
                />
              )}
            </div>
            <div className="meter-ticks" aria-hidden="true">
              {["−60", "−48", "−36", "−24", "−12", "0"].map((n) => (
                <span key={n}>{n}</span>
              ))}
            </div>
          </div>
        </div>
        <div className="monitor-state">
          <span className={"mic-state" + (runtime.listening ? " live" : "")}>
            <i />
            {!runtime.listening
              ? "麦克风待机"
              : runtime.playing && config.audio.suppress_during_playback
                ? "播放防回授"
                : "正在收音"}
          </span>
          <span>断句 {config.audio.pause_ms} ms</span>
          {config.audio.skip_low_energy && (
            <span title={runtime.last_skip_reason || ""}>
              过滤 {config.audio.energy_threshold_db} dBFS · 已跳过{" "}
              {runtime.skipped_audio_count || 0}
            </span>
          )}
          {!!runtime.dropped_audio_count && (
            <b className="error-text">队列已满 · 丢弃 {runtime.dropped_audio_count} 段</b>
          )}
          {runtime.listening && (
            <button onClick={flush} disabled={busy}>
              <Scissors size={13} />
              提交当前片段
            </button>
          )}
        </div>
        {runtime.listening && runtime.tts_remote && (
          <small className="probe-note" title={runtime.tts_remote.message}>
            {runtime.tts_remote.provider} ·{" "}
            {runtime.tts_remote.ok ? "已连通 " + runtime.tts_remote.rtt_ms + " ms" : "无法连接"} ·{" "}
            {new Date(runtime.tts_remote.checked_at).toLocaleTimeString()}
          </small>
        )}
      </div>
      <div className="session-totals">
        <div className="totals-heading">
          <span>本场计数</span>
          <button onClick={openHistory} aria-label="查看记录与用量" title="查看记录与用量">
            <ArrowUpRight size={16} />
          </button>
        </div>
        <dl>
          {["已完成", "排队中", "待确认"].map((label, i) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd className={counts[i] > 0 && i === 2 ? "needs-review" : ""}>
                {String(counts[i]).padStart(2, "0")}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
