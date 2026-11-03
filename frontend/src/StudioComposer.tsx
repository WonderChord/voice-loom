import { useRef } from "react";
import { FileAudio, PenLine, Send, X } from "lucide-react";
import { api, type Config } from "./types";

export type StudioDraft = { text: string; preview: boolean; file: File | null; lastId?: string };

export function StudioComposer({
  draft,
  update,
  config,
  dirty,
  save,
  busy,
  online,
  run,
  notify,
  showResult,
}: {
  draft: StudioDraft;
  update: (patch: Partial<StudioDraft>) => void;
  config: Config;
  dirty: boolean;
  save: () => Promise<Config>;
  busy: boolean;
  online: boolean;
  run: (fn: () => Promise<unknown>) => void;
  notify: (message: string, error?: boolean) => void;
  showResult?: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const editor = useRef<HTMLTextAreaElement>(null);
  const sending = useRef(false);
  const { text, preview, file } = draft;
  const play = !preview && config.audio.auto_play;
  const action = preview ? "预览编排" : play ? "合成并播放" : "生成音频";
  const submit = () => {
    if (sending.current || busy || !online || (!file && !text.trim())) return;
    sending.current = true;
    run(async () => {
      try {
        if (dirty) await save();
        if (file) {
          const data = new FormData();
          data.append("file", file);
          data.append("preview_only", String(preview));
          data.append("play_audio", String(play));
          const result = await api<{ id: string }>("/process/audio", {
            method: "POST",
            body: data,
          });
          update({ file: null, lastId: result.id });
        } else {
          const result = await api<{ id: string }>("/process/text", {
            method: "POST",
            body: JSON.stringify({
              text: text.trim(),
              preview_only: preview,
              play_audio: play,
            }),
          });
          update({ text: "", lastId: result.id });
        }
        notify(preview ? "已提交编排预览" : "已加入语音合成队列");
      } finally {
        sending.current = false;
      }
    });
  };
  return (
    <section
      className="card studio-composer"
      aria-label="文字与录音"
      onKeyDown={(e) => {
        if (e.ctrlKey && e.key === "Enter" && !e.nativeEvent.isComposing && !e.repeat) {
          e.preventDefault();
          submit();
        }
      }}
    >
      <div className="composer-heading">
        <h2>
          <PenLine size={17} />
          文字与录音
        </h2>
        <div className="segmented compact-segments" role="group" aria-label="提交方式">
          {([true, false] as const).map((value) => (
            <button
              key={String(value)}
              type="button"
              aria-pressed={preview === value}
              className={preview === value ? "active" : ""}
              disabled={busy}
              onClick={() => update({ preview: value })}
            >
              {value ? "只预览" : "合成语音"}
            </button>
          ))}
        </div>
      </div>
      {file ? (
        <div className="draft-file">
          <FileAudio size={23} />
          <div>
            <b>{file.name}</b>
            <small>已选录音 · {Math.max(1, Math.round(file.size / 1024))} KB</small>
          </div>
          <button
            className="icon"
            title="移除录音，返回文字输入"
            aria-label="移除录音"
            disabled={busy}
            onClick={() => update({ file: null })}
          >
            <X size={16} />
          </button>
        </div>
      ) : (
        <textarea
          ref={editor}
          aria-label="手动测试文本"
          placeholder="输入台词，或在下方选择一段录音…"
          maxLength={4000}
          disabled={busy}
          value={text}
          onChange={(e) => update({ text: e.target.value })}
        />
      )}
      <div className="composer-tools">
        <div className="composer-source">
          <button
            type="button"
            className="text-button"
            disabled={busy}
            onClick={() => input.current?.click()}
          >
            <FileAudio size={15} />
            {file ? "更换录音" : "选择录音"}
          </button>
          {!file && !text && (
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => {
                update({ text: "大家晚上好，欢迎来到今天的直播。", preview: true });
                editor.current?.focus();
              }}
            >
              填入预览示例
            </button>
          )}
          {!file && !!text && <small>{text.length} / 4000</small>}
        </div>
        <button
          className="primary composer-submit"
          title="Ctrl + Enter"
          disabled={busy || !online || (!file && !text.trim())}
          onClick={submit}
        >
          <Send size={15} />
          {action}
        </button>
      </div>
      <p className="composer-hint">
        {preview
          ? "预览显示台词和表演稿。"
          : play
            ? "生成后播放到所选输出设备。"
            : "音频保存在记录中，可随时试听。"}
        <span>{file ? "录音上限 60 秒、25 MB。" : "Ctrl + Enter 提交。"}</span>
        {showResult && (
          <button className="text-button locate-result" onClick={showResult}>
            查看本次结果
          </button>
        )}
      </p>
      <input
        ref={input}
        type="file"
        hidden
        accept=".wav,.flac,.ogg,.mp3"
        onChange={(e) => {
          const chosen = e.target.files?.[0];
          e.target.value = "";
          if (!chosen) return;
          if (!/\.(wav|flac|ogg|mp3)$/i.test(chosen.name))
            return notify("请选择 WAV、FLAC、OGG 或 MP3 录音。", true);
          if (!chosen.size || chosen.size > 25 * 1024 * 1024)
            return notify("请选择非空且不超过 25 MB 的录音。", true);
          update({ file: chosen });
        }}
      />
    </section>
  );
}
