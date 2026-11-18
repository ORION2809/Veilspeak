"""One-way receiver benchmark: fixed-mode frames through phone paths, share of frames recovered.

No key agreement and no mode switching: the sender transmits TRAIN, TRAIN, then 70 superframes
of one mode with random payloads, and the receiver has to find and decode them. This isolates
the modem and its timing recovery, and runs in seconds, so it is the regression test to run
after any change to the receiver.

    python tools/rxbench.py [name filter] [seeds]
"""
import os
import sys

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")          # many small matrices: threads only get in each other's way

from concurrent.futures import ProcessPoolExecutor      # noqa: E402

import numpy as np                                      # noqa: E402

sys.path.insert(0, ".")
from veilspeak import fec, modem, modes as M            # noqa: E402
from veilspeak.cellsim import call as CS                # noqa: E402

L = CS.Leg
PATHS = {
    "clean wire":                        ((), "FRI"),
    "AMR 12.2, 3% loss in runs":         ((L("amrnb", 12200, loss=0.03, burst=3),), "FR"),
    "AMR 7.4, 1% loss":                  ((L("amrnb", 7400, vad=2, loss=0.01, burst=2),), "R"),
    "AMR 4.75":                          ((L("amrnb", 4750),), "I"),
    "AMR-WB 12.65, 0.5% loss":           ((L("amrwb", 12650, loss=0.005),), "FR"),
    "VoLTE > G.711 > AMR 7.4":           ((L("amrwb", 12650), L("g711"), L("amrnb", 7400, vad=2)), "R"),
    "EVS 13.2, jitter 10 ms, 1% loss":   ((L("evs", 13200, jitter_ms=10, loss=0.01),), "FR"),
    "EVS 9.6, jitter 10 ms":             ((L("evs", 9600, jitter_ms=10, loss=0.005),), "R"),
    "AMR-WB in EVS phone, jitter 10 ms": ((L("evs", 12650, jitter_ms=10, loss=0.005),), "R"),
    "EVS 13.2, jitter 25 ms, spikes, 2% loss": ((L("evs", 13200, jitter_ms=25, spike_prob=0.003, loss=0.02, burst=1.5),), "FR"),
}
KINDS = {"F": M.FAST, "R": M.ROBUST, "I": M.IDLE}
SUPERFRAMES = 70


class Sink:
    def __init__(self, sent):
        self.codecs = {k: fec.FrameCodec(m) for k, m in M.MODES.items()}
        self.valid = {k: set() for k in M.MODES}
        for kind, msgs in sent:
            self.valid[kind].update(msgs)
        self.got = set()
        self.wrong = 0

    def frame(self, kind, index, symbols, confidence, when):
        hit = self.codecs[kind].decode(symbols, confidence, lambda m: m in self.valid[kind])
        if hit is None:
            return None
        self.got.add((kind, hit[0]))
        return hit[1]

    def superframe(self, kind, when, info):
        pass


def make(kind, seed, superframes=SUPERFRAMES):
    """Audio of TRAIN, TRAIN and then `superframes` superframes of one mode, and what they carry."""
    rng = np.random.default_rng(seed)
    framer = modem.Framer()
    codec = fec.FrameCodec(M.MODES[kind])
    audio, sent = [framer.superframe(M.TRAIN), framer.superframe(M.TRAIN)], []
    for _ in range(superframes):
        msgs = [rng.bytes(M.MODES[kind].msg_bytes) for _ in range(M.MODES[kind].frames)]
        audio.append(framer.superframe(kind, [codec.encode(m) for m in msgs]))
        sent.append((kind, msgs))
    return np.concatenate(audio), sent


def one(job):
    name, letter, seed = job
    legs, _ = PATHS[name]
    kind = KINDS[letter]
    x, sent = make(kind, seed)
    path = CS.Path(name, legs, clock_ppm=(25.0, -35.0)[seed % 2], seed=seed)
    y = CS.run(path, np.concatenate([np.zeros(2345), x, np.zeros(8000)]))
    sink = Sink(sent)
    rx = modem.Receiver(sink)
    for i in range(0, len(y), 1777):
        rx.push(y[i:i + 1777])
    want = [(kind, m) for _, msgs in sent[:-1] for m in msgs]         # the last burst may be cut off
    return name, letter, seed, sum(w in sink.got for w in want), len(want), dict(rx.stats)


def main():
    pick = sys.argv[1] if len(sys.argv) > 1 else ""
    seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    jobs = [(name, letter, seed) for name, (_, letters) in PATHS.items() if pick in name
            for letter in letters for seed in range(1, seeds + 1)]
    with ProcessPoolExecutor(7) as pool:
        rows = list(pool.map(one, jobs))
    print("%-42s %-6s %8s %7s %6s %6s %6s %8s" % ("path", "mode", "frames", "worst", "lost", "relock", "jumps", "repaired"))
    total_ok = total = 0
    for name, (_, letters) in PATHS.items():
        for letter in letters:
            rs = [r for r in rows if r[0] == name and r[1] == letter]
            if not rs:
                continue
            ok, n = sum(r[3] for r in rs), sum(r[4] for r in rs)
            total_ok, total = total_ok + ok, total + n
            stat = {k: sum(r[5][k] for r in rs) for k in rs[0][5]}
            print("%-42s %-6s %7.1f%% %6.1f%% %6d %6d %6d %8d" % (
                name, M.MODES[KINDS[letter]].name, 100.0 * ok / n, 100.0 * min(r[3] / r[4] for r in rs),
                stat["lost"], stat["relocks"], stat["jumps"], stat["repaired"]))
    print("%-42s %-6s %7.2f%%" % ("all", "", 100.0 * total_ok / max(total, 1)))


if __name__ == "__main__":
    main()
