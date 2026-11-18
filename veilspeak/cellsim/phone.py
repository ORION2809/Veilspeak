"""Stand-ins for the audio processing a phone applies to call audio.

Real phones use proprietary processing that differs by chipset and tuning; none of it can be
reproduced exactly. These are textbook versions of the same functions, used as stress tests:
if the modem survives a generic suppressor at several strengths, it has margin; if it only
survives with processing off, that is a risk to test on real phones first.
"""
import numpy as np
from scipy.signal import butter, sosfilt


def bandpass(x, fs, lo=200.0, hi=3400.0):
    """Telephone-band filter (causal): the phone's send/receive filtering."""
    sos = np.vstack([butter(2, lo, "highpass", fs=fs, output="sos"),
                     butter(6, hi, "lowpass", fs=fs, output="sos")])
    return sosfilt(sos, x)


def alaw(x):
    """G.711 A-law at 8 kHz: the 64 kbit/s coding used between networks."""
    a = 87.6
    ax = np.clip(np.abs(x), 0, 1)
    comp = np.where(ax < 1 / a, a * ax / (1 + np.log(a)), (1 + np.log(np.maximum(a * ax, 1e-12))) / (1 + np.log(a)))
    code = np.round(comp * 127.5 - 0.5).clip(0, 127)         # 8 bits including sign
    comp = (code + 0.5) / 127.5
    out = np.where(comp < 1 / (1 + np.log(a)), comp * (1 + np.log(a)) / a,
                   np.exp(comp * (1 + np.log(a)) - 1) / a)
    return np.sign(x) * out


def agc(x, fs, target_dbfs=-20.0, max_gain_db=15.0, time_s=1.0, limit=0.7):
    """Slow automatic gain control toward a target level, then a soft limiter."""
    hop = fs // 100
    gain_db, out = 0.0, np.empty(len(x))
    coef = hop / (time_s * fs)
    level = None
    for start in range(0, len(x), hop):
        block = x[start:start + hop]
        power = float(np.mean(block ** 2)) + 1e-12
        if 10 * np.log10(power) > -55:                        # only adapt on signal, not silence
            level = power if level is None else (1 - coef) * level + coef * power
            want = target_dbfs - 10 * np.log10(level)
            gain_db += 0.2 * (np.clip(want, -max_gain_db, max_gain_db) - gain_db)
        out[start:start + hop] = block * 10 ** (gain_db / 20)
    return limit * np.tanh(out / limit)


class NoiseSuppressor:
    """Single-microphone noise suppressor of the standard kind: short-time spectrum, noise
    floor tracked as the minimum of the smoothed power over the last `memory_s` seconds,
    Wiener gain with a decision-directed SNR estimate, attenuation limited to `max_db`.

    Anything steady for longer than `memory_s` is taken for noise and turned down.
    """

    def __init__(self, fs, max_db=12.0, memory_s=1.5, smooth=0.85, overestimate=1.5):
        self.fs, self.floor = fs, 10 ** (-max_db / 20)
        self.size, self.hop = fs // 50, fs // 100             # 20 ms frames, 10 ms hop
        self.window = np.sqrt(np.hanning(self.size + 1)[:-1])
        self.smooth, self.over = smooth, overestimate
        self.blocks, self.per_block = 8, max(1, int(memory_s * 100 / 8))

    def process(self, x):
        size, hop = self.size, self.hop
        n = (len(x) - size) // hop + 1
        out = np.zeros(len(x))
        nbins = size // 2 + 1
        power = np.zeros(nbins)
        block_min = np.full(nbins, np.inf)
        history = []
        noise = np.full(nbins, 1e-10)
        prev_clean = np.zeros(nbins)
        for i in range(n):
            seg = x[i * hop:i * hop + size] * self.window
            spec = np.fft.rfft(seg)
            mag2 = np.abs(spec) ** 2
            power = mag2 if i == 0 else self.smooth * power + (1 - self.smooth) * mag2
            block_min = np.minimum(block_min, power)
            if (i + 1) % self.per_block == 0:
                history = (history + [block_min])[-self.blocks:]
                block_min = np.full(nbins, np.inf)
            floor = np.minimum(block_min, np.min(history, axis=0)) if history else block_min
            noise = np.maximum(self.over * floor, 1e-10)
            post = mag2 / noise
            prior = 0.98 * prev_clean / noise + 0.02 * np.maximum(post - 1, 0)
            gain = np.maximum(prior / (1 + prior), self.floor)
            prev_clean = (gain ** 2) * mag2
            out[i * hop:i * hop + size] += np.fft.irfft(gain * spec, size) * self.window
        return out
