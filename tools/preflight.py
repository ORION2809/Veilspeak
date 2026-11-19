"""Pre-flight check to run BEFORE you test on real phones.

It needs no phones and no sound hardware. It confirms the whole software stack is healthy and
ready, so that when two phones are available the only unknowns left are the physical ones (the
phone's own audio processing, the injection point, real-time timing). It:

  1. confirms the 3GPP reference codecs are built and round-trip audio;
  2. runs complete simulated two-way calls through representative networks and checks that keys
     agree, the SAS matches, no wrong frame is accepted, and speech is delivered;
  3. runs the real-time, block-based wrapper (the code path live.py uses) back-to-back and
     checks it secures and delivers speech;
  4. reports whether sound devices are visible for the hardware step.

    python tools/preflight.py              # the standard check (about a minute)
    python tools/preflight.py --quick      # skip the slower VoLTE/EVS call

A GO here does NOT mean it will work on real phones -- that is what the phones are for. It means
the software is not the thing in the way. Start the real-phone steps (docs/REAL_PHONE_TESTING.md)
only once this says GO.
"""
import argparse
import os
import sys
import time

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np                                      # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from veilspeak import modes as M                        # noqa: E402
from veilspeak.cellsim import call as CS                # noqa: E402
from veilspeak.cellsim import duplex, speech            # noqa: E402

FS = 8000
PASS, FAIL, WARN = "  OK ", "FAIL ", "WARN "


class Checker:
    def __init__(self):
        self.failed = 0
        self.warned = 0

    def ok(self, name, detail=""):
        print("  [%s] %s%s" % (PASS, name, "  -- " + detail if detail else ""))

    def fail(self, name, detail=""):
        self.failed += 1
        print("  [%s] %s%s" % (FAIL, name, "  -- " + detail if detail else ""))

    def warn(self, name, detail=""):
        self.warned += 1
        print("  [%s] %s%s" % (WARN, name, "  -- " + detail if detail else ""))


def check_codecs(c):
    print("\n1. Reference codecs (third_party/bin)")
    tone = 0.2 * np.sin(2 * np.pi * 440 * np.arange(FS) / FS)      # 1 s, 8 kHz
    from veilspeak.cellsim import codecs as CO
    from scipy.signal import resample_poly
    probes = [
        ("AMR-NB 12.2 (2G/3G)", lambda: CO.amrnb_decode(CO.amrnb_encode(tone, 12200))),
        ("AMR-WB 12.65 (VoLTE)", lambda: CO.amrwb_decode(CO.amrwb_encode(resample_poly(tone, 2, 1), 12650))),
        ("EVS 13.2 (VoLTE)", lambda: CO.evs_decode(CO.evs_encode(resample_poly(tone, 2, 1), 16000, 13200), 16000)),
    ]
    for name, run in probes:
        try:
            out = run()
            lvl = float(np.sqrt(np.mean(np.asarray(out) ** 2)))
            if lvl > 1e-4:
                c.ok(name, "round-trips, output RMS %.3f" % lvl)
            else:
                c.fail(name, "round-trips but output is silent")
        except Exception as exc:                                    # noqa: BLE001
            c.fail(name, "%s -- run tools/build_codecs.sh" % str(exc).splitlines()[0][:80])


def check_sim_call(c, name, path_ab, path_ba, seconds, min_delivery):
    said_a = speech.voice("a", seconds + 2)
    said_b = speech.voice("b", seconds + 2)
    t0 = time.time()
    rep, a, b = duplex.simulate(path_ab, path_ba, said_a, said_b, seconds, start_s=1.5)
    dt = time.time() - t0
    if not rep["secure"]:
        c.fail(name, "key agreement did not complete")
        return
    if not rep["sas_match"]:
        c.fail(name, "SAS mismatch (A=%s B=%s) -- a man-in-the-middle would look like this" % (a.hs.sas, b.hs.sas))
        return
    ab, ba = rep["a_to_b"], rep["b_to_a"]
    wrong = ab["wrong"] + ba["wrong"]
    worst = min(ab["delivered_share"], ba["delivered_share"])
    detail = "secure %.1fs, SAS %s, delivery %.0f%%/%0.f%%, wrong frames %d (%.0fs sim in %.0fs)" % (
        rep["time_to_secure_s"], a.hs.sas, 100 * ab["delivered_share"], 100 * ba["delivered_share"], wrong, seconds, dt)
    if wrong > 0:
        c.fail(name, "accepted %d wrong frame(s) -- integrity check regression" % wrong)
    elif worst < min_delivery:
        c.warn(name, "delivery %.0f%% below %.0f%% -- %s" % (100 * worst, 100 * min_delivery, detail))
    else:
        c.ok(name, detail)


def check_live_loopback(c, seconds=15.0):
    from veilspeak.live import loopback_test
    t0 = time.time()
    a, b = loopback_test(seconds, verbose=False)
    dt = time.time() - t0
    sa, sb = a.state, b.state
    if not (a.ep.secure_at is not None and b.ep.secure_at is not None):
        c.fail("real-time wrapper (live.py path)", "did not secure in loopback")
        return
    if a.ep.hs.sas != b.ep.hs.sas:
        c.fail("real-time wrapper (live.py path)", "SAS mismatch in loopback")
        return
    heard = min(sa["frames_heard"], sb["frames_heard"])
    if heard < 50:
        c.warn("real-time wrapper (live.py path)", "secured but only %d frames heard" % heard)
    else:
        c.ok("real-time wrapper (live.py path)",
             "secured, SAS %s, %d/%d frames heard each way (%.0fs loop in %.0fs)" % (
                 a.ep.hs.sas, sa["frames_heard"], sb["frames_heard"], seconds, dt))


def check_sound_devices(c):
    print("\n4. Sound hardware (for the real-phone step)")
    try:
        import sounddevice as sd
    except Exception:                                               # noqa: BLE001
        c.warn("sounddevice", "not installed -- `pip install sounddevice` before a live two-way call")
        return
    try:
        devs = sd.query_devices()
        ins = [d for d in devs if d["max_input_channels"] > 0]
        outs = [d for d in devs if d["max_output_channels"] > 0]
        if ins and outs:
            c.ok("sounddevice", "%d input, %d output device(s) visible -- `python -m veilspeak.live --list` to choose" % (len(ins), len(outs)))
        else:
            c.warn("sounddevice", "installed but %d input / %d output devices" % (len(ins), len(outs)))
    except Exception as exc:                                        # noqa: BLE001
        c.warn("sounddevice", "query failed: %s" % str(exc).splitlines()[0][:80])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="skip the slower VoLTE/EVS simulated call")
    args = ap.parse_args()

    print("Veilspeak pre-flight -- software readiness for a real-phone test")
    print("=" * 70)
    c = Checker()

    check_codecs(c)

    print("\n2. Complete simulated calls")
    g711 = CS.Leg("g711")
    twoG = CS.Path("2g", (CS.Leg("amrnb", 12200),), clock_ppm=25.0)
    twoG2 = CS.Path("2g", (CS.Leg("amrnb", 12200),), clock_ppm=-35.0)
    check_sim_call(c, "2G AMR 12.2, clean", twoG.with_seed(11), twoG2.with_seed(12), 18.0, 0.95)
    if not args.quick:
        evs = CS.Path("evs", (CS.Leg("evs", 13200, jitter_ms=10, loss=0.01),), clock_ppm=25.0)
        evs2 = CS.Path("evs", (CS.Leg("evs", 13200, jitter_ms=10, loss=0.01),), clock_ppm=-35.0)
        check_sim_call(c, "VoLTE EVS 13.2, jitter + 1% loss", evs.with_seed(21), evs2.with_seed(22), 18.0, 0.95)

    print("\n3. Real-time pipeline (back-to-back, no hardware)")
    check_live_loopback(c)

    check_sound_devices(c)

    print("\n" + "=" * 70)
    if c.failed:
        print("NO-GO: %d check(s) failed. Fix these before testing on phones." % c.failed)
        sys.exit(1)
    elif c.warned:
        print("GO (with %d warning(s)). The software is ready; read the warnings above." % c.warned)
        print("Next: docs/REAL_PHONE_TESTING.md")
    else:
        print("GO. The software stack is healthy. The real phones are the only unknown left.")
        print("Next: docs/REAL_PHONE_TESTING.md")


if __name__ == "__main__":
    main()
