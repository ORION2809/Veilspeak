"""Modem bake-off: candidate waveforms through the 3GPP reference codecs.

For each (waveform, codebook, codec setting) it sends random symbols for 40 s and reports
  active  fraction of 20 ms frames the encoder coded as speech (the rest it treated as silence)
  ser     symbol error rate with ideal timing and channel knowledge, over the whole signal
  ser_act the same, counting only symbols that lay in frames coded as speech
This ranks waveforms; it is not the full receiver.

    python tools/bakeoff.py out.csv [quick]
"""
import itertools
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, ".")
from veilspeak.cellsim import codecs as C           # noqa: E402
from veilspeak.dsp import frac_window             # noqa: E402
from veilspeak.waveform import FS, Waveform         # noqa: E402

SECONDS = 40
B8 = tuple(range(1, 9))


def r(lo, hi):
    return tuple(range(lo, hi + 1))


# name: (waveform, codebook sizes in bits)
WAVEFORMS = {
    "A 400Hz 8h 2.5ms (P2 Table 4)":   (Waveform(20, 20, B8), (6,)),
    "B 200Hz 10h 5ms 600-2400 (P2 code)": (Waveform(40, 40, r(3, 12)), (6, 8, 10, 12)),
    "B 200Hz 12h 5ms 400-2600":        (Waveform(40, 40, r(2, 13)), (8, 12)),
    "B 200Hz 15h 5ms 400-3200":        (Waveform(40, 40, r(2, 16)), (12,)),
    "B 200Hz 8h 5ms 600-2000":         (Waveform(40, 40, r(3, 10)), (6, 12)),
    "C 200Hz 10h 6.25ms guard":        (Waveform(50, 40, r(3, 12)), (6, 12)),
    "C 200Hz 10h 7.5ms guard":         (Waveform(60, 40, r(3, 12)), (6, 12)),
    "D 133Hz 14h 7.5ms 667-2400":      (Waveform(60, 60, r(5, 18)), (9, 12)),
    "E 100Hz 19h 10ms 600-2400":       (Waveform(80, 80, r(6, 24)), (6, 8, 12)),
    "F 267Hz 8h 3.75ms 533-2400":      (Waveform(30, 30, r(2, 9)), (6, 9, 12)),
}


def channels(quick):
    ch = {}
    nb = [12200, 7400, 4750] if quick else [12200, 10200, 7950, 7400, 6700, 5900, 5150, 4750]
    for r in nb:
        ch["AMR-NB %.2f" % (r / 1000)] = ("amrnb", r, True, 1)
    ch["AMR-NB 12.20 VAD2"] = ("amrnb", 12200, True, 2)
    ch["AMR-NB 12.20 no DTX"] = ("amrnb", 12200, False, 1)
    if not quick:
        ch["AMR-NB 7.40 VAD2"] = ("amrnb", 7400, True, 2)
        ch["AMR-NB 7.40 no DTX"] = ("amrnb", 7400, False, 1)
    for r in ([12650] if quick else [6600, 8850, 12650, 15850, 23850]):
        ch["AMR-WB %.2f" % (r / 1000)] = ("amrwb", r, True)
    ch["AMR-WB 12.65 no DTX"] = ("amrwb", 12650, False)
    for r in ([13200] if quick else [7200, 8000, 9600, 13200, 16400, 24400]):
        ch["EVS %.1f" % (r / 1000)] = ("evs", r, 16000, True)
    ch["EVS 13.2 no DTX"] = ("evs", 13200, 16000, False)
    if not quick:
        ch["EVS 5.9 VBR"] = ("evs", 5900, 16000, True)
        ch["EVS 9.6 (8 kHz in)"] = ("evs", 9600, 8000, True)
        ch["EVS 13.2 (8 kHz in)"] = ("evs", 13200, 8000, True)
        ch["EVS as AMR-WB 12.65"] = ("evs", 12650, 16000, True)
    return ch


def through(spec, x):
    """Returns (audio at 8 kHz, boolean per 20 ms input frame: coded as speech)."""
    kind = spec[0]
    if kind == "amrnb":
        frames = C.amrnb_encode(x, spec[1], spec[2], spec[3])
        return C.amrnb_decode(frames), np.array([C.amr_frame_kind(f, 8) == "speech" for f in frames])
    if kind == "amrwb":
        frames = C.amrwb_encode(resample_poly(x, 2, 1), spec[1], spec[2])
        return resample_poly(C.amrwb_decode(frames), 1, 2), np.array([C.amr_frame_kind(f, 9) == "speech" for f in frames])
    fs = spec[2]
    frames = C.evs_encode(x if fs == FS else resample_poly(x, fs // FS, 1), fs, spec[1], spec[3])
    y = C.evs_decode(frames, fs)
    return (y if fs == FS else resample_poly(y, 1, fs // FS)), np.array([C.evs_frame_kind(f) == "speech" for f in frames])


def one(job):
    wname, bits, cname, spec, silence_every = job
    w = WAVEFORMS[wname][0]
    book = w.book(bits)
    ref = np.conj(np.exp(0.5j * np.pi * book))
    rng = np.random.default_rng(abs(hash((wname, bits))) % 2**32)
    nsym = SECONDS * FS // w.n
    idx = rng.integers(0, len(book), nsym)
    x = w.synth(book[idx])
    gap = np.ones(nsym, bool)
    if silence_every:                                  # P2: a silent 80 ms block every N blocks
        block = (np.arange(nsym) * w.n) // 640
        gap = block % silence_every != silence_every - 1
        x = x * np.repeat(gap, w.n)
    lead = 800
    y, speech = through(spec, np.concatenate([np.zeros(lead), x, np.zeros(800)]))
    # coarse delay by cross-correlation over the first 3 s, then the fractional offset with
    # the best signal-to-distortion ratio
    seg = 3 * FS
    corr = np.correlate(y[:seg + 1200], np.concatenate([np.zeros(lead), x])[:seg], "valid")
    lag = int(np.argmax(np.abs(corr)))
    best = (-1, None)
    for d in np.arange(-4, 4.01, 0.25):
        bins = w.demux(frac_window(y, lead + lag + d, nsym * w.n))
        z = (bins * ref[idx])[gap]
        m = z.mean(axis=0)
        q = float((np.abs(m) ** 2 / ((np.abs(z - m) ** 2).mean(axis=0) + 1e-9)).sum())
        if q > best[0]:
            best = (q, bins, m, (np.abs(z - m) ** 2).mean(axis=0), d)
    q, bins, mu, var, d = best
    var = np.maximum(var, 0.2 * var.mean())
    decided = ((bins * (np.conj(mu) / var)) @ ref.T).real.argmax(axis=1)
    wrong = decided != idx
    frame_of = np.minimum((lead + np.arange(nsym) * w.n) // 160, len(speech) - 1)
    last = np.minimum((lead + np.arange(nsym) * w.n + w.n - 1) // 160, len(speech) - 1)
    act = speech[frame_of] & speech[last] & gap
    return dict(waveform=wname, bits=bits, kbps=bits * w.baud / 1000, channel=cname, silence_every=silence_every,
                active=float(speech[lead // 160:(lead + len(x)) // 160].mean()),
                ser=float(wrong[gap].mean()), ser_act=float(wrong[act].mean()) if act.any() else float("nan"),
                sdr_db=float(10 * np.log10(np.mean(np.abs(mu) ** 2 / var))), offset=float(d), lag=lag)


if __name__ == "__main__":
    out, quick = sys.argv[1], len(sys.argv) > 2
    chans = channels(quick)
    jobs = [(wn, b, cn, spec, 0) for wn, (w, sizes) in WAVEFORMS.items() for b in sizes
            for cn, spec in chans.items()]
    # P2's remedy against silence detection, on the detectors that matter
    jobs += [(wn, sizes[0], cn, chans[cn], 16) for wn, (w, sizes) in WAVEFORMS.items()
             for cn in ("AMR-NB 12.20", "AMR-NB 12.20 VAD2", "AMR-WB 12.65", "EVS 13.2")]
    for wn, (w, sizes) in WAVEFORMS.items():             # build codebooks before forking
        for b in sizes:
            w.book(b)
    with ProcessPoolExecutor(8) as pool, open(out, "w") as f:
        keys = None
        for row in pool.map(one, jobs, chunksize=2):
            if keys is None:
                keys = list(row)
                f.write(",".join(keys) + "\n")
            f.write(",".join("%.5g" % row[k] if isinstance(row[k], float) else str(row[k]) for k in keys) + "\n")
            f.flush()
    print("wrote", out, len(jobs), "runs")
