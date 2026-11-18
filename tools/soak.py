"""Long one-way runs: does the receiver stay locked and keep decoding for the length of a real call?

Ten minutes (by default) of one mode through each path, with the sender's and receiver's sample
clocks 40 parts per million apart. Reports the share of frames recovered, the longest run of
lost frames, how often the receiver had to start over, and the clock offset it measured.

    python tools/soak.py [minutes]
"""
import os
import sys

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

from concurrent.futures import ProcessPoolExecutor      # noqa: E402

import numpy as np                                      # noqa: E402

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import rxbench                                          # noqa: E402
from veilspeak import modem, modes as M                 # noqa: E402
from veilspeak.cellsim import call as CS                # noqa: E402

L = CS.Leg
RUNS = [
    ("2G full rate AMR 12.2, 1% loss", (L("amrnb", 12200, loss=0.01, burst=2),), "F", 40.0),
    ("2G half rate AMR 7.4, 1% loss", (L("amrnb", 7400, vad=2, loss=0.01, burst=2),), "R", -40.0),
    ("VoLTE AMR-WB 12.65, 0.5% loss", (L("amrwb", 12650, loss=0.005),), "F", 40.0),
    ("VoLTE EVS 13.2, jitter buffer, 1% loss", (L("evs", 13200, jitter_ms=10, loss=0.01),), "F", -40.0),
    ("VoLTE EVS 9.6, jitter buffer, 1% loss", (L("evs", 9600, jitter_ms=10, loss=0.01),), "R", 40.0),
    ("VoLTE > G.711 > 2G half rate", (L("amrwb", 12650), L("g711"), L("amrnb", 7400, vad=2)), "R", -40.0),
    ("2G lowest rate AMR 4.75 (keep-alive frames)", (L("amrnb", 4750),), "I", 40.0),
]


def one(job):
    (name, legs, letter, ppm), minutes = job
    kind = rxbench.KINDS[letter]
    count = int(minutes * 60 / 0.48)
    x, sent = rxbench.make(kind, 7, count)
    y = CS.run(CS.Path(name, legs, clock_ppm=ppm, seed=7), np.concatenate([np.zeros(2345), x, np.zeros(8000)]))
    sink = rxbench.Sink(sent)
    rx = modem.Receiver(sink)
    longest_buffer = 0
    for i in range(0, len(y), 1777):
        rx.push(y[i:i + 1777])
        longest_buffer = max(longest_buffer, len(rx.buf))
    got = [(kind, m) in sink.got for _, msgs in sent[:-1] for m in msgs]
    run = longest = 0
    for ok in got:
        run = 0 if ok else run + 1
        longest = max(longest, run)
    per_frame = 0.48 / M.MODES[kind].frames
    return (name, M.MODES[kind].name, minutes, float(np.mean(got)), longest * per_frame, rx.stats,
            -rx.rate / M.SF_SAMPLES * 1e6, ppm, longest_buffer / 8000.0)


if __name__ == "__main__":
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    with ProcessPoolExecutor(7) as pool:
        rows = list(pool.map(one, [(r, minutes) for r in RUNS]))
    print("%-46s %-6s %5s %8s %9s %7s %6s %9s %14s" % (
        "path", "mode", "min", "frames", "longest", "relocks", "jumps", "repaired", "clock ppm"))
    for name, mode, mins, share, gap, stats, ppm, true_ppm, buf in rows:
        print("%-46s %-6s %5.0f %7.2f%% %7.2f s %7d %6d %9d %6.0f (%+.0f)" % (
            name, mode, mins, 100 * share, gap, stats["relocks"], stats["jumps"], stats["repaired"], ppm, true_ppm))
