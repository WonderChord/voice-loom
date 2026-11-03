"""全局快捷键：Windows RegisterHotKey，在独立线程的消息循环里收 WM_HOTKEY 并分发动作。"""
from __future__ import annotations

import ctypes
import os
import threading
from typing import Callable

MODIFIERS = {"ctrl": ("Ctrl", 0x0002), "control": ("Ctrl", 0x0002), "alt": ("Alt", 0x0001),
             "shift": ("Shift", 0x0004), "win": ("Win", 0x0008)}
MOD_ORDER = ["Ctrl", "Alt", "Shift", "Win"]
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

KEYS: dict[str, tuple[str, int]] = {}
for _n in range(1, 25):
    KEYS[f"f{_n}"] = (f"F{_n}", 0x70 + _n - 1)
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    KEYS[_c.lower()] = (_c, ord(_c))
for _n in range(10):
    KEYS[f"numpad{_n}"] = (f"Numpad{_n}", 0x60 + _n)
for _name, _vk in {"Space": 0x20, "Tab": 0x09, "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23,
                   "PageUp": 0x21, "PageDown": 0x22, "Up": 0x26, "Down": 0x28, "Left": 0x25, "Right": 0x27,
                   "Pause": 0x13, "ScrollLock": 0x91, "Backquote": 0xC0, "Minus": 0xBD, "Equal": 0xBB,
                   "BracketLeft": 0xDB, "BracketRight": 0xDD, "Backslash": 0xDC, "Semicolon": 0xBA,
                   "Quote": 0xDE, "Comma": 0xBC, "Period": 0xBE, "Slash": 0xBF,
                   "NumpadAdd": 0x6B, "NumpadSubtract": 0x6D, "NumpadMultiply": 0x6A,
                   "NumpadDivide": 0x6F, "NumpadDecimal": 0x6E}.items():
    KEYS[_name.lower()] = (_name, _vk)
for _alias, _key in {"del": "delete", "ins": "insert", "pgup": "pageup", "pgdn": "pagedown"}.items():
    KEYS[_alias] = KEYS[_key]
# 无修饰键也可注册：F13–F24 常用于 Stream Deck 等宏键盘，不会和日常输入冲突。
BARE_OK = {f"F{n}" for n in range(13, 25)} | {"Pause", "ScrollLock"}

ACTIONS = {"toggle_listen": "开始/停止监听", "flush": "立即处理当前语音", "stop_playback": "停止播放",
           "next_character": "下一张角色卡", "previous_character": "上一张角色卡"}


def parse(text: str) -> tuple[int, int, str]:
    """把「Ctrl+Alt+F9」解析成 (修饰位, 虚拟键码, 规范写法)。"""
    parts = [p.strip() for p in str(text).replace(" ", "").split("+")]
    if not parts or not all(parts):
        raise ValueError(f"快捷键「{text}」格式不正确，示例：Ctrl+Alt+F9")
    *mods, key = parts
    names, bits = set(), 0
    for mod in mods:
        if mod.lower() not in MODIFIERS:
            raise ValueError(f"快捷键「{text}」的修饰键「{mod}」无效，可用 Ctrl / Alt / Shift / Win")
        name, bit = MODIFIERS[mod.lower()]
        names.add(name)
        bits |= bit
    if key.lower() in MODIFIERS:
        raise ValueError(f"快捷键「{text}」需要一个主键，例如 F9")
    if key.lower() not in KEYS:
        raise ValueError(f"快捷键「{text}」的按键「{key}」不支持")
    key_name, vk = KEYS[key.lower()]
    if key_name == "F12":
        raise ValueError("F12 是 Windows 调试器保留键，请改用其他按键，例如 Ctrl+Alt+F8")
    if not bits and key_name not in BARE_OK:
        raise ValueError(f"快捷键「{text}」需要至少一个修饰键（Ctrl / Alt / Shift / Win）；单键支持 F13–F24、Pause、ScrollLock")
    canonical = "+".join([m for m in MOD_ORDER if m in names] + [key_name])
    return bits, vk, canonical


def normalize(text: str) -> str:
    return parse(text)[2] if str(text).strip() else ""


def bindings(config: dict) -> dict[str, str]:
    """配置里所有生效的绑定：动作名 → 快捷键。角色卡动作为 character:<id>。"""
    hotkeys = config.get("hotkeys") or {}
    if not hotkeys.get("enabled"):
        return {}
    result = {action: hotkeys[action] for action in ACTIONS if hotkeys.get(action)}
    for card in config.get("characters") or []:
        if card.get("hotkey"):
            result[f"character:{card['id']}"] = card["hotkey"]
    return result


def duplicates(binding: dict[str, str]) -> list[str]:
    seen, repeated = set(), []
    for key in binding.values():
        canonical = normalize(key)
        if canonical in seen:
            repeated.append(canonical)
        seen.add(canonical)
    return repeated


class NullHotkeys:
    """非 Windows 或测试环境：只记录配置，不注册系统快捷键。"""

    def __init__(self, dispatch: Callable[[str], None] | None = None):
        self.dispatch = dispatch
        self.binding: dict[str, str] = {}

    def configure(self, binding: dict[str, str]) -> dict[str, dict]:
        self.binding = dict(binding)
        return {action: {"key": key, "ok": True, "message": ""} for action, key in binding.items()}

    def close(self):
        self.binding = {}


class WindowsHotkeys:
    def __init__(self, dispatch: Callable[[str], None]):
        from ctypes import wintypes
        self.dispatch = dispatch
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.lock = threading.Lock()
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                             wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL

    def configure(self, binding: dict[str, str]) -> dict[str, dict]:
        with self.lock:
            self._stop()
            if not binding:
                return {}
            status: dict[str, dict] = {}
            ready = threading.Event()
            self.thread = threading.Thread(target=self._loop, args=(dict(binding), status, ready),
                                           name="voice-loom-hotkeys", daemon=True)
            self.thread.start()
            ready.wait(5)
            return status

    def post(self, message: int, wparam: int = 0):
        return bool(self.thread_id) and bool(self.user32.PostThreadMessageW(self.thread_id, message, wparam, 0))

    def close(self):
        with self.lock:
            self._stop()

    def _stop(self):
        if self.thread and self.thread.is_alive() and self.thread_id:
            self.post(WM_QUIT)
            self.thread.join(5)
        self.thread, self.thread_id = None, 0

    def _loop(self, binding, status, ready):
        msg = self.wintypes.MSG()
        # 先建立本线程的消息队列，保证 PostThreadMessage 能送达。
        self.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self.thread_id = self.kernel32.GetCurrentThreadId()
        self.ids: dict[int, str] = {}
        for index, (action, key) in enumerate(binding.items(), start=1):
            try:
                mods, vk, canonical = parse(key)
            except ValueError as exc:
                status[action] = {"key": key, "ok": False, "message": str(exc)}
                continue
            if self.user32.RegisterHotKey(None, index, mods | MOD_NOREPEAT, vk):
                self.ids[index] = action
                status[action] = {"key": canonical, "ok": True, "message": ""}
            else:
                error = ctypes.get_last_error()
                message = ("已被其他程序占用，请换一个组合" if error == ERROR_HOTKEY_ALREADY_REGISTERED
                           else f"注册失败（错误 {error}）")
                status[action] = {"key": canonical, "ok": False, "message": message}
        ready.set()
        try:
            while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in self.ids:
                    # 动作可能要等锁或网络，放到短线程里，消息循环保持响应。
                    threading.Thread(target=self.dispatch, args=(self.ids[msg.wParam],), daemon=True).start()
        finally:
            for index in self.ids:
                self.user32.UnregisterHotKey(None, index)


def create(dispatch: Callable[[str], None]):
    return WindowsHotkeys(dispatch) if os.name == "nt" else NullHotkeys(dispatch)
