"""Test signal for real phone calls: make a WAV to play into a call, check a recording of it.

This is the first experiment to run on real phones, before any live two-way call. It needs
no keys and no second unit: play the test signal into a call on one phone, record what comes
out of the other phone, and this tool reports how much of it survived.

    python tools/linetest.py make line_test.wav            # one minute: about a third in each mode
    python tools/linetest.py make line_test.wav --seconds 180 --rate 48000
    python tools/linetest.py check recording.wav           # any sample rate, mono or stereo

How to use it
  1. Call phone B from phone A and answer.
  2. Feed line_test.wav into phone A as if it were the microphone (headset cable with an
     attenuator, an audio interface, or an injection path inside the phone), with the
     phone's own level meter showing normal speech level. Do not play it through a
     loudspeaker into the microphone: room acoustics spoil the result.
  3. Record phone B's earpiece signal electrically (headset output into a line input).
  4. Run `check` on the recording. Repeat on each network and codec of interest; the phone's
     engineering screen shows which codec and rate the call uses.

The report lists, per mode, the share of frames recovered, the signal-to-distortion ratio the
receiver measured, how often the audio was shifted in time, and how often lock was lost.
"""
import argparse
import os
import sys

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np                                      # noqa: E402
import soundfile as sf                                  # noqa: E402
from scipy.signal import resample_poly                  # noqa: E402

sys.path.insert(0, ".")
from veilspeak import fec, modem, modes as M            # noqa: E402

SEED = 20261117
BLOCK = 12                       # superframes of one mode before moving to the next
ORDER = (M.ROBUST, M.FAST, M.IDLE)


def plan(superframes):
    """[(kind, [messages])] for the whole signal: two TRAIN, then blocks of each mode, with
    a TRAIN at the start of each block so a receiver that lost lock can pick up again."""
    rng = np.random.default_rng(SEED)
    out = [(M.TRAIN, []), (M.TRAIN, [])]
    block = 0
    while len(out) < superframes:
        kind = ORDER[block % len(ORDER)]
        out.append((M.TRAIN, []))
        for _ in range(BLOCK):
            out.append((kind, [rng.bytes(M.MODES[kind].msg_bytes) for _ in range(M.MODES[kind].frames)]))
        block += 1
    return out[:superframes]


def make(args):
    sent = plan(int(args.seconds / 0.48))
    framer = modem.Framer()
    codecs = {k: fec.FrameCodec(m) for k, m in M.MODES.items()}
    audio = [np.zeros(8000)]                              # a second of silence first
    for kind, msgs in sent:
        audio.append(framer.superframe(kind) if kind == M.TRAIN
                     else framer.superframe(kind, [codecs[kind].encode(m) for m in msgs]))
    audio.append(np.zeros(8000))
    x = np.concatenate(audio)
    if args.rate != 8000:
        x = resample_poly(x, args.rate, 8000)
    sf.write(args.wav, x, args.rate, subtype="PCM_16")
    print("%s: %.0f s at %d Hz, level %.0f dBFS RMS in the bursts, %d superframes" % (
        args.wav, len(x) / args.rate, args.rate, 20 * np.log10(M.GRID.amplitude * np.sqrt(M.K / 2)), len(sent)))


class Sink:
    def __init__(self, sent):
        self.codecs = {k: fec.FrameCodec(m) for k, m in M.MODES.items()}
        self.valid = {k: set() for k in M.MODES}
        for kind, msgs in sent:
            if kind != M.TRAIN:
                self.valid[kind].update(msgs)
        self.got = set()
        self.sdr = {k: [] for k in M.MODES}

    def frame(self, kind, index, symbols, confidence, when):
        hit = self.codecs[kind].decode(symbols, confidence, lambda m: m in self.valid[kind])
        if hit is None:
            return None
        self.got.add((kind, hit[0]))
        return hit[1]

    def superframe(self, kind, when, info):
        if kind in self.sdr and info.get("sdr_db") is not None:
            self.sdr[kind].append(info["sdr_db"])


def check(args):
    data, rate = sf.read(args.wav, dtype="float64", always_2d=True)
    x = data[:, args.channel] if data.shape[1] > 1 else data[:, 0]
    if rate != 8000:
        x = resample_poly(x, 8000, rate)
    sent = plan(int(len(x) / 8000 / 0.48) + 2)
    sink = Sink(sent)
    rx = modem.Receiver(sink)
    for i in range(0, len(x), 4000):
        rx.push(x[i:i + 4000])
    print("%s: %.0f s; receiver read %d bursts, followed %d time shifts, repaired %d bursts, started over %d times" % (
        args.wav, len(x) / 8000, rx.stats["superframes"], rx.stats["jumps"], rx.stats["repaired"], rx.stats["relocks"]))
    if rx.stats["superframes"] == 0:
        print("No training burst was found: the signal did not get through, or the recording is of something else.")
        return
    # only count what the recording is long enough to contain
    heard = max((i for i, (kind, msgs) in enumerate(sent) if any((kind, m) in sink.got for m in msgs)), default=0)
    print("%-8s %-22s %10s %8s" % ("mode", "carries", "frames", "SDR dB"))
    for kind, carries in ((M.FAST, "speech, Codec2 1200"), (M.ROBUST, "speech, Codec2 700C"), (M.IDLE, "keep-alive only")):
        want = [(kind, m) for k, msgs in sent[:heard + 1] if k == kind for m in msgs]
        if want:
            ok = sum(w in sink.got for w in want)
            sdr = "%.1f" % np.median(sink.sdr[kind]) if sink.sdr[kind] else "-"
            print("%-8s %-22s %4d/%-4d %3.0f%% %6s" % (M.MODES[kind].name, carries, ok, len(want), 100.0 * ok / len(want), sdr))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("wav")
    m.add_argument("--seconds", type=float, default=60.0)
    m.add_argument("--rate", type=int, default=8000, help="sample rate of the WAV file")
    c = sub.add_parser("check")
    c.add_argument("wav")
    c.add_argument("--channel", type=int, default=0, help="which channel of a stereo recording")
    a = ap.parse_args()
    (make if a.cmd == "make" else check)(a)
