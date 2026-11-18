"""The adversary: a speech-vs-modem detector, and the audio to train and test it on.

A carrier that wants to find or block the signal runs a classifier on call audio. This module
builds a plausible version of that classifier from cheap, well-understood acoustic features,
and the two kinds of audio it judges:

  * speech  -- ordinary conversation (real recordings passed in by the caller);
  * modem   -- the Track A transmitted signal, synthesised here.

The modem audio is faithful without needing the codecs or the crypto. Track A encrypts with
AES-CTR and then Reed-Solomon codes the result, so the symbols on the wire are uniform over
the codebook; drawing random codebook symbols therefore gives the exact signal statistics a
real call would, pause structure and all. So this module depends only on numpy/scipy and the
modem's own waveform code.

Everything is level-blind: each analysis window is scaled to unit RMS before features are
taken, so a detector cannot win on loudness alone (a carrier normalises). What is left is
structure, which is where a 23-line tone comb in regular 480 ms bursts differs from speech.

The classifier is a plain logistic regression (no sklearn); `auc` reports ranking quality and
needs no training, so single-feature detectability can be read off without any fitting at all.
"""
import numpy as np

from .. import modes as M
from ..waveform import FS

# ---- features ---------------------------------------------------------------------------

BAND = (300.0, 3400.0)                 # telephone band the detector looks in
TONES = M.GRID.hz                      # the 23 modem tones, 400..2600 Hz, 100 Hz apart

FEATURES = [
    "comb_db",          # power on the exact 100 Hz tone comb vs the rest of the band (dB)
    "flatness",         # spectral flatness in band: a line spectrum is peaky, so low
    "band_frac",        # share of power inside the 400-2600 Hz modem band
    "lo_frac",          # share of power below 350 Hz (speech has some; the modem none)
    "hi_frac",          # share of power above 2650 Hz (same)
    "crest",            # peak / RMS: the modem waveform is built for a low crest factor
    "f0_med",           # median voiced pitch (the comb reads as a steady ~100 Hz pitch)
    "f0_std",           # spread of voiced pitch (speech pitch moves; the comb does not)
    "voiced_frac",      # share of frames that look voiced
    "burst_ac",         # envelope self-similarity at the 480 ms superframe period
    "silent_frac",      # share of 10 ms frames that are near-silent (the 80 ms pauses)
    "zcr_std",          # variation of the zero-crossing rate over the window
]


def _psd(x):
    w = np.hanning(len(x))
    X = np.abs(np.fft.rfft(x * w)) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / FS)
    return f, X


def _envelope(x, frame=80):            # energy of 10 ms frames
    n = len(x) // frame
    return (x[:n * frame].reshape(n, frame) ** 2).mean(axis=1) if n else np.zeros(1)


def _pitch(x, frame=240, hop=120):     # autocorrelation pitch over 30 ms frames
    lo, hi = FS // 400, FS // 80       # lags for 80..400 Hz
    f0s, voiced, total = [], 0, 0
    for i in range(0, max(len(x) - frame, 0), hop):
        fr = x[i:i + frame] - x[i:i + frame].mean()
        e = float(fr @ fr)
        if e < 1e-6:
            continue
        ac = np.correlate(fr, fr, "full")[frame - 1:]
        if len(ac) <= hi:
            continue
        k = int(np.argmax(ac[lo:hi])) + lo
        total += 1
        if ac[k] / (ac[0] + 1e-12) > 0.35:
            voiced += 1
            f0s.append(FS / k)
    if not f0s:
        return 100.0, 0.0, 0.0
    return float(np.median(f0s)), float(np.std(f0s)), voiced / max(total, 1)


def feature_vector(x):
    """A 1-D float window (any length, nominally 1 s) -> features in FEATURES order."""
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    rms = np.sqrt((x @ x) / len(x)) + 1e-12
    x = x / rms                                             # level-blind

    f, P = _psd(x)
    band = (f >= BAND[0]) & (f <= BAND[1])
    band_p = P[band].sum() + 1e-12
    comb = np.zeros(len(f), bool)
    for hz in TONES:
        comb |= np.abs(f - hz) <= 2.0
    comb_p = P[comb].sum()
    comb_db = 10 * np.log10(comb_p / (band_p - comb_p + 1e-12) + 1e-12)
    flatness = float(np.exp(np.log(P[band] + 1e-20).mean()) / (P[band].mean() + 1e-20))
    total_p = P[(f >= 0) & (f <= 4000)].sum() + 1e-12
    band_frac = float(P[(f >= 400) & (f <= 2600)].sum() / total_p)
    lo_frac = float(P[(f >= 0) & (f < 350)].sum() / total_p)
    hi_frac = float(P[(f > 2650) & (f <= 4000)].sum() / total_p)
    crest = float(np.max(np.abs(x)))                        # RMS is 1

    f0_med, f0_std, voiced_frac = _pitch(x)

    env = _envelope(x)
    e = env - env.mean()
    ac = np.correlate(e, e, "full")[len(e) - 1:]
    ac0 = ac[0] + 1e-12
    lags = [l for l in range(40, 57) if l < len(ac)]        # 400..560 ms around the 480 ms period
    burst_ac = float(max((ac[l] for l in lags), default=0.0) / ac0)
    silent_frac = float((env < 0.15 * (np.median(env) + 1e-12)).mean())

    zc = [np.mean(np.abs(np.diff(np.sign(x[i:i + 80]))) > 0) for i in range(0, len(x) - 80, 80)]
    zcr_std = float(np.std(zc)) if zc else 0.0

    return np.array([comb_db, flatness, band_frac, lo_frac, hi_frac, crest,
                     f0_med, f0_std, voiced_frac, burst_ac, silent_frac, zcr_std])


def feature_matrix(windows):
    return np.vstack([feature_vector(w) for w in windows])


# ---- audio ------------------------------------------------------------------------------

def windows(signal, length=FS, hop=FS // 2, floor=0.02):
    """Slice a 1-D signal into analysis windows, dropping near-silent ones (relative to the
    signal's own peak so that real speech pauses are kept but dead air is not)."""
    signal = np.asarray(signal, dtype=float)
    ref = np.sqrt(np.mean(signal ** 2)) + 1e-12
    out = []
    for i in range(0, len(signal) - length + 1, hop):
        w = signal[i:i + length]
        if np.sqrt(np.mean(w ** 2)) >= floor * ref:
            out.append(w)
    return out


def modem_signal(kind, seconds, seed=0, role=0):
    """`seconds` of the Track A transmitted waveform in the given mode (M.FAST / M.ROBUST /
    M.IDLE), as a float array: a TRAIN burst, then superframes carrying uniform random symbols.
    Faithful to a real call because real payload is encrypted-then-coded, hence uniform."""
    from ..modem import Framer
    fr = Framer(role)
    mode = M.MODES[kind]
    parts, hi = len(mode.parts), 1 << mode.sym_bits
    per = M.DATA_SYMS * parts // mode.frames
    rng = np.random.default_rng(seed)
    out = [fr.superframe(M.TRAIN)]
    for _ in range(int(seconds * FS / M.SF_SAMPLES) + 1):
        frames = [rng.integers(0, hi, per) for _ in range(mode.frames)]
        out.append(fr.superframe(kind, frames))
    return np.concatenate(out)[:int(seconds * FS)]


# ---- classifier and scoring -------------------------------------------------------------

def auc(scores, labels):
    """Area under the ROC curve: the chance a random positive outranks a random negative.
    0.5 is indistinguishable, 1.0 perfectly separable. Rank statistic, no fitting."""
    scores, labels = np.asarray(scores, float), np.asarray(labels, int)
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores))
    ranks[order] = np.arange(1, len(scores) + 1)
    # average ranks for ties
    s = scores[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    npos, nneg = int((labels == 1).sum()), int((labels == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    return float((ranks[labels == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg))


class Logistic:
    """Logistic regression by gradient descent on standardised features (numpy only)."""

    def __init__(self, l2=1e-3, iters=4000, lr=0.3):
        self.l2, self.iters, self.lr = l2, iters, lr

    def fit(self, X, y):
        self.mean_, self.std_ = X.mean(0), X.std(0) + 1e-9
        Z = (X - self.mean_) / self.std_
        n, d = Z.shape
        self.w, self.b = np.zeros(d), 0.0
        y = np.asarray(y, float)
        for _ in range(self.iters):
            p = 1 / (1 + np.exp(-(Z @ self.w + self.b)))
            g = p - y
            self.w -= self.lr * (Z.T @ g / n + self.l2 * self.w)
            self.b -= self.lr * g.mean()
        return self

    def score(self, X):
        return (X - self.mean_) / self.std_ @ self.w + self.b
