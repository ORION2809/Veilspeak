"""How do the codecs' silence detectors treat the modem signal?

Sends random symbols in bursts (on_ms of signal, then off_ms of silence) for 60 s and
reports, for each encoder, the share of 20 ms frames lying fully inside a burst that the
encoder coded as speech. Only the encoders run, so this is fast.

    python tools/vad_probe.py
"""
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, ".")
from veilspeak.cellsim import codecs as C           # noqa: E402
from veilspeak.waveform import FS, Waveform         # noqa: E402

ENCODERS = {
    "AMR 12.2 VAD1": lambda x: [C.amr_frame_kind(f, 8) for f in C.amrnb_encode(x, 12200, True, 1)],
    "AMR 12.2 VAD2": lambda x: [C.amr_frame_kind(f, 8) for f in C.amrnb_encode(x, 12200, True, 2)],
    "AMR 7.4 VAD1": lambda x: [C.amr_frame_kind(f, 8) for f in C.amrnb_encode(x, 7400, True, 1)],
    "AMR 7.4 VAD2": lambda x: [C.amr_frame_kind(f, 8) for f in C.amrnb_encode(x, 7400, True, 2)],
    "AMR 4.75 VAD2": lambda x: [C.amr_frame_kind(f, 8) for f in C.amrnb_encode(x, 4750, True, 2)],
    "AMR-WB 12.65": lambda x: [C.amr_frame_kind(f, 9) for f in C.amrwb_encode(resample_poly(x, 2, 1), 12650)],
    "EVS 13.2": lambda x: [C.evs_frame_kind(f) for f in C.evs_encode(resample_poly(x, 2, 1), 16000, 13200)],
    "EVS 9.6": lambda x: [C.evs_frame_kind(f) for f in C.evs_encode(resample_poly(x, 2, 1), 16000, 9600)],
}


def burst_signal(w, bits, on_ms, off_ms, seconds=60, seed=0, level_db=0.0, lead_ms=1000):
    rng = np.random.default_rng(seed)
    book = w.book(bits)
    nsym = seconds * FS // w.n
    x = w.synth(book[rng.integers(0, len(book), nsym)]) * 10 ** (level_db / 20)
    t = np.arange(len(x))
    if off_ms:
        period = (on_ms + off_ms) * 8
        on = (t % period) < on_ms * 8
    else:
        on = np.ones(len(x), bool)
    lead = np.zeros(lead_ms * 8)
    return np.concatenate([lead, x * on]), np.concatenate([lead.astype(bool), on])


def probe(job):
    wname, w, bits, on_ms, off_ms, level_db, enc = job
    x, on = burst_signal(w, bits, on_ms, off_ms, level_db=level_db)
    kinds = ENCODERS[enc](x)
    inside = on[:len(kinds) * 160].reshape(-1, 160).all(axis=1)
    speech = np.array([k == "speech" for k in kinds])
    third = len(kinds) // 3
    return (wname, on_ms, off_ms, level_db, enc, float(speech[inside].mean()),
            float(speech[2 * third:][inside[2 * third:]].mean()))


if __name__ == "__main__":
    def r(lo, hi):
        return tuple(range(lo, hi + 1))
    waves = {
        "A 8h 2.5ms": (Waveform(20, 20, r(1, 8)), 6),
        "B 10h 5ms": (Waveform(40, 40, r(3, 12)), 6),
        "E 19h 10ms": (Waveform(80, 80, r(6, 24)), 12),
    }
    patterns = [(0, 0), (1200, 80), (800, 80), (400, 80), (320, 80), (240, 80), (400, 40), (400, 160), (160, 80)]
    jobs = [(wn, w, b, on, off, 0.0, e) for wn, (w, b) in waves.items() for on, off in patterns for e in ENCODERS]
    for w, b in waves.values():
        w.book(b)
    with ProcessPoolExecutor(6) as pool:
        rows = list(pool.map(probe, jobs, chunksize=4))
    encs = list(ENCODERS)
    print("share of in-burst frames coded as speech (whole call / last third)")
    print("%-12s %-12s " % ("waveform", "on/off ms") + " ".join("%-13s" % e for e in encs))
    table = {}
    for wn, on, off, lv, e, a, a3 in rows:
        table.setdefault((wn, on, off), {})[e] = (a, a3)
    for (wn, on, off), d in table.items():
        print("%-12s %-12s " % (wn, "continuous" if not off else "%d/%d" % (on, off))
              + " ".join("%3.0f%% / %3.0f%%  " % (100 * d[e][0], 100 * d[e][1]) for e in encs))
