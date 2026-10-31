"""WebRTC endpointing adapted from the existing speech-chain relay.

Only the detector input is resampled. Delivered segments retain the capture
sample rate and include a short lead-in and endpoint tail.
"""
from collections import deque
import math

import numpy as np
from .resample import resample_poly
import webrtcvad


class SpeechGate:
    def __init__(self, rate, pause_ms=900, min_db=-44, max_seconds=30, detector=None):
        self.rate = int(rate)
        if self.rate < 8000 or not math.isfinite(float(max_seconds)) or max_seconds <= 0:
            raise ValueError("无效的录音采样率或分段时长")
        self.n = round(self.rate * .02)
        self.pause = max(1, math.ceil(float(pause_ms) / 20))
        self.max_frames = max(6, int(float(max_seconds) * self.rate // self.n))
        self.floor = 10 ** (float(min_db) / 20)
        self.vad = detector if detector is not None else webrtcvad.Vad(2)
        self.pre = deque(maxlen=15)
        self.reset()

    def reset(self):
        self.pending = np.empty(0, dtype=np.float32)
        self.pre.clear()
        self.segment = []
        self.active = False
        self.run = self.silence = self.voiced = 0
        self.continuation = False

    def speech(self, frame):
        if float(np.sqrt(np.mean(frame * frame))) < self.floor:
            return False
        divisor = math.gcd(self.rate, 16000)
        samples = (resample_poly(frame, 16000 // divisor, self.rate // divisor)
                   if self.rate != 16000 else frame)
        samples = np.clip(samples[:320] * 32767, -32768, 32767).astype('<i2')
        return len(samples) == 320 and self.vad.is_speech(samples.tobytes(), 16000)

    def _finish(self, split=False):
        result = []
        enough_voice = self.voiced >= 6 or (self.continuation and self.voiced > 0)
        if self.active and self.segment and enough_voice:
            data = np.concatenate(self.segment)
            if not split:
                trim = max(0, self.silence - 15) * self.n
                if trim:
                    data = data[:-trim]
            if len(data):
                result.append(data[:, None])
        self.segment = []
        self.pre.clear()
        self.voiced = self.run = 0
        if split:
            # Continue the same utterance immediately. No onset frames are lost
            # at the configured boundary, including a short final remainder.
            self.active = True
            self.continuation = True
        else:
            self.active = self.continuation = False
            self.silence = 0
        return result

    def feed(self, audio):
        samples = np.asarray(audio, dtype=np.float32)
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        elif samples.ndim != 1:
            raise ValueError("录音必须是一维或二维音频数组")
        if not np.isfinite(samples).all():
            raise ValueError("录音包含无效采样")
        samples = np.concatenate((self.pending, samples))
        end = len(samples) // self.n * self.n
        output = []
        for pos in range(0, end, self.n):
            frame = samples[pos:pos + self.n].copy()
            voiced = self.speech(frame)
            if not self.active:
                self.pre.append((frame, voiced))
                self.run = self.run + 1 if voiced else 0
                if self.run >= 3:
                    self.active = True
                    self.segment = [item[0] for item in self.pre]
                    self.voiced = sum(item[1] for item in self.pre)
                    self.silence = 0
            else:
                self.segment.append(frame)
                self.voiced += int(voiced)
                self.silence = 0 if voiced else self.silence + 1
            if self.active and self.silence >= self.pause:
                output.extend(self._finish())
            elif self.active and len(self.segment) >= self.max_frames:
                output.extend(self._finish(split=True))
        self.pending = samples[end:].copy()
        return output

    def flush(self):
        if self.active and len(self.pending):
            self.segment.append(self.pending)
            if self.continuation and np.sqrt(np.mean(self.pending ** 2)) >= self.floor:
                self.voiced += 1
        self.pending = np.empty(0, dtype=np.float32)
        return self._finish()
