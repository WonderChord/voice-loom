import { useEffect, useState } from "react";
import { Save, Plus, RotateCcw, ShieldCheck, Check } from "lucide-react";
import {
  api,
  type Config,
  type Capabilities,
  type StylePreset,
  type StyleStrength,
  type TargetLanguage,
  targetLanguageNames,
  processingModeLabel,
  llmConnectionIssue,
} from "./types";
import { Field, Toggle } from "./components";

const strengths: Record<StyleStrength, string> = {
  off: "原意保留",
  light: "轻度整理",
  balanced: "自然角色",
  strong: "鲜明角色",
};
type Props = {
  config: Config;
  change: (patch: Partial<Config>) => void;
  save: (next?: Config) => Promise<Config>;
  run: (fn: () => Promise<unknown>) => void;
  notify: (text: string, error?: boolean) => void;
};

export function PromptsPage({ config, change, save, run, notify }: Props) {
  const [section, setSection] = useState<"performance" | "character">("performance");
  const [ruleProvider, setRuleProvider] = useState(config.active.tts);
  const [caps, setCaps] = useState<Record<string, Capabilities>>({});
  const [presetName, setPresetName] = useState("");
  const tts = config.providers.filter((p) => p.kind === "tts");
  const provider = tts.find((p) => p.id === ruleProvider) || tts[0];
  const capability = caps[provider.id];
  const ruleKey = provider.model.trim() || "__default__";
  const custom = provider.performance_rules?.[ruleKey] || "";
  const presets = config.style_presets || [];
  const selected = presets.find((p) => p.id === config.active_style_preset);
  const targetLanguage = config.target_language ?? "original";
  const translating = targetLanguage !== "original";
  const processingMode = processingModeLabel(targetLanguage, config.rewrite_enabled);
  const llmIssue = llmConnectionIssue(
    config.providers.find((p) => p.kind === "llm" && p.id === config.active.llm),
  );

  useEffect(() => {
    let alive = true;
    api<Record<string, Capabilities>>("/capabilities")
      .then((value) => {
        if (alive) setCaps(value);
      })
      .catch(() => {
        if (alive) setCaps({});
      });
    return () => {
      alive = false;
    };
  }, [config.providers]);
  useEffect(() => {
    setPresetName(selected?.name || "");
  }, [selected?.id, selected?.name]);

  const setRules = (value: string | null) => {
    const rules = { ...provider.performance_rules };
    if (value === null) delete rules[ruleKey];
    else rules[ruleKey] = value;
    change({
      providers: config.providers.map((p) =>
        p.id === provider.id ? { ...p, performance_rules: rules } : p,
      ),
    });
  };
  const applyPreset = (preset: StylePreset) => {
    setPresetName(preset.name);
    change({
      active_style_preset: preset.id,
      persona: preset.persona,
      prompt: preset.prompt,
      style_strength: preset.strength,
    });
  };
  const storePreset = (overwrite: boolean) =>
    run(async () => {
      const name = presetName.trim();
      if (!name) throw new Error("请给角色风格起一个名称");
      if (!overwrite && presets.length >= 40) throw new Error("最多保存 40 个预设，可覆盖已有预设");
      const preset: StylePreset = {
        id: overwrite && selected ? selected.id : `style-${Date.now()}`,
        name,
        persona: config.persona,
        prompt: config.prompt,
        strength: config.style_strength,
      };
      const list =
        overwrite && selected
          ? presets.map((p) => (p.id === selected.id ? preset : p))
          : [...presets, preset];
      await save({ ...config, style_presets: list, active_style_preset: preset.id });
      notify(`已保存角色预设「${name}」`);
    });
  return (
    <>
      <div className="page-intro">
        <div>
          <h2>语气跟随模型，风格属于角色</h2>
          <p>选择输出语言，再按需要调整角色风格与语气。</p>
        </div>
      </div>
      <section className="card settings-card">
        <div className="form-grid">
          <Field label="目标语言（LLM翻译）" note="保留原意、称呼和人称；与工作台使用同一设置。">
            <select
              aria-label="目标语言（LLM翻译）"
              value={targetLanguage}
              onChange={(e) => change({ target_language: e.target.value as TargetLanguage })}
            >
              {Object.entries(targetLanguageNames).map(([value, label]) => (
                <option value={value} key={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <div className="rule-summary">
            <b>{processingMode}</b>
            <small>
              {translating
                ? "翻译调用当前 LLM，并进行独立自检。请先在模型连接中完成配置。"
                : config.rewrite_enabled
                  ? "保持原语言，按角色风格整理措辞并自检。"
                  : "保持原语言与措辞，完成本地指令复核后合成。"}
            </small>
            <small>参考音色的语言在音色库中单独设置。</small>
            {translating && llmIssue && (
              <small className="danger-text" role="status">
                {llmIssue}
              </small>
            )}
          </div>
        </div>
        <Toggle
          label="角色润色与语气补全（LLM 优化）"
          note={
            translating
              ? "开启后翻译并按角色风格润色；关闭后仅翻译。"
              : "开启后按角色风格和当前 TTS 规则整理表达；关闭后原文直通。"
          }
          value={config.rewrite_enabled}
          onChange={(rewrite_enabled) => change({ rewrite_enabled })}
        />
        <Toggle
          label="独立防漏读审核（LLM 自检）"
          note="开启后使用独立 LLM 请求检查语气/舞台描述是否漏入朗读正文；关闭后仅执行本地正则复核。"
          value={config.audit_enabled ?? true}
          onChange={(audit_enabled) => change({ audit_enabled })}
        />
      </section>
      <div className="toolbar">
        <div className="segmented">
          <button
            className={section === "performance" ? "active" : ""}
            onClick={() => setSection("performance")}
          >
            模型语气补全
          </button>
          <button
            className={section === "character" ? "active" : ""}
            onClick={() => setSection("character")}
          >
            角色风格预设
          </button>
        </div>
      </div>
      <section className="card prompt-card" hidden={section !== "performance"}>
        <div className="section-number">
          01 <span>MODEL PERFORMANCE</span>
        </div>
        <div className="row between">
          <div>
            <h2>模型语气补全规则</h2>
            <p>每个模型保存自己的规则，生成时使用当前工作台 TTS 对应的版本。</p>
          </div>
          <span className="chip active-chip">
            {provider.id === config.active.tts ? "当前工作台 TTS" : "编辑其他模型"}
          </span>
        </div>
        <div className="form-grid">
          <Field label="规则所属 TTS">
            <select
              aria-label="规则所属 TTS"
              value={provider.id}
              onChange={(e) => setRuleProvider(e.target.value)}
            >
              {tts.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <div className="rule-summary">
            <b>{capability?.rule_title || "保存模型连接后加载规则"}</b>
            <small>{provider.model || "本机默认模型"}</small>
            <small>{custom ? "已使用自定义规则" : "使用模型默认规则"}</small>
          </div>
        </div>
        <Field label="模型专属语气规则" note="按连接与模型名分别保存；切换模型名后读取对应规则。">
          <textarea
            aria-label="模型专属语气规则"
            rows={5}
            maxLength={16000}
            value={provider.performance_rules?.[ruleKey] ?? capability?.default_rules ?? ""}
            onChange={(e) => setRules(e.target.value)}
            placeholder="保存模型连接后可编辑默认规则。"
          />
        </Field>
        <div className="row between wrap">
          <div className="chips">
            {capability?.whisper && <span className="chip active-chip">耳语</span>}
            {capability?.instructions && <span className="chip active-chip">情绪语气</span>}
            {capability?.events?.length > 0 && (
              <span className="chip active-chip">叹气 / 轻笑 / 呼吸</span>
            )}
          </div>
          <button className="text-button" onClick={() => setRules(null)}>
            <RotateCcw size={14} />
            恢复模型默认规则
          </button>
        </div>
        {!!capability?.syntax_examples?.length && (
          <div className="rule-examples">
            <small>合成时转换的标签示例与标签规范</small>
            {capability.tag_spec?.whisper_tag && (
              <code>耳语标签：{capability.tag_spec.whisper_tag}</code>
            )}
            {capability.tag_spec && Object.keys(capability.tag_spec.event_tags).length > 0 && (
              <code>
                动作标签：
                {Object.entries(capability.tag_spec.event_tags)
                  .map(([k, v]) => `${k}=${v}`)
                  .join("  ")}
              </code>
            )}
            {capability.syntax_examples.map((sample, i) => (
              <code key={i}>{sample}</code>
            ))}
          </div>
        )}
        <Field label="通用语音偏好" note="适用于所有模型的自然停顿、情绪张力与语气表现偏好。">
          <textarea
            aria-label="通用语音偏好"
            rows={3}
            maxLength={16000}
            value={config.speech_prompt}
            onChange={(e) => change({ speech_prompt: e.target.value })}
          />
        </Field>
        <div className="form-grid">
          <Field label={`情绪强度 · ${Math.round(config.emotion_intensity * 100)}%`}>
            <input
              type="range"
              min="0"
              max="1"
              step=".05"
              value={config.emotion_intensity}
              onChange={(e) => change({ emotion_intensity: +e.target.value })}
            />
          </Field>
          <Toggle
            label="允许叹气、轻笑与呼吸"
            note="实际安排依据当前 TTS 能力，不支持的动作自动略过。"
            value={config.allow_events}
            onChange={(allow_events) => change({ allow_events })}
          />
        </div>
      </section>

      <section className="card prompt-card" hidden={section !== "character"}>
        <div className="section-number">
          02 <span>CHARACTER STYLE</span>
        </div>
        <h2>角色润色风格</h2>
        <p>选择预设后可自由定制人设性格、口癖与改写幅度，保存为专属角色风格。</p>
        <div className="style-presets">
          {presets.map((preset) => (
            <button
              key={preset.id}
              className={"style-preset " + (selected?.id === preset.id ? "selected" : "")}
              onClick={() => applyPreset(preset)}
            >
              <b>{preset.name}</b>
              <small>{strengths[preset.strength]}</small>
              {selected?.id === preset.id && <Check size={15} />}
            </button>
          ))}
        </div>
        <div className="form-grid">
          <Field label="润色程度">
            <select
              aria-label="润色程度"
              value={config.style_strength}
              onChange={(e) => change({ style_strength: e.target.value as StyleStrength })}
            >
              {Object.entries(strengths).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="预设名称">
            <input
              aria-label="预设名称"
              maxLength={100}
              value={presetName}
              onChange={(e) => setPresetName(e.target.value)}
              placeholder="例如：毒舌大小姐 / 慵懒狐娘 / 冷峻指挥官"
            />
          </Field>
        </div>
        <div className="prompt-grid">
          <Field label="目标角色人设">
            <textarea
              aria-label="目标角色人设"
              rows={5}
              maxLength={12000}
              value={config.persona}
              onChange={(e) => change({ persona: e.target.value })}
            />
          </Field>
          <Field label="微重构规则">
            <textarea
              aria-label="微重构规则"
              rows={5}
              maxLength={16000}
              value={config.prompt}
              onChange={(e) => change({ prompt: e.target.value })}
            />
          </Field>
        </div>
        <div className="row wrap">
          <button className="primary" onClick={() => storePreset(false)}>
            <Plus size={15} />
            另存为新预设
          </button>
          <button disabled={!selected} onClick={() => storePreset(true)}>
            <Save size={15} />
            覆盖当前预设
          </button>
          <small className="muted">预设保存人设、微重构规则和润色程度。</small>
        </div>
      </section>
      <div className="prompt-grid">
        <section className="card settings-card">
          <h2>上下文记忆</h2>
          <Field
            label={`${config.context_limit} 条历史消息`}
            note="设为 0 时每句独立处理，新建会话清空上下文。"
          >
            <input
              aria-label="上下文条数上限"
              type="number"
              min="0"
              max="50"
              value={config.context_limit}
              onChange={(e) => change({ context_limit: +e.target.value })}
            />
          </Field>
        </section>
        <section className="card settings-card review-info">
          <ShieldCheck size={25} />
          <h2>防语气指令漏读自检</h2>
          <p>
            自动剥离“用耳语”“语速加快”等自然语言控制说明，防止语气描述被误读成语音；角色口癖与情绪张力正常放行。
          </p>
        </section>
      </div>
    </>
  );
}
