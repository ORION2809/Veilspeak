"""B0 -- the covert-mode baseline: how detectable is the current Track A signal?

Builds the kind of speech-vs-modem detector a network might run (veilspeak/covert/detect.py)
and measures how well it tells the Track A modem apart from ordinary speech. This documents an
honest limitation: a detector AUC near 1.0 means the signal is plainly a modem that a network
can detect and block. It is measurement only, not a step toward evading anything (plan_v3.md
section 9).

    python tools/covert_baseline.py                 # prints the report
    python tools/covert_baseline.py --out repro_results   # also writes covert_B0_*.{txt,csv}

Speech comes from the real `say` recordings in demo_out/ (a_says.wav, b_says.wav); the modem
is synthesised. Both are sliced into 1 s windows, each scaled to unit RMS so the detector
cannot win on loudness. This is a proof of baseline detectability on two synthetic voices, not
a corpus study -- a real baseline wants many speakers and codec-processed audio (see plan_v3.md,
"Detectability"). It is enough to establish the starting point.
"""
import argparse
import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veilspeak import modes as M                            # noqa: E402
from veilspeak.covert import detect                         # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEECH_WAVS = [os.path.join(HERE, "demo_out", w) for w in ("a_says.wav", "b_says.wav")]


def load_speech():
    sig = []
    for path in SPEECH_WAVS:
        if not os.path.exists(path):
            raise SystemExit("need %s; run `python -m veilspeak \"2G full rate, good signal\"` first" % path)
        data, rate = sf.read(path, dtype="float64", always_2d=True)
        data = data.mean(axis=1)
        if rate != 8000:
            raise SystemExit("%s is %d Hz, expected 8000" % (path, rate))
        sig.append(data)
    return detect.windows(np.concatenate(sig))


def build():
    speech = load_speech()
    modem = (detect.windows(detect.modem_signal(M.FAST, 70, seed=1)) +
             detect.windows(detect.modem_signal(M.ROBUST, 70, seed=2)))
    Xs, Xm = detect.feature_matrix(speech), detect.feature_matrix(modem)
    X = np.vstack([Xs, Xm])
    y = np.concatenate([np.zeros(len(Xs)), np.ones(len(Xm))]).astype(int)
    return X, y, len(speech), len(modem)


def split(n, frac, seed=0):
    idx = np.random.default_rng(seed).permutation(n)
    cut = int(n * frac)
    return idx[cut:], idx[:cut]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", help="directory to also write covert_B0_baseline.txt and covert_B0_features.csv")
    args = ap.parse_args()

    X, y, n_speech, n_modem = build()
    lines = []

    def say(s=""):
        print(s)
        lines.append(s)

    say("Covert-mode baseline (B0): detectability of the current Track A signal")
    say("=" * 70)
    say("Windows: %d speech (demo_out say voices), %d modem (fast+robust, synthesised)."
        % (n_speech, n_modem))
    say("Each window is 1 s, scaled to unit RMS before features (level-blind).")
    say("")

    say("Per-feature separability (univariate AUC; 0.50 = useless, 1.00 = perfect):")
    order = sorted(range(len(detect.FEATURES)), key=lambda j: -abs(detect.auc(X[:, j], y) - 0.5))
    for j in order:
        a = detect.auc(X[:, j], y)
        bar = "#" * int(round(abs(a - 0.5) * 40))
        say("  %-12s %.3f  %s" % (detect.FEATURES[j], a, bar))
    say("")

    best = order[0]
    say("Single best feature alone (%s), no training: AUC %.3f"
        % (detect.FEATURES[best], detect.auc(X[:, best], y)))
    say("")

    tr, te = split(len(y), 0.3, seed=7)
    clf = detect.Logistic().fit(X[tr], y[tr])
    s_te = clf.score(X[te])
    a_te = detect.auc(s_te, y[te])
    pred = (s_te > np.median(clf.score(X[tr]))).astype(int)   # threshold from train
    thr = 0.0
    # pick the threshold that maximises accuracy on train, apply to test
    strain = clf.score(X[tr])
    cand = np.sort(strain)
    accs = [(np.mean((strain > t).astype(int) == y[tr]), t) for t in cand]
    thr = max(accs)[1]
    acc_te = float(np.mean((s_te > thr).astype(int) == y[te]))

    say("Full logistic-regression detector (12 features, 70/30 train/test split):")
    say("  test AUC       %.4f" % a_te)
    say("  test accuracy  %.1f%%  (at the train-optimal threshold)" % (100 * acc_te))
    say("")
    verdict = ("trivially detectable" if a_te > 0.98 else
               "highly detectable" if a_te > 0.9 else
               "detectable" if a_te > 0.7 else "hard to separate")
    say("Verdict: the current Track A signal is %s (AUC %.4f)." % (verdict, a_te))
    say("This documents a limitation, not a target: the signal is plainly a modem that a")
    say("network can detect and could block (plan_v3.md section 9). It is measurement only.")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        txt = os.path.join(args.out, "covert_B0_baseline.txt")
        with open(txt, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        csv = os.path.join(args.out, "covert_B0_features.csv")
        with open(csv, "w") as fh:
            fh.write("label," + ",".join(detect.FEATURES) + "\n")
            for row, lab in zip(X, y):
                fh.write(("modem" if lab else "speech") + "," + ",".join("%.6g" % v for v in row) + "\n")
        print("\nWrote %s and %s" % (txt, csv))


if __name__ == "__main__":
    main()
