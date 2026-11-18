"""Finer silence-detector probe for the 10 ms / 23-tone grid: which pause patterns keep each
encoder coding the modem as speech, across signal levels, line noise in the pauses and seeds.

Prints, per pattern and encoder, the worst case over all conditions of the share of in-burst
frames coded as speech in the last two thirds of a 60 s signal.

    python tools/vad_probe2.py
"""
import itertools
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, ".")
from veilspeak.cellsim import codecs as C           # noqa: E402
from veilspeak.waveform import FS, Waveform         # noqa: E402

W = Waveform(80, 80, tuple(range(4, 27)))
ENC = {
    "NB12.2 v1": lambda x: [C.amr_frame_kind(f, 8) for f in C.amrnb_encode(x, 12200, True, 1)],
    "NB12.2 v2": lambda x: [C.amr_frame_kind(f, 8) for f in C.amrnb_encode(x, 12200, True, 2)],
    "NB7.4 v1": lambda x: [C.amr_frame_kind(f, 8) for f in C.amrnb_encode(x, 7400, True, 1)],
    "NB7.4 v2": lambda x: [C.amr_frame_kind(f, 8) for f in C.amrnb_encode(x, 7400, True, 2)],
    "NB4.75 v2": lambda x: [C.amr_frame_kind(f, 8) for f in C.amrnb_encode(x, 4750, True, 2)],
    "WB12.65": lambda x: [C.amr_frame_kind(f, 9) for f in C.amrwb_encode(resample_poly(x, 2, 1), 12650)],
    "WB6.6": lambda x: [C.amr_frame_kind(f, 9) for f in C.amrwb_encode(resample_poly(x, 2, 1), 6600)],
    "EVS13.2": lambda x: [C.evs_frame_kind(f) for f in C.evs_encode(resample_poly(x, 2, 1), 16000, 13200)],
    "EVS9.6": lambda x: [C.evs_frame_kind(f) for f in C.evs_encode(resample_poly(x, 2, 1), 16000, 9600)],
    "EVS=WB": lambda x: [C.evs_frame_kind(f) for f in C.evs_encode(resample_poly(x, 2, 1), 16000, 12650)],
}
PATTERNS = [(0, 0), (400, 80), (400, 60), (400, 40), (420, 60), (440, 40), (480, 80), (560, 80), (640, 80),
            (320, 40), (240, 40), (160, 40), (320, 60), (360, 60), (400, 100), (400, 120)]
LEVELS = (-12.0, 0.0, 6.0)
NOISE = (None, -50.0)
SEEDS = (0, 1)


def probe(job):
    (on_ms, off_ms), level, noise, seed, enc = job
    rng = np.random.default_rng(seed)
    nsym = 60 * FS // W.n
    x = W.synth(rng.integers(0, 4, (nsym, W.k))) * 10 ** (level / 20)
    t = np.arange(len(x))
    on = np.ones(len(x), bool) if not off_ms else (t % ((on_ms + off_ms) * 8)) < on_ms * 8
    x = np.concatenate([np.zeros(8000), x * on])
    on = np.concatenate([np.zeros(8000, bool), on])
    if noise is not None:
        x = x + rng.normal(0, 10 ** (noise / 20), len(x))
    kinds = ENC[enc](np.clip(x, -1, 1))
    inside = on[:len(kinds) * 160].reshape(-1, 160).all(axis=1)
    speech = np.array([k == "speech" for k in kinds])
    third = len(kinds) // 3
    return (on_ms, off_ms), enc, float(speech[third:][inside[third:]].mean())


if __name__ == "__main__":
    jobs = list(itertools.product(PATTERNS, LEVELS, NOISE, SEEDS, ENC))
    with ProcessPoolExecutor(8) as pool:
        rows = list(pool.map(probe, jobs, chunksize=8))
    worst = {}
    for pat, enc, a in rows:
        worst[(pat, enc)] = min(worst.get((pat, enc), 1.0), a)
    print("worst case over levels, pause noise and seeds: share of in-burst frames coded as speech")
    print("%-11s " % "on/off ms" + " ".join("%-9s" % e for e in ENC))
    for pat in PATTERNS:
        name = "continuous" if not pat[1] else "%d/%d" % pat
        print("%-11s " % name + " ".join("%-9s" % ("%3.0f%%" % (100 * worst[(pat, e)])) for e in ENC))
