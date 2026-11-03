import { useState, type KeyboardEvent } from "react";
import { type HotkeyStatus } from "./types";

const special: Record<string, string> = {
  ArrowUp: "Up",
  ArrowDown: "Down",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  Space: "Space",
  Tab: "Tab",
  Insert: "Insert",
  Delete: "Delete",
  Home: "Home",
  End: "End",
  PageUp: "PageUp",
  PageDown: "PageDown",
  Pause: "Pause",
  ScrollLock: "ScrollLock",
  Backquote: "Backquote",
  Minus: "Minus",
  Equal: "Equal",
  BracketLeft: "BracketLeft",
  BracketRight: "BracketRight",
  Backslash: "Backslash",
  Semicolon: "Semicolon",
  Quote: "Quote",
  Comma: "Comma",
  Period: "Period",
  Slash: "Slash",
  NumpadAdd: "NumpadAdd",
  NumpadSubtract: "NumpadSubtract",
  NumpadMultiply: "NumpadMultiply",
  NumpadDivide: "NumpadDivide",
  NumpadDecimal: "NumpadDecimal",
};

export function HotkeyField({
  label,
  value,
  onChange,
  enabled,
  status,
}: {
  label: string;
  value: string;
  onChange: (key: string) => void;
  enabled: boolean;
  status?: HotkeyStatus;
}) {
  const [error, setError] = useState("");
  const capture = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Tab" && !event.ctrlKey && !event.altKey && !event.metaKey) return;
    event.preventDefault();
    if (event.key === "Escape") {
      event.currentTarget.blur();
      return;
    }
    if (["Control", "Alt", "Shift", "Meta"].includes(event.key) || event.repeat) return;
    const mods = [
      event.ctrlKey && "Ctrl",
      event.altKey && "Alt",
      event.shiftKey && "Shift",
      event.metaKey && "Win",
    ].filter(Boolean);
    if (!mods.length && ["Backspace", "Delete"].includes(event.key)) {
      onChange("");
      setError("");
      return;
    }
    const code = event.code;
    const key = /^Key[A-Z]$/.test(code)
      ? code.slice(3)
      : /^Digit[0-9]$/.test(code)
        ? code.slice(5)
        : /^(F([1-9]|1[0-9]|2[0-4])|Numpad[0-9])$/.test(code)
          ? code
          : special[code];
    if (!key) {
      setError("请使用字母、数字、功能键或导航键。");
      return;
    }
    if (key === "F12") {
      setError("F12 为 Windows 保留键，请换一个组合。");
      return;
    }
    if (!mods.length && !/^(F(1[3-9]|2[0-4])|Pause|ScrollLock)$/.test(key)) {
      setError("请同时按住 Ctrl、Alt、Shift 或 Win。");
      return;
    }
    onChange([...mods, key].join("+"));
    setError("");
  };
  const applied = enabled && !!value && status?.key === value;
  const failed = applied && !status.ok;
  return (
    <div className="field hotkey-field">
      <label>
        <span>{label}</span>
        <input
          aria-label={label}
          value={value}
          placeholder="点击后按下组合键"
          onKeyDown={capture}
          onChange={(e) => {
            onChange(e.target.value);
            setError("");
          }}
          onPaste={(e) => {
            e.preventDefault();
            onChange(e.clipboardData.getData("text").trim());
            setError("");
          }}
        />
      </label>
      <div className="hotkey-feedback">
        <small className={error || failed ? "error-text" : "muted"} role="status">
          {error ||
            (failed
              ? status.message
              : applied
                ? "已生效"
                : !value
                  ? "未绑定"
                  : enabled
                    ? "保存后生效"
                    : "快捷键已关闭")}
        </small>
        <button
          type="button"
          className="text-button"
          aria-label={`清除${label}`}
          disabled={!value}
          onClick={() => {
            onChange("");
            setError("");
          }}
        >
          清除
        </button>
      </div>
    </div>
  );
}
