"""Speech quality measurement: PESQ, STOI, and Whisper WER of delivered speech.

Measures modem/channel degradation in isolation by comparing delivered speech against a
Codec2-only reference (encode then decode, no modem, no channel). That way the vocoder's
own quality cancels out.

    python tools/speech_quality.py                              # default scenario
    python tools/speech_quality.py --scenario "2G full rate"    # specific scenario
    python tools/speech_quality.py --no-whisper                 # skip Whisper WER (slow)
    python tools/speech_quality.py --input ref.wav deg.wav      # compare two WAV files
    python tools/speech_quality.py --list                       # list available scenarios
"""
import argparse
import os
import sys

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np                                              # noqa: E402
import soundfile as sf                                          # noqa: E402
from scipy.signal import fftconvolve, resample_poly             # noqa: E402

sys.path.insert(0, ".")
from veilspeak.cellsim import duplex, speech                    # noqa: E402
from veilspeak.cellsim.scenarios import SCENARIOS, SIDETONE     # noqa: E402
from veilspeak.vocoder import FRAME_SAMPLES, Vocoder            # noqa: E402

FS = 8000


# ---------------------------------------------------------------------------
# Codec2-only reference: encode then decode, no modem, no channel
# ---------------------------------------------------------------------------
def codec2_reference(pcm, rate=1200):
    """Round-trip through Codec2 at the given rate. Returns int16 at 8 kHz."""
    pcm = np.asarray(pcm, dtype=np.int16)
    n = (len(pcm) // FRAME_SAMPLES) * FRAME_SAMPLES
    pcm = pcm[:n]
    voc = Vocoder(rate)
    packed = voc.encode(pcm)
    frames = voc.split(packed, n // FRAME_SAMPLES)
    dec = Vocoder(rate)
    out = np.concatenate([dec.decode(f) for f in frames])
    return out


# ---------------------------------------------------------------------------
# Alignment: cross-correlate to find the delay between two signals
# ---------------------------------------------------------------------------
def align(ref, deg, max_shift=None):
    """Align deg to ref by cross-correlation. Returns (ref_trimmed, deg_trimmed)
    of the same length, with the delay removed."""
    ref_f = ref.astype(np.float64)
    deg_f = deg.astype(np.float64)
    if max_shift is None:
        max_shift = min(len(ref_f), len(deg_f)) // 2
    cc = fftconvolve(ref_f, deg_f[::-1], mode="full")
    mid = len(deg_f) - 1
    lo = max(mid - max_shift, 0)
    hi = min(mid + max_shift + 1, len(cc))
    best = int(np.argmax(np.abs(cc[lo:hi]))) + lo
    lag = best - mid
    if lag >= 0:
        r, d = ref_f[lag:], deg_f[:len(ref_f) - lag]
    else:
        r, d = ref_f[:len(ref_f) + lag], deg_f[-lag:]
    n = min(len(r), len(d))
    return r[:n], d[:n]


# ---------------------------------------------------------------------------
# WER by edit distance (no external dependency)
# ---------------------------------------------------------------------------
def _edit_distance(ref_words, hyp_words):
    n, m = len(ref_words), len(hyp_words)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, m + 1):
            cost = 0 if ref_words[i - 1] == hyp_words[j - 1] else 1
            ins, delete, sub = dp[j] + 1, dp[j - 1] + 1, prev + cost
            prev, dp[j] = dp[j], min(ins, delete, sub)
    return dp[m]


def word_error_rate(ref_text, hyp_text):
    ref_words = ref_text.lower().split()
    hyp_words = hyp_text.lower().split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return _edit_distance(ref_words, hyp_words) / len(ref_words)


# ---------------------------------------------------------------------------
# Metric wrappers (gracefully skip if not installed)
# ---------------------------------------------------------------------------
def measure_pesq(ref, deg, mode="nb"):
    try:
        from pesq import pesq as pesq_fn
    except ImportError:
        return None, "pesq not installed (pip install pesq)"
    rate = 8000 if mode == "nb" else 16000
    r, d = ref.copy(), deg.copy()
    if mode == "wb":
        r = resample_poly(r, 2, 1).astype(np.float64)
        d = resample_poly(d, 2, 1).astype(np.float64)
    r /= max(np.abs(r).max(), 1e-6)
    d /= max(np.abs(d).max(), 1e-6)
    try:
        score = pesq_fn(rate, r, d, mode)
        return score, None
    except Exception as e:
        return None, str(e)


def measure_stoi(ref, deg):
    try:
        from pystoi import stoi as stoi_fn
    except ImportError:
        return None, "pystoi not installed (pip install pystoi)"
    r = ref / max(np.abs(ref).max(), 1e-6)
    d = deg / max(np.abs(deg).max(), 1e-6)
    try:
        score = stoi_fn(r, d, FS, extended=False)
        return score, None
    except Exception as e:
        return None, str(e)


def measure_whisper(ref_audio, deg_audio, model_name="base"):
    try:
        import whisper
    except ImportError:
        return None, None, None, "openai-whisper not installed (pip install openai-whisper)"
    import tempfile
    model = whisper.load_model(model_name)

    def transcribe(pcm):
        fp = os.path.join(tempfile.mkdtemp(), "audio.wav")
        sf.write(fp, pcm, FS, subtype="PCM_16")
        result = model.transcribe(fp, language="en")
        os.unlink(fp)
        return result["text"].strip()

    ref_text = transcribe(ref_audio.astype(np.int16))
    deg_text = transcribe(deg_audio.astype(np.int16))
    wer = word_error_rate(ref_text, deg_text)
    return ref_text, deg_text, wer, None


# ---------------------------------------------------------------------------
# Run a simulated call and extract audio
# ---------------------------------------------------------------------------
def run_call(scenario_name, seconds=30.0, start_s=2.9, seed=1):
    matches = [n for n in SCENARIOS if scenario_name.lower() in n.lower()]
    if len(matches) != 1:
        raise SystemExit("scenario %r matches %d scenarios; use --list to see them"
                         % (scenario_name, len(matches)))
    name = matches[0]
    path_ab, path_ba = (p.with_seed(10 * seed + i) for i, p in enumerate(SCENARIOS[name], 1))
    need = int((seconds + 2) * FS)
    said_a = speech.voice("a", seconds + 2)[:need]
    said_b = speech.voice("b", seconds + 2)[:need]

    rep, a, b = duplex.simulate(path_ab, path_ba, said_a, said_b, seconds,
                                start_s=start_s, sidetone_db=SIDETONE.get(name))
    if not rep["secure"]:
        raise SystemExit("key agreement did not complete for scenario %r" % name)

    degraded = duplex.heard_audio(b, a)
    original = said_a

    info = {
        "scenario": name,
        "seconds": seconds,
        "delivery": rep["a_to_b"]["delivered_share"],
        "fast_share": rep["a_to_b"]["fast_share"],
        "robust_share": rep["a_to_b"]["robust_share"],
        "sdr_db": rep["a_to_b"]["sdr_db"],
    }
    return original, degraded, info


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--scenario", default="2G full rate, good signal",
                    help="scenario name (or unique substring)")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--input", nargs=2, metavar=("REF", "DEG"),
                    help="compare two WAV files directly instead of running a call")
    ap.add_argument("--no-whisper", action="store_true",
                    help="skip Whisper WER measurement (slow)")
    ap.add_argument("--whisper-model", default="base",
                    help="Whisper model size (tiny/base/small/medium/large)")
    ap.add_argument("--codec2-rate", type=int, default=1200, choices=[700, 1200],
                    help="Codec2 rate for the reference")
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    ap.add_argument("--out", help="write aligned ref/deg WAVs to this directory")
    args = ap.parse_args()

    if args.list:
        for name in SCENARIOS:
            print("  " + name)
        return

    if args.input:
        ref_pcm = speech.load(args.input[0]).astype(np.float64)
        deg_pcm = speech.load(args.input[1]).astype(np.float64)
        info = {"scenario": "(files: %s vs %s)" % tuple(args.input),
                "seconds": len(deg_pcm) / FS, "delivery": None,
                "fast_share": None, "robust_share": None, "sdr_db": None}
        ref_aligned, deg_aligned = align(ref_pcm, deg_pcm)
    else:
        print("Running simulated call: %s (%.0f s) ..." % (args.scenario, args.seconds))
        original, degraded, info = run_call(args.scenario, args.seconds, seed=args.seed)

        c2_ref = codec2_reference(original, args.codec2_rate)
        ref_pcm = c2_ref.astype(np.float64)
        deg_pcm = degraded.astype(np.float64)
        ref_aligned, deg_aligned = align(ref_pcm, deg_pcm, max_shift=FS * 4)

    n = min(len(ref_aligned), len(deg_aligned))
    ref_aligned, deg_aligned = ref_aligned[:n], deg_aligned[:n]

    if n < FS:
        raise SystemExit("aligned audio too short (%d samples); check inputs" % n)

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        sf.write(os.path.join(args.out, "ref_aligned.wav"),
                 ref_aligned.astype(np.float64) / 32768, FS)
        sf.write(os.path.join(args.out, "deg_aligned.wav"),
                 deg_aligned.astype(np.float64) / 32768, FS)

    # -- also measure Codec2-only against original for comparison --
    if not args.input:
        orig_aligned = original[:len(c2_ref)].astype(np.float64)
        c2_aligned = c2_ref.astype(np.float64)
        n_orig = min(len(orig_aligned), len(c2_aligned))
        orig_aligned, c2_aligned = orig_aligned[:n_orig], c2_aligned[:n_orig]

    # -- header --
    print()
    print("Speech Quality Report")
    print("=" * 50)
    print("Scenario:  %s" % info["scenario"])
    print("Duration:  %.1f s aligned audio (%.1f s call)" % (n / FS, info["seconds"]))
    if not args.input:
        print()
        print("Reference: Codec2 %d encode/decode only (no channel)" % args.codec2_rate)
        print("Degraded:  Full chain (Codec2 -> AES -> RS -> modem -> channel -> modem -> RS -> AES -> Codec2)")
    if info["delivery"] is not None:
        print()
        print("Delivery:  %.1f%% of speech frames" % (100 * info["delivery"]))
    if info["fast_share"] is not None:
        print("Mode mix:  %.0f%% fast (1200), %.0f%% robust (700C)" %
              (100 * info["fast_share"], 100 * info["robust_share"]))
    if info["sdr_db"] is not None:
        print("SDR:       %.1f dB" % (info["sdr_db"] or 0))

    # -- PESQ --
    print()
    for mode, label in [("nb", "PESQ-NB"), ("wb", "PESQ-WB")]:
        score, err = measure_pesq(ref_aligned, deg_aligned, mode)
        if err:
            print("%-10s skipped (%s)" % (label + ":", err))
        else:
            line = "%-10s %.2f MOS-LQO" % (label + ":", score)
            if not args.input:
                ref_score, _ = measure_pesq(orig_aligned, c2_aligned, mode)
                if ref_score is not None:
                    line += "  (Codec2 alone: %.2f)" % ref_score
            print(line)

    # -- STOI --
    stoi_score, err = measure_stoi(ref_aligned, deg_aligned)
    if err:
        print("%-10s skipped (%s)" % ("STOI:", err))
    else:
        line = "%-10s %.3f" % ("STOI:", stoi_score)
        if not args.input:
            ref_stoi, _ = measure_stoi(orig_aligned, c2_aligned)
            if ref_stoi is not None:
                line += "          (Codec2 alone: %.3f)" % ref_stoi
        print(line)

    # -- Whisper WER --
    if args.no_whisper:
        print()
        print("Whisper:   skipped (--no-whisper)")
    else:
        print()
        print("Whisper WER (model: %s) ..." % args.whisper_model)
        ref_text, deg_text, wer, err = measure_whisper(
            ref_aligned, deg_aligned, args.whisper_model)
        if err:
            print("  skipped (%s)" % err)
        else:
            print("  Reference: \"%s\"" % (ref_text[:120] + "..." if len(ref_text) > 120 else ref_text))
            print("  Degraded:  \"%s\"" % (deg_text[:120] + "..." if len(deg_text) > 120 else deg_text))
            print("  WER:       %.1f%%" % (100 * wer))
            if not args.input:
                orig_text, c2_text, c2_wer, _ = measure_whisper(
                    orig_aligned, c2_aligned, args.whisper_model)
                if c2_wer is not None:
                    print("  Codec2 alone WER: %.1f%%" % (100 * c2_wer))

    print()


if __name__ == "__main__":
    main()
