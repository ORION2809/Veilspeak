"""Bake-off round 3: higher-rate modes on the 100 Hz / 10 ms tone grid.

A symbol may carry several sub-symbols, each a codeword over its own subset of the harmonics.
Reports the error rate per sub-symbol (silence detection off, ideal timing).

    python tools/bakeoff3.py out.csv
"""
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from bakeoff import SECONDS, through                 # noqa: E402
from veilspeak import codebook                       # noqa: E402
from veilspeak.dsp import frac_window                # noqa: E402
from veilspeak.waveform import FS, Waveform          # noqa: E402


def r(lo, hi, step=1):
    return tuple(range(lo, hi + 1, step))


G = Waveform(80, 80, r(6, 24))                       # 19 harmonics, 600-2400 Hz
GW = Waveform(80, 80, r(4, 26))                      # 23 harmonics, 400-2600 Hz
B12 = Waveform(40, 40, r(2, 13))
A = Waveform(20, 20, r(1, 8))
# name: (waveform, [(harmonic positions, bits), ...])
MODES = {
    "A 2.5ms 6b (P2)  2.4k":         (A, [(r(0, 7), 6)]),
    "B12h 5ms 12b     2.4k":         (B12, [(r(0, 11), 12)]),
    "G19 12b          1.2k":         (G, [(r(0, 18), 12)]),
    "G19 16b          1.6k":         (G, [(r(0, 18), 16)]),
    "G19 9+9 even/odd 1.8k":         (G, [(r(0, 18, 2), 9), (r(1, 17, 2), 9)]),
    "G19 12+12 even/odd 2.4k":       (G, [(r(0, 18, 2), 12), (r(1, 17, 2), 12)]),
    "G19 12+12 low/high 2.4k":       (G, [(r(0, 9), 12), (r(10, 18), 12)]),
    "G19 8+8+8 interleaved 2.4k":    (G, [(r(0, 18, 3), 8), (r(1, 18, 3), 8), (r(2, 18, 3), 8)]),
    "G23 12+12 even/odd 2.4k":       (GW, [(r(0, 22, 2), 12), (r(1, 21, 2), 12)]),
    "G23 16b          1.6k":         (GW, [(r(0, 22), 16)]),
    "G23 12+12+12 interleaved 3.6k": (GW, [(r(0, 22, 3), 12), (r(1, 22, 3), 12), (r(2, 22, 3), 12)]),
}
CHANNELS = {
    "NB 12.2": ("amrnb", 12200, False, 1), "NB 10.2": ("amrnb", 10200, False, 1),
    "NB 7.95": ("amrnb", 7950, False, 1), "NB 7.4": ("amrnb", 7400, False, 1), "NB 6.7": ("amrnb", 6700, False, 1),
    "WB 8.85": ("amrwb", 8850, False), "WB 12.65": ("amrwb", 12650, False),
    "WB 15.85": ("amrwb", 15850, False), "WB 23.85": ("amrwb", 23850, False),
    "EVS 9.6": ("evs", 9600, 16000, False), "EVS 13.2": ("evs", 13200, 16000, False),
    "EVS 16.4": ("evs", 16400, 16000, False), "EVS 24.4": ("evs", 24400, 16000, False),
    "EVS=WB 12.65": ("evs", 12650, 16000, False), "EVS13.2 nb": ("evs", 13200, 8000, False),
}


def books(mode):
    w, parts = MODES[mode]
    return [codebook.base_code(bits, len(pos)) for pos, bits in parts]


def one(job):
    mode, cname = job
    w, parts = MODES[mode]
    bks = books(mode)
    rng = np.random.default_rng(abs(hash(mode)) % 2**32)
    nsym = SECONDS * FS // w.n
    phases = np.zeros((nsym, w.k), int)
    idx = []
    for (pos, bits), book in zip(parts, bks):
        i = rng.integers(0, len(book), nsym)
        idx.append(i)
        phases[:, list(pos)] = book[i]
    scramble = np.random.default_rng(7).integers(0, 4, w.k)      # fixed phase offsets: lower peaks
    phases = (phases + scramble) % 4
    x = w.synth(phases)
    lead = 800
    y, _ = through(CHANNELS[cname], np.concatenate([np.zeros(lead), x, np.zeros(800)]))
    seg = 3 * FS
    corr = np.correlate(y[:seg + 1200], np.concatenate([np.zeros(lead), x])[:seg], "valid")
    lag = int(np.argmax(np.abs(corr)))
    ref = np.conj(np.exp(0.5j * np.pi * phases))
    best = None
    for d in np.arange(-4, 4.01, 0.25):
        bins = w.demux(frac_window(y, lead + lag + d, nsym * w.n))
        z = bins * ref
        m = z.mean(axis=0)
        v = (np.abs(z - m) ** 2).mean(axis=0)
        q = float((np.abs(m) ** 2 / (v + 1e-9)).sum())
        if best is None or q > best[0]:
            best = (q, bins, m, v)
    q, bins, mu, var = best
    var = np.maximum(var, 0.2 * var.mean())
    y_eq = bins * (np.conj(mu) / var) * np.exp(-0.5j * np.pi * scramble)
    errs = []
    for (pos, bits), book, i in zip(parts, bks, idx):
        bref = np.conj(np.exp(0.5j * np.pi * book))
        dec = np.concatenate([(y_eq[s:s + 500][:, list(pos)] @ bref.T).real.argmax(axis=1) for s in range(0, nsym, 500)])
        errs.append(float((dec != i).mean()))
    total_bits = sum(b for _, b in parts)
    return (mode, cname, total_bits * w.baud / 1000, float(np.mean(errs)),
            float(10 * np.log10(np.mean(np.abs(mu) ** 2 / var))))


if __name__ == "__main__":
    for m in MODES:
        books(m)
    jobs = [(m, c) for m in MODES for c in CHANNELS]
    with ProcessPoolExecutor(8) as pool:
        rows = list(pool.map(one, jobs, chunksize=2))
    with open(sys.argv[1], "w") as f:
        f.write("mode,channel,kbps,sub_symbol_error_rate,sdr_db\n")
        for row in rows:
            f.write("%s,%s,%.2f,%.5f,%.1f\n" % row)
    chans = list(CHANNELS)
    print("%-30s " % "sub-symbol error rate" + " ".join("%-8s" % c[:8] for c in chans))
    for m in MODES:
        d = {c: e for mm, c, k, e, s in rows if mm == m}
        print("%-30s " % m + " ".join("%-8.4f" % d[c] for c in chans))
    print("%-30s " % "signal-to-distortion, dB" + " ".join("%-8s" % c[:8] for c in chans))
    for m in MODES:
        d = {c: s for mm, c, k, e, s in rows if mm == m}
        print("%-30s " % m + " ".join("%-8.1f" % d[c] for c in chans))
