"""Coverage map: which transmission mode gets through which phone codec, at every bit rate.

For each codec setting the sender transmits TRAIN, TRAIN and then 70 superframes of one mode
with random payloads; the receiver has to find and decode them. No frame loss and no jitter:
this measures the codec alone. The silence detector (DTX) is on, as in real networks.

    python tools/coverage.py out.csv [seeds] [--fixed]
    python tools/coverage.py --print out.csv

--fixed runs the same test through the fixed-point reference codecs (the bit-exact ones that
phones are tested against) instead of the floating-point ones.

Verdict per codec setting (what a call over it gets):
    FAST     Codec2 1200: the 2.4 kbit/s mode arrives whole and the signal-to-distortion ratio
             is above the level at which a unit asks for it
    ROBUST   Codec2 700C: only the 1.2 kbit/s mode is safe
    marginal the 1.2 kbit/s mode loses some frames (80-99% arrive): speech with gaps
    link     only keep-alive frames get through: the units stay connected but carry no speech
"""
import csv
import os
import sys
from dataclasses import replace

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

from concurrent.futures import ProcessPoolExecutor      # noqa: E402

import numpy as np                                      # noqa: E402

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import rxbench                                          # noqa: E402
from veilspeak import modem, modes as M                 # noqa: E402
from veilspeak.cellsim import call as CS                # noqa: E402
from veilspeak.endpoint import FAST_SDR_DB              # noqa: E402

L = CS.Leg
G711 = L("g711")
PATHS = {}
for _r in (12200, 10200, 7950, 7400, 6700, 5900, 5150, 4750):
    PATHS["2G/3G  AMR %.2f" % (_r / 1000)] = (L("amrnb", _r),)
PATHS["2G/3G  AMR 12.20, second silence detector"] = (L("amrnb", 12200, vad=2),)
PATHS["2G/3G  AMR 7.40, second silence detector"] = (L("amrnb", 7400, vad=2),)
for _r in (23850, 23050, 19850, 18250, 15850, 14250, 12650, 8850, 6600):
    PATHS["VoLTE  AMR-WB %.2f" % (_r / 1000)] = (L("amrwb", _r),)
for _r in (24400, 16400, 13200, 9600, 8000, 7200):
    PATHS["VoLTE  EVS %.1f wideband" % (_r / 1000)] = (L("evs", _r),)
PATHS["VoLTE  EVS 5.9 variable rate"] = (L("evs", 5900),)
PATHS["VoLTE  EVS 13.2 channel-aware"] = (L("evs", 13200, channel_aware=("LO", 3)),)
PATHS["VoLTE  EVS 13.2 narrowband"] = (L("evs", 13200, evs_fs=8000),)
PATHS["VoLTE  EVS 9.6 narrowband"] = (L("evs", 9600, evs_fs=8000),)
PATHS["VoLTE  AMR-WB 12.65 inside an EVS phone"] = (L("evs", 12650),)
PATHS["VoLTE  AMR-WB 8.85 inside an EVS phone"] = (L("evs", 8850),)
PATHS["landline G.711"] = (G711,)
PATHS["across networks  AMR 12.2 > G.711 > AMR 12.2"] = (L("amrnb", 12200), G711, L("amrnb", 12200))
PATHS["across networks  AMR 12.2 > G.711 > AMR 7.4"] = (L("amrnb", 12200), G711, L("amrnb", 7400))
PATHS["across networks  AMR 7.4 > G.711 > AMR 7.4"] = (L("amrnb", 7400), G711, L("amrnb", 7400))
PATHS["across networks  AMR-WB 12.65 > G.711 > AMR 12.2"] = (L("amrwb", 12650), G711, L("amrnb", 12200))
PATHS["across networks  AMR-WB 12.65 > G.711 > AMR 7.4"] = (L("amrwb", 12650), G711, L("amrnb", 7400))
PATHS["across networks  AMR-WB 12.65 > G.711 > AMR-WB 12.65"] = (L("amrwb", 12650), G711, L("amrwb", 12650))
PATHS["across networks  EVS 13.2 > G.711 > AMR 12.2"] = (L("evs", 13200), G711, L("amrnb", 12200))
PATHS["across networks  EVS 13.2 > G.711 > EVS 13.2"] = (L("evs", 13200), G711, L("evs", 13200))
MODES = {"F": M.FAST, "R": M.ROBUST, "I": M.IDLE}


def one(job):
    name, letter, seed, fixed = job
    kind = MODES[letter]
    x, sent = rxbench.make(kind, seed)
    legs = tuple(replace(leg, fixed=fixed and leg.codec != "g711") for leg in PATHS[name])
    path = CS.Path(name, legs, clock_ppm=(25.0, -35.0)[seed % 2], seed=seed)
    y = CS.run(path, np.concatenate([np.zeros(2345), x, np.zeros(8000)]))
    sdr = []

    class Sink(rxbench.Sink):
        def superframe(self, k, when, info):
            if info.get("sdr_db") is not None and k is not None:
                sdr.append(info["sdr_db"])

    sink = Sink(sent)
    rx = modem.Receiver(sink)
    for i in range(0, len(y), 1777):
        rx.push(y[i:i + 1777])
    want = [(kind, m) for _, msgs in sent[:-1] for m in msgs]
    return name, letter, sum(w in sink.got for w in want), len(want), sdr


def verdict(share, sdr_db):
    if share["F"] >= 0.99 and share["R"] >= 0.99 and sdr_db >= FAST_SDR_DB:
        return "FAST"
    if share["R"] >= 0.99:
        return "ROBUST"
    if share["R"] >= 0.80:
        return "marginal"
    return "link" if share["I"] >= 0.95 else "nothing"


def main():
    fixed = "--fixed" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--fixed"]
    out = args[0]
    seeds = int(args[1]) if len(args) > 1 else 4
    jobs = [(name, letter, seed, fixed) for name in PATHS for letter in MODES for seed in range(1, seeds + 1)]
    with ProcessPoolExecutor(7) as pool:
        results = list(pool.map(one, jobs))
    print("%-52s %7s %7s %7s %8s  %s" % ("codec", "fast", "robust", "idle", "SDR dB", "verdict"))
    with open(out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["codec", "fast_frames", "robust_frames", "idle_frames", "sdr_db", "verdict"])
        for name in PATHS:
            share, sdr = {}, []
            for letter in MODES:
                rs = [r for r in results if r[0] == name and r[1] == letter]
                share[letter] = sum(r[2] for r in rs) / sum(r[3] for r in rs)
                sdr += [v for r in rs for v in r[4]]
            level = float(np.median(sdr)) if sdr else float("nan")
            print("%-52s %6.1f%% %6.1f%% %6.1f%% %8.1f  %s" % (
                name, 100 * share["F"], 100 * share["R"], 100 * share["I"], level, verdict(share, level)))
            writer.writerow([name, round(share["F"], 4), round(share["R"], 4), round(share["I"], 4), round(level, 1), verdict(share, level)])


def reprint(path):
    """Prints the table again from a CSV written earlier."""
    print("%-52s %7s %7s %7s %8s  %s" % ("codec", "fast", "robust", "idle", "SDR dB", "verdict"))
    for r in csv.DictReader(open(path)):
        share = {"F": float(r["fast_frames"]), "R": float(r["robust_frames"]), "I": float(r["idle_frames"])}
        print("%-52s %6.1f%% %6.1f%% %6.1f%% %8.1f  %s" % (
            r["codec"], 100 * share["F"], 100 * share["R"], 100 * share["I"], float(r["sdr_db"]), verdict(share, float(r["sdr_db"]))))


if __name__ == "__main__":
    if sys.argv[1] == "--print":
        reprint(sys.argv[2])
    else:
        main()
