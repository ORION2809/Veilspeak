"""Simulates one complete secure call and writes the audio.

    python -m veilspeak                       list the scenarios
    python -m veilspeak "2G full rate, good signal"
    python -m veilspeak "VoLTE EVS 13.2, jitter buffer, 1% loss" --seconds 60 --out demo_out/evs

Two people (A and B) each have a unit wired to their phone. The call starts as an ordinary
one. After a few seconds A switches the unit on; B's unit answers by itself, the two agree
keys, and from then on both people's speech crosses the simulated phone network encrypted.

Files written:
    a_says.wav, b_says.wav       what each person said
    line_a_to_b.wav              what the phone network carried from A to B (as B's unit hears it):
                                 plain speech at first, then the modem signal
    b_hears.wav, a_hears.wav     the speech each person heard after decryption
"""
import argparse
import os

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np                                      # noqa: E402
import soundfile as sf                                  # noqa: E402

from .cellsim import duplex, speech                     # noqa: E402
from .cellsim.scenarios import SCENARIOS, SIDETONE      # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("scenario", nargs="?", help="scenario name (or a unique part of it)")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--start", type=float, default=2.9, help="seconds of ordinary call before A switches on")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="demo_out")
    ap.add_argument("--speech-a", help="WAV file for what A says (default: a recorded test voice)")
    ap.add_argument("--speech-b", help="WAV file for what B says")
    args = ap.parse_args()
    if not args.scenario:
        print("Scenarios:")
        for name in SCENARIOS:
            print("  " + name)
        return
    matches = [n for n in SCENARIOS if args.scenario.lower() in n.lower()]
    if len(matches) != 1:
        raise SystemExit("scenario %r matches %d scenarios; run without arguments to list them" % (args.scenario, len(matches)))
    name = matches[0]
    path_ab, path_ba = (p.with_seed(10 * args.seed + i) for i, p in enumerate(SCENARIOS[name], 1))
    need = int((args.seconds + 2) * 8000)
    said_a = np.resize(speech.load(args.speech_a), need) if args.speech_a else speech.voice("a", args.seconds + 2)
    said_b = np.resize(speech.load(args.speech_b), need) if args.speech_b else speech.voice("b", args.seconds + 2)

    print("Scenario: %s (%.0f s; A switches on after %.1f s)" % (name, args.seconds, args.start))
    rep, a, b = duplex.simulate(path_ab, path_ba, said_a, said_b, args.seconds, start_s=args.start,
                                sidetone_db=SIDETONE.get(name))
    if not rep["secure"]:
        print("Key agreement did not complete.")
    else:
        print("Keys agreed %.1f s after A switched on. Code shown to A: %s   to B: %s   -> %s" % (
            rep["time_to_secure_s"], a.hs.sas, b.hs.sas, "MATCH" if rep["sas_match"] else "MISMATCH (attack)"))
        for label, key in (("A to B", "a_to_b"), ("B to A", "b_to_a")):
            d = rep[key]
            print("  %s: %.1f%% of speech delivered bit-exact (%.0f%% at 1200 bit/s, %.0f%% at 700 bit/s); "
                  "longest gap %.2f s; wrong frames accepted: %d" % (
                      label, 100 * d["delivered_share"], 100 * d["fast_share"], 100 * d["robust_share"],
                      d["longest_gap_s"], d["wrong"]))
            rx = d["receiver"]
            print("          receiver: %d bursts read, %d windows empty, %d time shifts followed, %d bursts repaired; "
                  "signal-to-distortion %.1f dB" % (rx["superframes"], rx["lost"], rx["jumps"], rx["repaired"], d["sdr_db"] or 0))
    os.makedirs(args.out, exist_ok=True)
    sf.write(os.path.join(args.out, "a_says.wav"), said_a, 8000, subtype="PCM_16")
    sf.write(os.path.join(args.out, "b_says.wav"), said_b, 8000, subtype="PCM_16")
    if rep["secure"]:
        sf.write(os.path.join(args.out, "b_hears.wav"), duplex.heard_audio(b, a), 8000, subtype="PCM_16")
        sf.write(os.path.join(args.out, "a_hears.wav"), duplex.heard_audio(a, b), 8000, subtype="PCM_16")
    if rep.get("line_ab") is not None:
        sf.write(os.path.join(args.out, "line_a_to_b.wav"), rep["line_ab"], 8000, subtype="PCM_16")
    print("Audio written to %s/" % args.out)


if __name__ == "__main__":
    main()
