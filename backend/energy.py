"""Shared pre-ASR energy gate for microphone segments and imported audio."""
from dataclasses import asdict, dataclass
import math

import numpy as np
import soundfile as sf


@dataclass(frozen=True)
class EnergyCheck:
    accepted: bool
    active_ms: float
    peak_db: float
    threshold_db: float
    min_active_ms: int
    reason: str

    def as_dict(self):
        return asdict(self)


def check_audio_energy(audio, rate, *, threshold_db=-45, min_active_ms=120):
    """Accumulate above-threshold 20 ms frames without diluting with silence."""
    if (isinstance(rate, bool) or not math.isfinite(float(rate))
            or int(rate) != rate or rate <= 0):
        raise ValueError("音频采样率无效，请重新导入音频")
    if not math.isfinite(float(threshold_db)) or not 0 < min_active_ms <= 1000:
        raise ValueError("能量检查参数无效")
    samples = np.asarray(audio, dtype=np.float64)
    if samples.ndim == 2 and samples.shape[1] > 0:
        samples = samples.mean(axis=1)
    elif samples.ndim != 1:
        raise ValueError("音频须包含一条或多条声道")
    if not len(samples) or not np.isfinite(samples).all():
        raise ValueError("音频为空或包含无效采样，请重新导入音频")

    frame_size = max(1, round(rate * .02))
    full_size = len(samples) // frame_size * frame_size
    powers = np.mean(samples[:full_size].reshape(-1, frame_size) ** 2, axis=1)
    threshold_power = 10 ** (float(threshold_db) / 10)
    active_samples = int(np.count_nonzero(powers > threshold_power)) * frame_size
    peak_power = float(powers.max()) if len(powers) else 0.0
    if full_size < len(samples):
        tail_power = float(np.mean(samples[full_size:] ** 2))
        peak_power = max(peak_power, tail_power)
        if tail_power > threshold_power:
            active_samples += len(samples) - full_size
    active_ms = active_samples * 1000 / rate
    accepted = active_ms >= min_active_ms
    active_label = f"{active_ms:.3f}".rstrip("0").rstrip(".")
    reason = "" if accepted else (
        f"音量不足，已跳过识别：高于 {threshold_db:g} dBFS 的音频仅 {active_label} ms，"
        f"需要至少 {min_active_ms} ms。请提高录音音量或选择包含人声的片段。")
    return EnergyCheck(accepted, round(active_ms, 3),
                       round(max(-120.0, 10 * math.log10(max(peak_power, 1e-12))), 2),
                       float(threshold_db), int(min_active_ms), reason)


def check_file_energy(path, audio_config):
    if not audio_config.get("skip_low_energy", True):
        return None
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    return check_audio_energy(audio, rate,
                              threshold_db=audio_config.get("energy_threshold_db", -45),
                              min_active_ms=audio_config.get("min_active_ms", 120))
