"""Measure the added one-way delay of the Veilspeak system.

    python tools/clicktest.py                        # clean loopback, no codec
    python tools/clicktest.py --codec amrnb --rate 12200
    python tools/clicktest.py --codec evs --rate 13200 --clicks 10

Runs a simulated two-way call between two endpoints, injects short click
impulses into one side's speech at known times, and measures when each click
appears in the decoded speech on the other side via cross-correlation.

Reports: per-click delay, mean, std, and a breakdown of where the delay comes
from (vocoder framing, FEC frame assembly, modem superframe structure).
"""
import argparse
import os
import sys

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np                                      # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from veilspeak import modes as M                        # noqa: E402
from veilspeak.cellsim import call as CS                # noqa: E402
from veilspeak.cellsim import duplex                    # noqa: E402
from veilspeak.endpoint import FRAMES_PER_SF            # noqa: E402
from veilspeak.vocoder import FRAME_SAMPLES             # noqa: E402

FS = 8000


def make_click_speech(total_samples, click_positions, click_width=4):
    """Speech array with short clicks (bipolar pulses) at the given sample positions."""
    speech = np.zeros(total_samples, dtype=np.int16)
    for pos in click_positions:
        for i in range(click_width):
            if pos + i < total_samples:
                speech[pos + i] = 20000 * (1 if i % 2 == 0 else -1)
    return speech


def find_click(decoded, reference_click, search_start, search_end, click_width=4):
    """Find the position of a click in decoded audio via energy detection.

    Returns the sample position of the click peak, or -1 if not found."""
    if search_end > len(decoded):
        search_end = len(decoded)
    if search_start < 0:
        search_start = 0
    if search_end <= search_start:
        return -1

    segment = decoded[search_start:search_end].astype(float)
    if len(segment) < FRAME_SAMPLES:
        return -1

    frame = FRAME_SAMPLES
    energy = np.array([
        np.sum(segment[i:i + frame] ** 2)
        for i in range(0, len(segment) - frame + 1, frame // 4)
    ])

    if len(energy) == 0 or energy.max() < 10 * energy.mean():
        return -1

    peak_idx = int(np.argmax(energy)) * (frame // 4) + search_start

    lo = max(peak_idx - frame, search_start)
    hi = min(peak_idx + frame, search_end)
    fine = decoded[lo:hi].astype(float)
    if len(fine) == 0:
        return peak_idx
    return lo + int(np.argmax(np.abs(fine)))


def run_clicktest(codec=None, rate=None, clicks=5, seconds=None, seed=1):
    """Run a call with clicks and measure delays. Returns list of delay_ms per click."""
    setup_sf = 25
    sfs_per_click = 6
    if seconds is None:
        seconds = (setup_sf + clicks * sfs_per_click + 5) * M.SF_SAMPLES / FS
    total = int((seconds + 2) * FS)

    click_start_sf = setup_sf
    click_positions = []
    for i in range(clicks):
        sf = click_start_sf + i * sfs_per_click
        sample = sf * M.SF_SAMPLES
        if sample + FRAME_SAMPLES < total:
            click_positions.append(sample)

    speech_a = make_click_speech(total, click_positions)
    speech_b = np.zeros(total, dtype=np.int16)

    if codec is not None:
        leg = CS.Leg(codec, rate or 12200)
        path = CS.Path("clicktest", (leg,), clock_ppm=0.0, seed=seed)
    else:
        path = CS.Path("clean", ())

    rep, a, b = duplex.simulate(path, path, speech_a, speech_b, seconds, start_s=0.0)

    if not rep["secure"]:
        print("Key agreement did not complete — cannot measure delay.")
        return []

    secure_sf = a.secure_at
    decoded = duplex.heard_audio(b, a)

    delays = []
    for i, click_pos in enumerate(click_positions):
        click_sf = click_pos // M.SF_SAMPLES
        if click_sf < secure_sf:
            continue

        relative_sf = click_sf - secure_sf
        min_delay_samples = int(0.2 * FS)
        max_delay_samples = int(3.0 * FS)
        expected_out = relative_sf * M.SF_SAMPLES

        search_lo = max(expected_out + min_delay_samples - FRAMES_PER_SF * FRAME_SAMPLES, 0)
        search_hi = min(expected_out + max_delay_samples, len(decoded))

        found = find_click(decoded, None, search_lo, search_hi)
        if found < 0:
            continue

        click_in_stream = click_pos
        click_out_stream = found + secure_sf * M.SF_SAMPLES + FRAMES_PER_SF * FRAME_SAMPLES

        delay_samples = click_out_stream - click_in_stream
        delay_ms = delay_samples / FS * 1000
        if 100 < delay_ms < 5000:
            delays.append(delay_ms)

    return delays


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--codec", choices=["amrnb", "amrwb", "evs"], default=None,
                    help="codec to simulate (default: clean, no codec)")
    ap.add_argument("--rate", type=int, default=None, help="codec bit rate")
    ap.add_argument("--clicks", type=int, default=5, help="number of clicks to inject")
    ap.add_argument("--seconds", type=float, default=None, help="call duration")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    codec_label = args.codec or "clean (no codec)"
    rate_label = "%d" % args.rate if args.rate else "default"
    print("Click-test: codec=%s  rate=%s  clicks=%d" % (codec_label, rate_label, args.clicks))

    delays = run_clicktest(args.codec, args.rate, args.clicks, args.seconds, args.seed)

    if not delays:
        print("No clicks detected in the decoded speech.")
        print("The call may not have been long enough, or the codec may be too destructive.")
        return

    print("\nResults:")
    print("  Clicks detected: %d / %d" % (len(delays), args.clicks))
    for i, d in enumerate(delays):
        print("  Click %d: %.0f ms" % (i + 1, d))
    print()
    print("  Mean delay:  %.0f ms" % np.mean(delays))
    if len(delays) > 1:
        print("  Std delay:   %.0f ms" % np.std(delays))
    print("  Min delay:   %.0f ms" % np.min(delays))
    print("  Max delay:   %.0f ms" % np.max(delays))

    print("\nDelay breakdown (design values):")
    print("  Vocoder frame:        40 ms (Codec2 operates on 40 ms frames)")
    print("  FEC frame assembly:   varies by mode")
    print("    FAST:  2 frames of 6 vocoder frames = 240 ms per FEC half")
    print("    ROBUST: 1 frame of 12 vocoder frames = 480 ms")
    print("  Superframe + pause:   480 ms (38 data symbols + 2 header + 8 pause)")
    print("  Speech lead:          %d superframes = %d ms" % (
        4, 4 * M.SF_SAMPLES * 1000 // FS))
    print("  Expected one-way:     ~450 ms (FAST) / ~640 ms (ROBUST)")
    print("  Measured mean:        %.0f ms" % np.mean(delays))


if __name__ == "__main__":
    main()
