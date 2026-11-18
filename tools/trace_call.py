"""Superframe-by-superframe trace of one simulated call, for finding out why speech was lost.

    python tools/trace_call.py "<scenario>" [seed] [seconds] [start_s]

One line per 480 ms. For each direction: what was sent (T train, C control, F fast, R robust,
I idle, p plain speech), what the receiver made of it (type seen / type accepted, + or x per
FEC frame), its signal-to-distortion estimate, the mode it asks for, and its running counts
of lost windows / timing jumps / repaired bursts / relocks.
"""
import os
import sys

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np                                               # noqa: E402

sys.path.insert(0, ".")
from veilspeak import endpoint as E, modem, modes as M           # noqa: E402
from veilspeak.cellsim import duplex, speech                     # noqa: E402
from veilspeak.cellsim.scenarios import SCENARIOS                # noqa: E402

KN = {M.TRAIN: "T", M.FAST: "F", M.ROBUST: "R", M.IDLE: "I", M.CONTROL: "C", None: "-"}


def install():
    plain_superframe = E.Endpoint.superframe
    plain_next = E.Endpoint.next_superframe
    plain_framer = modem.Framer.superframe

    def superframe(self, kind, when, info):
        plain_superframe(self, kind, when, info)
        self.__dict__.setdefault("events", []).append(dict(
            when=when, kind=KN[kind], seen=KN[info.get("kind_seen")], sdr=info.get("sdr_db"),
            frames="".join("+" if ok else "x" for ok, _ in info.get("frames", ())),
            want="LIRF"[self.want], stats=dict(self.rx.stats)))

    def framer(self, kind, frames=None):
        self.last_kind = KN[kind]
        return plain_framer(self, kind, frames)

    def next_superframe(self):
        self.framer.last_kind = "p"
        audio = plain_next(self)
        self.__dict__.setdefault("sent", []).append(self.framer.last_kind)
        return audio

    E.Endpoint.superframe, E.Endpoint.next_superframe, modem.Framer.superframe = superframe, next_superframe, framer


def main():
    name = [n for n in SCENARIOS if sys.argv[1].lower() in n.lower()][0]
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 45.0
    start_s = float(sys.argv[4]) if len(sys.argv) > 4 else 2.4
    install()
    path_ab, path_ba = SCENARIOS[name]
    said = [speech.voice(w, seconds + 2, 7.0 * seed) for w in "ab"]
    rep, a, b = duplex.simulate(path_ab.with_seed(10 * seed + 1), path_ba.with_seed(10 * seed + 2),
                                said[0], said[1], seconds, start_s=start_s)
    print(name, "seed", seed)
    print({k: v for k, v in rep.items() if k not in ("line_ab", "legs", "a_to_b", "b_to_a")})
    for key in ("a_to_b", "b_to_a"):
        if key in rep:
            print(key, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in rep[key].items()})
    print("A log", a.log)
    print("B log", b.log)

    def by_slot(ep):
        out = {}
        for e in getattr(ep, "events", []):
            out.setdefault((e["when"] - M.PAUSE_SAMPLES) // M.SF_SAMPLES, []).append(e)
        return out

    heard_by = {"a": by_slot(a), "b": by_slot(b)}
    print("sf   t(s) | A>  B: seen/ok frames   sdr want lost/jump/rep/relock | B>  A: seen/ok frames   sdr want lost/jump/rep/relock")
    for n in range(len(a.sent)):
        cells = []
        for tx, events in ((a, heard_by["b"]), (b, heard_by["a"])):
            evs = events.get(n, [])
            if evs:
                e = evs[-1]
                st = e["stats"]
                cell = "%s   %s/%s %-3s %5s %s %d/%d/%d/%d%s" % (
                    tx.sent[n], e["seen"], e["kind"], e["frames"], "%.1f" % e["sdr"] if e["sdr"] is not None else "  -",
                    e["want"], st["lost"], st["jumps"], st["repaired"], st["relocks"], " x%d" % len(evs) if len(evs) > 1 else "")
            else:
                cell = "%s   (no window)" % tx.sent[n]
            cells.append("%-38s" % cell)
        print("%3d %5.1f | %s | %s" % (n, n * 0.48, cells[0], cells[1]))


if __name__ == "__main__":
    main()
