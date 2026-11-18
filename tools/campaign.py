"""Simulated-call campaign: many complete two-way calls per scenario.

Each call starts as an ordinary one with both people talking. After a few seconds A's unit is
switched on; B's unit answers by itself, the two agree keys, and speech then flows both ways
through the scenario's phone paths. Results go to a CSV, one row per call.

    python tools/campaign.py out.csv [calls per scenario] [seconds per call] ["name filter|another filter"] [--fixed]
    python tools/campaign.py --summary out.csv

--fixed runs the calls through the fixed-point reference codecs (the bit-exact ones phones are
tested against; about seven times slower) instead of the floating-point ones.
"""
import csv
import os
import sys
import time
from dataclasses import replace

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")          # many small matrices: threads only get in each other's way

from concurrent.futures import ProcessPoolExecutor, as_completed    # noqa: E402

import numpy as np                                      # noqa: E402

sys.path.insert(0, ".")
from veilspeak.cellsim import duplex, speech            # noqa: E402
from veilspeak.cellsim.call import Handover             # noqa: E402
from veilspeak.cellsim.scenarios import SCENARIOS, SIDETONE       # noqa: E402

PER_DIRECTION = ("delivered", "fast", "robust", "longest_gap_s", "wrong", "sdr_db", "lost", "relocks", "jumps", "repaired")
FIELDS = ["scenario", "seed", "start_s", "secure", "sas_match", "time_to_secure_s", "wall_s"] + [
    "%s_%s" % (d, k) for d in ("ab", "ba") for k in PER_DIRECTION]


def fixed_point(path):
    """The same path with every codec leg switched to the fixed-point codec."""
    def leg(item):
        if isinstance(item, Handover):
            return replace(item, before=tuple(leg(x) for x in item.before), after=tuple(leg(x) for x in item.after))
        return replace(item, fixed=item.codec in ("amrnb", "amrwb", "evs"))
    return replace(path, legs=tuple(leg(x) for x in path.legs))


def one(job):
    name, seed, seconds, fixed = job
    path_ab, path_ba = SCENARIOS[name]
    if fixed:
        path_ab, path_ba = fixed_point(path_ab), fixed_point(path_ba)
    start_s = 1.44 + 0.48 * (seed % 7)                  # A talks for 1.4 to 4.3 s before switching on
    said = [speech.voice(who, seconds + 2, 5.3 * seed + 2.1 * i) for i, who in enumerate("ab")]
    began = time.time()
    rep, a, b = duplex.simulate(path_ab.with_seed(10 * seed + 1), path_ba.with_seed(10 * seed + 2),
                                said[0], said[1], seconds, start_s=start_s, sidetone_db=SIDETONE.get(name))
    row = {"scenario": name, "seed": seed, "start_s": start_s, "secure": int(rep["secure"]),
           "sas_match": int(rep["sas_match"]), "time_to_secure_s": rep["time_to_secure_s"],
           "wall_s": round(time.time() - began)}
    for tag, key in (("ab", "a_to_b"), ("ba", "b_to_a")):
        d = rep.get(key)
        if d:
            rx = d["receiver"]
            row.update({tag + "_delivered": round(d["delivered_share"], 4), tag + "_fast": round(d["fast_share"], 4),
                        tag + "_robust": round(d["robust_share"], 4), tag + "_longest_gap_s": round(d["longest_gap_s"], 2),
                        tag + "_wrong": d["wrong"], tag + "_sdr_db": None if d["sdr_db"] is None else round(d["sdr_db"], 1),
                        tag + "_lost": rx["lost"], tag + "_relocks": rx["relocks"], tag + "_jumps": rx["jumps"],
                        tag + "_repaired": rx["repaired"]})
    return row


def summarise(rows):
    names = [n for n in SCENARIOS if any(r["scenario"] == n for r in rows)]
    print("%-64s %5s %7s %9s %8s %7s %9s %6s %6s" % (
        "scenario", "calls", "secure", "setup s", "speech", "worst", "longest", "fast", "wrong"))
    total = {"calls": 0, "secure": 0, "wrong": 0}
    for name in names:
        rs = [r for r in rows if r["scenario"] == name]
        sec = [r for r in rs if int(r["secure"]) and int(r["sas_match"])]

        def col(key, cast=float):
            return [cast(r[d + key]) for r in sec for d in ("ab_", "ba_") if r.get(d + key) not in (None, "")]

        deliv, fast, gaps, wrong = col("delivered"), col("fast"), col("longest_gap_s"), sum(col("wrong", int))
        setup = [float(r["time_to_secure_s"]) for r in sec]
        total["calls"] += len(rs)
        total["secure"] += len(sec)
        total["wrong"] += wrong
        print("%-64s %5d %4d/%-2d %9s %7s%% %6s%% %8s s %5s%% %6d" % (
            name[:64], len(rs), len(sec), len(rs),
            "%.1f-%.1f" % (min(setup), max(setup)) if setup else "-",
            "%.1f" % (100 * np.mean(deliv)) if deliv else "-",
            "%.1f" % (100 * np.min(deliv)) if deliv else "-",
            "%.1f" % max(gaps) if gaps else "-",
            "%.0f" % (100 * np.mean(fast)) if fast else "-", wrong))
    print("%d calls, %d with keys agreed and matching codes, %d wrong frames accepted" % (
        total["calls"], total["secure"], total["wrong"]))


if __name__ == "__main__":
    if sys.argv[1] == "--summary":
        summarise(list(csv.DictReader(open(sys.argv[2]))))
        sys.exit()
    fixed = "--fixed" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--fixed"]
    out = args[0]
    calls = int(args[1]) if len(args) > 1 else 6
    seconds = float(args[2]) if len(args) > 2 else 45.0
    picks = (args[3] if len(args) > 3 else "").lower().split("|")     # several name filters, separated by |
    jobs = [(name, seed, seconds, fixed) for seed in range(calls) for name in SCENARIOS
            if any(p in name.lower() for p in picks)]
    rows = []
    with ProcessPoolExecutor(7) as pool, open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for fut in as_completed([pool.submit(one, j) for j in jobs]):
            row = fut.result()
            rows.append(row)
            writer.writerow(row)
            f.flush()
    summarise(rows)
