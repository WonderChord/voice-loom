"""Polyphase FIR audio resampling compatible with scipy.signal.resample_poly."""
from __future__ import annotations

from functools import lru_cache
import math

import numpy as np


@lru_cache(maxsize=64)
def _design_polyphase_filter(up: int, down: int) -> tuple[np.ndarray, int]:
    max_rate = max(up, down)
    half_len = 10 * max_rate
    n = np.arange(-half_len, half_len + 1, dtype=np.float64)
    h = np.sinc(n / max_rate) * np.kaiser(2 * half_len + 1, 5.0)
    h *= up / np.sum(h)
    pad = (-len(h)) % up
    if pad:
        h = np.pad(h, (0, pad))
    # h_poly[p, j] == h[j * up + p]
    h_poly = h.reshape(-1, up).T.copy()
    return h_poly, half_len


def resample_poly(x: np.ndarray, up: int, down: int, axis: int = 0) -> np.ndarray:
    arr = np.asarray(x)
    up_i, down_i = int(up), int(down)
    if up_i <= 0 or down_i <= 0:
        raise ValueError("up and down must be positive integers")
    g = math.gcd(up_i, down_i)
    up_i //= g
    down_i //= g
    if up_i == 1 and down_i == 1:
        return np.array(arr, dtype=np.float64 if arr.dtype.kind in "iu" else arr.dtype, copy=True)
    if axis != 0:
        arr = np.moveaxis(arr, axis, 0)
    n_in = arr.shape[0]
    if n_in == 0:
        out = arr.copy()
        return np.moveaxis(out, 0, axis) if axis != 0 else out

    h_poly, half_len = _design_polyphase_filter(up_i, down_i)
    m_taps = h_poly.shape[1]
    n_out = (n_in * up_i + down_i - 1) // down_i

    t = np.arange(n_out, dtype=np.int64) * down_i + half_len
    q = t // up_i
    p = t % up_i

    lead_pad = m_taps - 1
    tail_pad = max(0, int(q[-1]) + 1 - n_in)
    pad_spec = [(lead_pad, tail_pad)] + [(0, 0)] * (arr.ndim - 1)
    x_pad = np.pad(arr.astype(np.float64, copy=False), pad_spec, mode="constant")

    out = np.zeros((n_out,) + arr.shape[1:], dtype=np.float64)
    base_idx = lead_pad + q
    expand_shape = (n_out,) + (1,) * (arr.ndim - 1)
    for j in range(m_taps):
        weights = h_poly[p, j].reshape(expand_shape)
        out += weights * x_pad[base_idx - j]

    if axis != 0:
        out = np.moveaxis(out, 0, axis)
    return out
