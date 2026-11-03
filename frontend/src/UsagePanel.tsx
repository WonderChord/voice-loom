import { useEffect, useState } from "react";
import { api, type UsageRow, type UsageSummary } from "./types";

const count = (n: number) => n.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
const columns = [
  ["requests", "请求"], ["failures", "失败"], ["tokens_in", "输入 token"], ["tokens_out", "输出 token"],
  ["chars", "TTS 字数"], ["input_s", "输入音频 / 秒"], ["audio_s", "生成音频 / 秒"], ["fallbacks", "备用合成"],
] as const;
function UsageTable({ rows, title, note }: { rows: UsageRow[]; title: string; note: string }) {
  const total = rows.reduce((n, row) => n + row.requests, 0);
  const failed = rows.reduce((n, row) => n + row.failures, 0);
  return (
    <section className="card settings-card usage-card">
      <div className="usage-heading"><h2>{title}</h2>
        {rows.length > 0 && <span>{count(total)} 次请求 · {count(failed)} 次失败</span>}
      </div>
      <p className="muted">{note}</p>
      {rows.length ? (
        <div className="usage-scroll">
          <table className="usage-table" aria-label={title}>
            <thead>
              <tr>
                <th>服务</th>
                {columns.map(([key, label]) => <th key={key} scope="col">{label}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <th scope="row">
                    {row.name || row.id}
                    <small>{row.kind.toUpperCase()}</small>
                  </th>
                  {columns.map(([key, label]) => (
                    <td key={key} data-label={label}>{count(row[key])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-small">尚无用量，处理任务后会自动累计。</div>
      )}
    </section>
  );
}

export function UsagePanel() {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const result = await api<UsageSummary>("/usage?days=30");
        if (live) {
          setUsage(result);
          setError("");
        }
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : "用量读取失败");
      } finally {
        if (live) timer = setTimeout(refresh, 4000);
      }
    };
    void refresh();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, []);
  return (
    <div className="usage-panel">
      <p className="hint">
        每 4 秒更新。请求统计处理任务中的 HTTP 调用；连接测试和探活不计入。token
        按服务返回值累计，字数按成功合成的原始台词累计。
      </p>
      {(error || usage?.save_error) && (
        <div className="notice error-text" role="status">
          {error || usage?.save_error}
        </div>
      )}
      {usage ? (
        <>
          <UsageTable
            title="本场用量"
            rows={usage.session}
            note={`开始于 ${new Date(usage.session_started).toLocaleString("zh-CN")} · 新建会话后清零`}
          />
          <UsageTable
            title="近 30 天"
            rows={usage.providers}
            note="累计记录保存在本机；新建会话后保留。备用合成按成功完成的句子计数。"
          />
        </>
      ) : (
        !error && <p className="muted">正在读取用量…</p>
      )}
    </div>
  );
}
