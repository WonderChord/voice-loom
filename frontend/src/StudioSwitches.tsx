import { ArrowUpRight, Contact, Volume2 } from "lucide-react";
import type { Config, Runtime } from "./types";

export type OutputTarget = "character" | "tts" | "tts_fallback";

// A state poll can still describe the old route just after a saved selection changes.
export function activeFailover(config: Config, runtime: Runtime) {
  const failover = runtime.tts_failover;
  return failover?.primary === config.active.tts && failover.fallback === config.active.tts_fallback
    ? failover : null;
}

export function StudioSwitches({
  config, runtime, busy, online, dirty, select, navigate,
}: {
  config: Config;
  runtime: Runtime;
  busy: boolean;
  online: boolean;
  dirty: boolean;
  select: (target: OutputTarget, id: string) => void;
  navigate: (page: string, providerId?: string) => void;
}) {
  const locked = busy || !online || dirty;
  const providers = config.providers.filter((p) => p.kind === "tts");
  const name = (id: string) => providers.find((p) => p.id === id)?.name || id;
  const { tts, tts_fallback: backup } = config.active;
  const card = config.characters.find((c) => c.id === config.active_character);
  const override = !!card?.tts && card.tts !== tts;
  const failover = activeFailover(config, runtime);
  return (
    <section className="switchboard card" aria-label="角色与语音合成">
      <div className="switch-fields">
        <div>
          <div className="switch-label">
            <label htmlFor="live-character"><Contact size={15} />当前角色</label>
            <button onClick={() => navigate("characters")}>管理角色<ArrowUpRight size={12} /></button>
          </div>
          {config.characters.length ? (
            <select id="live-character" value={config.active_character} disabled={locked}
              onChange={(e) => select("character", e.target.value)}>
              <option value="">不使用角色卡</option>
              {config.characters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          ) : (
            <button className="first-character" id="live-character" aria-label="创建第一张角色卡" onClick={() => navigate("characters")}>
              创建第一张角色卡<ArrowUpRight size={14} />
            </button>
          )}
        </div>
        <div>
          <div className="switch-label">
            <label htmlFor="live-tts"><Volume2 size={15} />主 TTS</label>
            <button onClick={() => navigate("models", tts)}>配置连接<ArrowUpRight size={12} /></button>
          </div>
          <select id="live-tts" value={tts} disabled={locked} onChange={(e) => select("tts", e.target.value)}>
            {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <div>
          <div className="switch-label"><label htmlFor="live-backup">备用 TTS</label><small>主服务不可达时启用</small></div>
          <select id="live-backup" value={backup || ""} disabled={locked}
            onChange={(e) => select("tts_fallback", e.target.value)}>
            <option value="">不启用</option>
            {backup === tts && <option value={tts}>暂停备用（与主 TTS 相同）</option>}
            {providers.filter((p) => p.id !== tts).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </div>
      <div className="switch-status">
        <span className={"output-route" + (failover ? " using-backup" : "")}>
          <i aria-hidden="true" />{failover ? "备用输出" : "当前输出"} · {failover ? name(failover.fallback) : name(tts)}
        </span>
        {dirty ? <span>请先保存或撤销顶部的更改，再切换。</span> :
          override ? <span>角色卡绑定「{name(card.tts)}」。<button disabled={locked}
            onClick={() => select("tts", card.tts)}>用回角色 TTS</button></span> :
          <span>切换对新提交的台词生效</span>}
      </div>
    </section>
  );
}
