"""Native local audio I/O. Importing this module never opens a device."""
from dataclasses import dataclass, field
from contextlib import contextmanager
import ctypes
import math
import os
from pathlib import Path
import queue
import threading
import time

import numpy as np
import sounddevice as sd
import soundfile as sf

from .resample import resample_poly
from .vad import SpeechGate


_PORTAUDIO_LIFECYCLE = threading.RLock()


@contextmanager
def _windows_com():
    """WASAPI requires COM on every thread that operates its streams."""
    if os.name != "nt":
        yield
        return
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    result = ole.CoInitializeEx(None, 0)
    # An existing STA apartment is also suitable. Only balance successful calls
    # made here; RPC_E_CHANGED_MODE leaves the caller's apartment untouched.
    initialized = result in (0, 1)
    if not initialized and (result & 0xffffffff) != 0x80010106:
        raise OSError(f"Windows 音频线程初始化失败：0x{result & 0xffffffff:08x}")
    try:
        yield
    finally:
        if initialized:
            ole.CoUninitialize()


def list_devices() -> dict:
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        defaults = sd.default.device
        result = {"inputs": [], "outputs": [],
                  "default_input": int(defaults[0]) if defaults[0] >= 0 else None,
                  "default_output": int(defaults[1]) if defaults[1] >= 0 else None}
        for index, device in enumerate(devices):
            hostapi = hostapis[device["hostapi"]]["name"]
            entry = {"id": index, "name": device["name"],
                     "hostapi": hostapi,
                     # WDM-KS 不经 Windows 混音器，独占和多设备切换时容易报 -9999。
                     "unstable": "WDM-KS" in hostapi,
                     "sample_rate": float(device["default_samplerate"])}
            if device["max_input_channels"] > 0:
                result["inputs"].append(entry.copy())
            if device["max_output_channels"] > 0:
                result["outputs"].append(entry.copy())
        return result
    except Exception as exc:
        raise RuntimeError("无法读取音频设备，请检查系统声音设置") from exc


def _device(device, kind):
    if device is None:
        device = int(sd.default.device[0 if kind == "input" else 1])
    if isinstance(device, bool) or not isinstance(device, (int, np.integer)) or device < 0:
        raise RuntimeError("没有可用的" + ("输入" if kind == "input" else "输出") + "设备")
    info = sd.query_devices(int(device), kind)
    if info[f"max_{kind}_channels"] < 1:
        raise RuntimeError("选定设备不支持" + ("录音" if kind == "input" else "播放"))
    return int(device), info


def _format(device, info, kind, preferred_channels=1, source_rate=None):
    max_channels = int(info[f"max_{kind}_channels"])
    channels = [min(preferred_channels, max_channels)]
    if max_channels >= 2:
        channels.append(2)
    channels.append(1)
    rates = [round(info["default_samplerate"]), source_rate, 48000, 44100, 16000]
    check = sd.check_input_settings if kind == "input" else sd.check_output_settings
    for channel_count in dict.fromkeys(channels):
        for rate in dict.fromkeys(rates):
            if rate is None or rate < 8000:
                continue
            try:
                check(device=device, channels=channel_count, dtype="float32", samplerate=rate)
                return int(rate), channel_count
            except Exception:
                continue
    raise RuntimeError("设备没有可用的采样率，请刷新设备后重选")


def _close_stream(stream):
    if stream is None:
        return
    try:
        stream.abort()
    except Exception:
        pass
    try:
        stream.close()
    except Exception:
        pass


@dataclass
class _Capture:
    token: int
    config: dict
    rate: int
    packets: queue.Queue
    stop: threading.Event = field(default_factory=threading.Event)
    stream_lock: threading.Lock = field(default_factory=threading.Lock)
    stream: object = None
    thread: threading.Thread | None = None
    sequence: int = 0
    overflow_total: int = 0
    ready: threading.Event = field(default_factory=threading.Event)
    startup_error: Exception | None = None


@dataclass
class _Playback:
    token: int
    stop: threading.Event = field(default_factory=threading.Event)
    stream_lock: threading.Lock = field(default_factory=threading.Lock)
    stream: object = None
    thread: threading.Thread | None = None


class AudioEngine:
    CAPTURE_QUEUE_SIZE = 128

    def __init__(self, on_segment, on_status):
        self.on_segment = on_segment
        self.on_status = on_status
        self._lock = threading.RLock()
        self._lifecycle = threading.RLock()
        self._capture = None
        self._playback = None
        self._capture_token = self._play_token = 0
        self._playing = threading.Event()

    def _notify(self, **status):
        # A UI notification failure must not leave an audio device open.
        try:
            self.on_status(status)
        except Exception:
            pass

    @property
    def is_playing(self) -> bool:
        return self._playing.is_set()

    def _capture_current(self, capture):
        with self._lock:
            return self._capture is capture and not capture.stop.is_set()

    @staticmethod
    def _close_context(context):
        with context.stream_lock:
            stream, context.stream = context.stream, None
            if stream is not None:
                with _windows_com(), _PORTAUDIO_LIFECYCLE:
                    _close_stream(stream)

    def start(self, audio_config: dict):
        with self._lifecycle:
            self.stop()
            config = dict(audio_config)
            with self._lock:
                self._capture_token += 1
                capture = _Capture(self._capture_token, config, 0,
                                   queue.Queue(self.CAPTURE_QUEUE_SIZE))
                self._capture = capture
            capture.thread = threading.Thread(target=self._capture_worker, args=(capture,),
                                              name="voice-loom-audio-dsp", daemon=True)
            try:
                capture.thread.start()
                capture.ready.wait()
                if capture.startup_error is not None:
                    raise capture.startup_error
            except Exception as exc:
                capture.stop.set()
                self._close_context(capture)
                with self._lock:
                    if self._capture is capture:
                        self._capture = None
                error = "无法启动录音，请确认输入设备仍已连接且未被独占"
                self._notify(listening=False, level_db=-96.0, error=error, error_source="input")
                raise RuntimeError(error) from exc

    def _capture_worker(self, capture):
        try:
            # Keep this apartment alive until its stream has closed. API
            # request workers can enter/leave independently of recording.
            with _windows_com():
                try:
                    self._open_capture(capture)
                finally:
                    self._close_context(capture)
        except Exception as exc:
            if not capture.ready.is_set():
                capture.startup_error = exc
            elif not capture.stop.is_set():
                self._notify(error="录音已停止：" + str(exc), error_source="input")
        finally:
            capture.stop.set()
            with self._lock:
                current = self._capture is capture
                if current:
                    self._capture = None
            capture.ready.set()
            if current:
                self._notify(listening=False, level_db=-96.0)

    def _open_capture(self, capture):
        config = capture.config
        with _PORTAUDIO_LIFECYCLE:
            device, info = _device(config.get("input_device"), "input")
            rate, channels = _format(device, info, "input")
        capture.rate = rate
        gate = SpeechGate(rate, config.get("pause_ms", 900), config.get("min_db", -44),
                          config.get("max_seconds", 30))

        def callback(indata, frames, timing, status):
            # PortAudio owns indata. Only copy and enqueue on its callback.
            capture.sequence += 1
            packet = ("audio", capture.sequence, indata.copy(), status,
                      bool(config.get("suppress_during_playback", True)
                           and self._playing.is_set()))
            try:
                capture.packets.put_nowait(packet)
            except queue.Full:
                capture.overflow_total += 1

        with capture.stream_lock, _PORTAUDIO_LIFECYCLE:
            if capture.stop.is_set():
                return
            capture.stream = sd.InputStream(device=device, samplerate=rate, channels=channels,
                                            dtype="float32", blocksize=round(rate * .02),
                                            callback=callback)
            capture.stream.start()
        self._notify(listening=True, level_db=-96.0, message="已开始监听麦克风")
        capture.ready.set()
        self._capture_loop(capture, gate)

    def _emit(self, capture, segments):
        for segment in segments:
            if not self._capture_current(capture):
                break
            self.on_segment(segment, capture.rate)

    def _capture_loop(self, capture, gate):
        sequence = overflow_seen = 0
        last_level = 0.0
        was_suppressed = False
        while self._capture_current(capture):
            if capture.overflow_total != overflow_seen:
                dropped = capture.overflow_total - overflow_seen
                overflow_seen = capture.overflow_total
                self._notify(message=f"输入队列已满，丢弃了 {dropped} 个音频块；请降低处理负载")
            try:
                packet = capture.packets.get(timeout=.1)
            except queue.Empty:
                with capture.stream_lock:
                    if capture.stop.is_set():
                        break
                    if capture.stream is None or not capture.stream.active:
                        raise RuntimeError("输入设备已断开或录音流意外停止")
                continue
            if not self._capture_current(capture):
                break
            if packet[0] == "flush":
                if capture.config.get("suppress_during_playback", True) and self.is_playing:
                    gate.reset()
                else:
                    self._emit(capture, gate.flush())
                continue
            _, number, audio, port_status, suppressed = packet
            if sequence and number != sequence + 1:
                gate.reset()
            sequence = number
            if port_status:
                gate.reset()
                self._notify(message="音频设备报告数据中断：" + str(port_status))
            if not np.isfinite(audio).all():
                gate.reset()
                self._notify(message="输入音频包含无效采样，已跳过该音频块")
                continue
            now = time.monotonic()
            if now - last_level >= .08:
                rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) if audio.size else 0
                self._notify(level_db=max(-96.0, 20 * math.log10(max(rms, 1e-12))))
                last_level = now
            if suppressed:
                gate.reset()
                if not was_suppressed:
                    self._notify(message="播放期间暂停收音，避免回授")
                was_suppressed = True
                continue
            if was_suppressed:
                gate.reset()
                was_suppressed = False
            self._emit(capture, gate.feed(audio))

    def flush(self):
        with self._lock:
            capture = self._capture
        if capture is None or capture.stop.is_set():
            return
        try:
            capture.packets.put(("flush",), timeout=.2)
        except queue.Full:
            self._notify(message="输入队列仍满，当前片段尚未提交，请稍后重试")

    def stop(self):
        with self._lifecycle:
            with self._lock:
                self._capture_token += 1
                capture, self._capture = self._capture, None
                if capture is not None:
                    capture.stop.set()
            if capture is not None:
                self._close_context(capture)
                if capture.thread is not None and capture.thread is not threading.current_thread():
                    capture.thread.join(timeout=.5)
            self.stop_playback()
            self._notify(listening=False, level_db=-96.0)

    def stop_playback(self):
        with self._lock:
            self._play_token += 1
            playback, self._playback = self._playback, None
            if playback is not None:
                playback.stop.set()
            self._playing.clear()
        if playback is not None:
            self._close_context(playback)
        self._notify(playing=False)

    def play(self, path: str | Path, device: int | None = None, blocking: bool = True):
        with self._lock:
            self._play_token += 1
            previous = self._playback
            if previous is not None:
                previous.stop.set()
            playback = _Playback(self._play_token)
            self._playback = playback
            # Includes file loading and resampling so an async caller cannot
            # mistake the preparation window for completed playback.
            self._playing.set()
        self._notify(playing=True)
        if previous is not None:
            self._close_context(previous)
        if blocking:
            self._play_file(playback, path, device, raise_errors=True)
        else:
            playback.thread = threading.Thread(target=self._play_file,
                                               args=(playback, path, device, False),
                                               name="voice-loom-playback", daemon=True)
            playback.thread.start()

    def _play_file(self, playback, path, device, raise_errors):
        try:
            with _windows_com():
                try:
                    self._stream_file(playback, path, device)
                finally:
                    self._close_context(playback)
        except Exception as exc:
            if not playback.stop.is_set():
                error = "播放失败，请检查输出设备或音频文件"
                self._notify(error=error, error_source="output")
                if raise_errors:
                    raise RuntimeError(error) from exc
        finally:
            with self._lock:
                current = self._playback is playback
                if current:
                    self._playback = None
                    self._playing.clear()
            if current:
                self._notify(playing=False)

    def _stream_file(self, playback, path, device):
        audio, source_rate = sf.read(str(path), dtype="float32", always_2d=True)
        if not len(audio) or not np.isfinite(audio).all():
            raise ValueError("播放文件为空或包含无效采样")
        if playback.stop.is_set():
            return
        with _PORTAUDIO_LIFECYCLE:
            device, info = _device(device, "output")
            rate, channels = _format(device, info, "output", min(audio.shape[1], 2), source_rate)
        if channels == 1:
            audio = audio.mean(axis=1, keepdims=True)
        elif audio.shape[1] == 1:
            audio = np.repeat(audio, channels, axis=1)
        else:
            audio = audio[:, :channels]
        if source_rate != rate:
            divisor = math.gcd(int(source_rate), int(rate))
            audio = resample_poly(audio, rate // divisor, source_rate // divisor, axis=0)
        audio = np.ascontiguousarray(np.clip(audio, -1, 1), dtype=np.float32)
        with playback.stream_lock, _PORTAUDIO_LIFECYCLE:
            if playback.stop.is_set():
                return
            stream = sd.OutputStream(device=device, samplerate=rate, channels=channels,
                                     dtype="float32", blocksize=round(rate * .02))
            playback.stream = stream
            stream.start()
        with self._lock:
            current = self._playback is playback and not playback.stop.is_set()
        if not current:
            return
        size = max(1, round(rate * .02))
        underflow_reported = False
        for position in range(0, len(audio), size):
            if playback.stop.is_set():
                break
            if not stream.active:
                raise RuntimeError("输出设备已断开或播放流意外停止")
            if stream.write(audio[position:position + size]) and not underflow_reported:
                self._notify(message="输出设备供给中断，播放可能出现短暂停顿")
                underflow_reported = True
        if not playback.stop.is_set():
            # Drain the final hardware buffer; abort is reserved for cancel.
            stream.stop()
