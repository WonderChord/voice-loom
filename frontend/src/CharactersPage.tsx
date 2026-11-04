import { useState } from "react";
import { Check, Copy, Plus, Save, Trash2, UserRound, Wand2 } from "lucide-react";
import {
  api,
  type Character,
  type Config,
  type Pronunciation,
  type StyleStrength,
  type TargetLanguage,
  type Voice,
  type Runtime,
  targetLanguageNames,
} from "./types";
import { Field, Toggle } from "./components";
import { HotkeyField } from "./HotkeyField";

const strengths: Record<StyleStrength, string> = {
  off: "原意保留",
  light: "轻度整理",
  balanced: "自然角色",
  strong: "鲜明角色",
};

type Props = {
  config: Config;
  hotkeyStatus: Runtime["hotkeys"];
  voices: Voice[];
  change: (patch: Partial<Config>) => void;
  save: (next?: Config) => Promise<Config>;
  applyCharacter: (id: string) => void;
  dirty: boolean;
  canSwitch: boolean;
  run: (fn: () => Promise<unknown>) => void;
  notify: (text: string, error?: boolean) => void;
};

const emptyEntry = (): Pronunciation => ({
  text: "",
  say: "",
  case_sensitive: false,
  enabled: true,
  providers: [],
  note: "",
});

function fromCurrent(config: Config, name: string): Character {
  return {
    id: `char-${Date.now()}`,
    name,
    note: "",
    reference: config.active.reference,
    tts: config.active.tts,
    persona: config.persona,
    prompt: config.prompt,
    style_strength: config.style_strength,
    rewrite_enabled: config.rewrite_enabled,
    target_language: config.target_language ?? "original",
    emotion_intensity: config.emotion_intensity,
    pronunciations: [],
    hotkey: "",
  };
}

export function PronunciationEditor({
  entries,
  onChange,
  config,
  title,
  note,
}: {
  entries: Pronunciation[];
  onChange: (next: Pronunciation[]) => void;
  config: Config;
  title: string;
  note: string;
}) {
  const tts = config.providers.filter((p) => p.kind === "tts");
  const update = (index: number, patch: Partial<Pronunciation>) =>
    onChange(entries.map((e, i) => (i === index ? { ...e, ...patch } : e)));
  return (
    <div className="pron-editor">
      <div className="row between">
        <div>
          <b>{title}</b>
          <small className="muted"> {note}</small>
        </div>
        <button className="text-button" onClick={() => onChange([...entries, emptyEntry()])}>
          <Plus size={14} />
          添加词条
        </button>
      </div>
      {entries.length === 0 && <p className="muted">还没有词条。</p>}
      {entries.length > 0 && (
        <div className="pron-table" role="table" aria-label={title}>
          <div className="pron-row pron-head" role="row">
            <span>启用</span>
            <span>原词</span>
            <span>读作（送给 TTS 的写法）</span>
            <span>只对此 TTS</span>
            <span>区分大小写</span>
            <span />
          </div>
          {entries.map((entry, index) => (
            <div className="pron-row" role="row" key={index}>
              <input
                type="checkbox"
                aria-label="启用词条"
                checked={entry.enabled}
                onChange={(e) => update(index, { enabled: e.target.checked })}
              />
              <input
                aria-label="原词"
                maxLength={100}
                value={entry.text}
                placeholder="例如：乃爱"
                onChange={(e) => update(index, { text: e.target.value })}
              />
              <input
                aria-label="读作"
                maxLength={200}
                value={entry.say}
                placeholder="例如：奶爱 / Noa"
                onChange={(e) => update(index, { say: e.target.value })}
              />
              <select
                aria-label="只对此 TTS"
                value={entry.providers[0] || ""}
                onChange={(e) => update(index, { providers: e.target.value ? [e.target.value] : [] })}
              >
                <option value="">全部 TTS</option>
                {tts.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <input
                type="checkbox"
                aria-label="区分大小写"
                checked={entry.case_sensitive}
                onChange={(e) => update(index, { case_sensitive: e.target.checked })}
              />
              <button
                className="icon"
                aria-label="删除词条"
                onClick={() => onChange(entries.filter((_, i) => i !== index))}
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function CharactersPage({ config, hotkeyStatus, voices, change, save, applyCharacter, dirty, canSwitch, run, notify }: Props) {
  const cards = config.characters || [];
  const [editingId, setEditingId] = useState(config.active_character || cards[0]?.id || "");
  const [newName, setNewName] = useState("");
  const [sample, setSample] = useState("");
  const [preview, setPreview] = useState("");
  const editing = cards.find((c) => c.id === editingId);
  const tts = config.providers.filter((p) => p.kind === "tts");
  const providerName = (id: string) => tts.find((p) => p.id === id)?.name || "未指定";
  const voiceName = (id: string) => voices.find((v) => v.id === id)?.name || (id ? "已删除" : "不切换");

  const setCards = (next: Character[]) => change({ characters: next });
  const updateCard = (patch: Partial<Character>) =>
    editing && setCards(cards.map((c) => (c.id === editing.id ? { ...c, ...patch } : c)));

  const create = () =>
    run(async () => {
      const name = newName.trim();
      if (!name) throw new Error("请给角色卡起一个名称");
      if (cards.length >= 40) throw new Error("最多保存 40 张角色卡");
      const card = fromCurrent(config, name);
      await save({ ...config, characters: [...cards, card] });
      setEditingId(card.id);
      setNewName("");
      notify(`已用当前设置创建角色卡「${name}」`);
    });
  const duplicate = () =>
    editing &&
    run(async () => {
      if (cards.length >= 40) throw new Error("最多保存 40 张角色卡");
      const copy = { ...structuredClone(editing), id: `char-${Date.now()}`, name: editing.name + " 副本", hotkey: "" };
      await save({ ...config, characters: [...cards, copy] });
      setEditingId(copy.id);
    });
  const remove = () =>
    editing &&
    run(async () => {
      const next = cards.filter((c) => c.id !== editing.id);
      await save({
        ...config,
        characters: next,
        active_character: config.active_character === editing.id ? "" : config.active_character,
      });
      setEditingId(next[0]?.id || "");
      notify(`已删除角色卡「${editing.name}」`);
    });
  const captureCurrent = () =>
    editing && updateCard({ ...fromCurrent(config, editing.name), id: editing.id, note: editing.note, pronunciations: editing.pronunciations, hotkey: editing.hotkey });
  const runPreview = () =>
    run(async () => {
      if (dirty) await save();
      const result = await api<{ text: string }>("/pronunciations/preview", {
        method: "POST",
        body: JSON.stringify({ text: sample }),
      });
      setPreview(result.text);
    });

  return (
    <>
      <div className="page-intro">
        <div>
          <h2>角色卡与读音</h2>
          <p>角色卡一键切换音色、TTS、人设和语言；读音词典只改送给 TTS 的文字，字幕和记录保留原词。</p>
        </div>
      </div>

      <section className="card prompt-card">
        <div className="section-number">
          01 <span>CHARACTERS</span>
        </div>
        <h2>角色卡</h2>
        <p>{dirty ? "先保存或撤销更改，再使用角色卡。" : "点卡片查看或编辑；点「使用此卡」才会切换，下一句生效。"}</p>
        <div className="style-presets">
          {cards.map((card) => (
            <div
              key={card.id}
              className={"style-preset character-card " + (editingId === card.id ? "selected" : "")}
            >
              <button className="character-pick" aria-label={"编辑角色卡：" + card.name} aria-pressed={editingId === card.id}
                onClick={() => setEditingId(card.id)}>
                <b>
                  {card.name}
                  {config.active_character === card.id && <span className="card-in-use"><Check size={12} />使用中</span>}
                </b>
                <small>
                  {voiceName(card.reference)} · {providerName(card.tts)}
                </small>
                <small>
                  {targetLanguageNames[card.target_language]} · {strengths[card.style_strength]}
                </small>
                {card.hotkey && <small>{card.hotkey}</small>}
              </button>
              <button
                className={config.active_character === card.id ? "" : "primary"}
                disabled={!canSwitch}
                onClick={() => applyCharacter(card.id)}
              >
                {config.active_character === card.id ? "重新应用" : "使用此卡"}
              </button>
            </div>
          ))}
        </div>
        <div className="row wrap">
          <input
            aria-label="新角色卡名称"
            maxLength={100}
            value={newName}
            placeholder="新角色卡名称，例如：乃爱 · 日语直播"
            onChange={(e) => setNewName(e.target.value)}
          />
          <button className="primary" onClick={create}>
            <Plus size={15} />
            用当前设置新建
          </button>
        </div>
      </section>

      {editing && (
        <section className="card prompt-card">
          <div className="section-number">
            02 <span>EDIT</span>
          </div>
          <div className="row between wrap">
            <h2>
              <UserRound size={18} /> 编辑「{editing.name}」
            </h2>
            <div className="row wrap">
              <button onClick={captureCurrent} title="把工作台当前的音色、TTS、人设和语言写入这张卡">
                <Wand2 size={15} />
                取当前设置
              </button>
              <button onClick={duplicate}>
                <Copy size={15} />
                复制
              </button>
              <button className="danger" onClick={remove}>
                <Trash2 size={15} />
                删除
              </button>
            </div>
          </div>
          <div className="form-grid">
            <HotkeyField label="角色卡快捷键" value={editing.hotkey || ""} enabled={config.hotkeys.enabled}
              status={hotkeyStatus?.[`character:${editing.id}`]} onChange={(hotkey) => updateCard({ hotkey })} />
            <Field label="名称">
              <input
                aria-label="角色卡名称"
                maxLength={100}
                value={editing.name}
                onChange={(e) => updateCard({ name: e.target.value })}
              />
            </Field>
            <Field label="备注">
              <input
                aria-label="角色卡备注"
                maxLength={500}
                value={editing.note}
                onChange={(e) => updateCard({ note: e.target.value })}
              />
            </Field>
            <Field label="参考音色" note="选「不切换」时保留工作台当前音色。">
              <select
                aria-label="角色卡参考音色"
                value={editing.reference}
                onChange={(e) => updateCard({ reference: e.target.value })}
              >
                <option value="">不切换</option>
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="TTS" note="选「不切换」时保留工作台当前 TTS。">
              <select
                aria-label="角色卡 TTS"
                value={editing.tts}
                onChange={(e) => updateCard({ tts: e.target.value })}
              >
                <option value="">不切换</option>
                {tts.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="目标语言（LLM翻译）">
              <select
                aria-label="角色卡目标语言"
                value={editing.target_language}
                onChange={(e) => updateCard({ target_language: e.target.value as TargetLanguage })}
              >
                {Object.entries(targetLanguageNames).map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="润色程度">
              <select
                aria-label="角色卡润色程度"
                value={editing.style_strength}
                onChange={(e) => updateCard({ style_strength: e.target.value as StyleStrength })}
              >
                {Object.entries(strengths).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={`情绪强度 · ${Math.round(editing.emotion_intensity * 100)}%`}>
              <input
                type="range"
                aria-label="角色卡情绪强度"
                min="0"
                max="1"
                step=".05"
                value={editing.emotion_intensity}
                onChange={(e) => updateCard({ emotion_intensity: +e.target.value })}
              />
            </Field>
            <Toggle
              label="角色润色（LLM 优化）"
              value={editing.rewrite_enabled}
              onChange={(rewrite_enabled) => updateCard({ rewrite_enabled })}
            />
          </div>
          <div className="prompt-grid">
            <Field label="角色人设">
              <textarea
                aria-label="角色卡人设"
                rows={4}
                maxLength={12000}
                value={editing.persona}
                onChange={(e) => updateCard({ persona: e.target.value })}
              />
            </Field>
            <Field label="微重构规则">
              <textarea
                aria-label="角色卡微重构规则"
                rows={4}
                maxLength={16000}
                value={editing.prompt}
                onChange={(e) => updateCard({ prompt: e.target.value })}
              />
            </Field>
          </div>
          <PronunciationEditor
            config={config}
            title="角色专属读音"
            note="只在这张卡生效时使用，同一原词覆盖全局词条。"
            entries={editing.pronunciations}
            onChange={(pronunciations) => updateCard({ pronunciations })}
          />
          <div className="row wrap">
            <button className="primary" disabled={!dirty} onClick={() => run(() => save())}>
              <Save size={15} />
              保存角色卡
            </button>
          </div>
        </section>
      )}

      <section className="card prompt-card">
        <div className="section-number">
          03 <span>PRONUNCIATION</span>
        </div>
        <h2>读音词典</h2>
        <p>合成前把原词替换成 TTS 读得准的写法：长词优先、只替换一次。可写同音字、拼音、空格分隔的字母，或限定只对某个 TTS 生效。</p>
        <PronunciationEditor
          config={config}
          title="全局词条"
          note="对所有角色卡生效。"
          entries={config.pronunciations || []}
          onChange={(pronunciations) => change({ pronunciations })}
        />
        <div className="row wrap">
          <button className="primary" disabled={!dirty} onClick={() => run(() => save())}>
            <Save size={15} />
            保存词典
          </button>
        </div>
        <div className="form-grid">
          <Field label="试读" note="按当前 TTS 与当前角色卡显示实际送给 TTS 的文字。">
            <input
              aria-label="读音试读文本"
              maxLength={2000}
              value={sample}
              placeholder="输入一句含专有名词的话"
              onChange={(e) => setSample(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sample.trim() && runPreview()}
            />
          </Field>
          <div className="rule-summary">
            <button disabled={!sample.trim()} onClick={runPreview}>
              查看替换结果
            </button>
            {preview && <code aria-label="替换结果">{preview}</code>}
          </div>
        </div>
      </section>
    </>
  );
}
