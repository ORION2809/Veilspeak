"""Band-limited resampling helpers shared by the receiver and the simulators."""
import numpy as np


def frac_window(x, start, n, half=16):
    """n samples of x beginning at the fractional position start (windowed-sinc interpolation)."""
    base = int(np.floor(start))
    d = np.arange(-half + 1, half + 1) - (start - base)
    taps = np.sinc(d) * (0.5 + 0.5 * np.cos(np.pi * d / half))
    lo, hi = base - half + 1, base + n + half
    seg = np.zeros(hi - lo)
    a, b = max(lo, 0), min(hi, len(x))
    if b > a:
        seg[a - lo:b - lo] = x[a:b]
    return np.correlate(seg, taps, "valid")


def resample_at(x, t, half=24):
    """x at arbitrary fractional positions t (windowed-sinc), as a sound card's resampler
    would do. Cheaper interpolators attenuate the top harmonics and overstate the damage."""
    base = np.floor(t).astype(int)
    frac = t - base
    padded = np.concatenate([np.zeros(half), x, np.zeros(half + 1)])
    out = np.zeros(len(t))
    for k in range(-half + 1, half + 1):
        d = k - frac
        out += padded[np.clip(base + k + half, 0, len(padded) - 1)] * np.sinc(d) * (0.5 + 0.5 * np.cos(np.pi * d / half))
    return out


def clock_offset(x, ppm):
    """x as sampled by a clock that runs ppm parts per million fast."""
    if not ppm:
        return np.asarray(x, dtype=float)
    step = 1 + ppm * 1e-6
    return resample_at(np.asarray(x, dtype=float), np.arange(int(len(x) / step)) * step)
