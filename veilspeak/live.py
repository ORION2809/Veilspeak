"""Real-time operation with sound hardware: one unit, wired to one phone.

    python -m veilspeak.live --list
    python -m veilspeak.live --initiator --line-in 3 --line-out 3 --say hello.wav --heard out.wav
    python -m veilspeak.live --initiator --line-in 3 --line-out 3 --mic 1 --heard out.wav
    python -m veilspeak.live --loopback --seconds 30

Wiring: the sound device's output goes to the phone's headset microphone input (through an
attenuator: the phone expects microphone level, not line level), and the phone's headset
output goes to the sound device's input. The phone places an ordinary call.

Speech is sent a superframe at a time (about 1 s of added delay each way).
"""
import argparse
import signal
import sys
import threading
import time

import numpy as np

from . import modes as M
from .endpoint import FRAMES_PER_SF, Endpoint
from .ringbuf import AudioRingBuffer
from .vocoder import FRAME_SAMPLES

RATE = 8000
BLOCK = 160                       # 20 ms


class LiveUnit:
    """Glue between an Endpoint and block-based audio I/O. `pull_line(n)` returns the next n
    samples to play into the phone; `push_line(block)` takes what came out of the phone;
    `pull_heard(n)` returns decoded speech for the loudspeaker, delayed enough to be gapless."""

    PLAYOUT_FRAMES = 2 * FRAMES_PER_SF          # speech is played 0.96 s behind the frame counter

    def __init__(self, initiator, speech=None):
        self.ep = Endpoint(initiator, speech, speech_lead=FRAMES_PER_SF, start_at=0 if initiator else None)
        self._play_pos = None
        self._lock = threading.Lock()
        self._sas_confirmed = False
        self._muted = False
        self._start_time = time.monotonic()
        self._secure_time = None
        self._tamper_count = 0
        self._frames_since_secure = 0

    def pull_line(self, n):
        with self._lock:
            return self.ep.read(n)

    def push_line(self, block):
        with self._lock:
            self.ep.push_audio(block)
            if self.ep.secure_at is not None and self._secure_time is None:
                self._secure_time = time.monotonic()

    def add_speech(self, pcm):
        """Appends microphone samples (int16, 8 kHz) to what this side says."""
        with self._lock:
            pcm = np.asarray(pcm, dtype=np.int16)
            self.ep.speech = pcm if self.ep.speech is None else np.concatenate([self.ep.speech, pcm])

    def add_speech_stream(self, pcm_float):
        """Appends float64 microphone samples, converting to int16 for the vocoder."""
        pcm = np.clip(np.asarray(pcm_float, dtype=np.float64) * 32768, -32768, 32767).astype(np.int16)
        self.add_speech(pcm)

    def pull_heard(self, n):
        """n samples of decoded speech (int16). Missing frames are silence."""
        with self._lock:
            heard = self.ep.heard
            if self._play_pos is None:
                if not heard:
                    return np.zeros(n, dtype=np.int16)
                self._play_pos = (max(heard) - self.PLAYOUT_FRAMES) * FRAME_SAMPLES
            out = np.zeros(n, dtype=np.int16)
            first, last = self._play_pos // FRAME_SAMPLES, (self._play_pos + n - 1) // FRAME_SAMPLES
            for f in range(first, last + 1):
                pcm = heard.get(f)
                if pcm is None:
                    continue
                lo = max(self._play_pos, f * FRAME_SAMPLES)
                hi = min(self._play_pos + n, (f + 1) * FRAME_SAMPLES)
                out[lo - self._play_pos:hi - self._play_pos] = pcm[lo - f * FRAME_SAMPLES:hi - f * FRAME_SAMPLES]
            for f in [k for k in heard if k < first - FRAMES_PER_SF]:
                del heard[f]
            self._play_pos += n
            return out

    @property
    def state(self):
        """Structured status for the terminal UI."""
        with self._lock:
            ep = self.ep
            if ep.secure_at is None:
                conn = "locked" if ep.rx.state == "locked" else "listening"
            else:
                conn = "SECURE"
            mode = None
            if ep.log:
                for sf, event, detail in reversed(ep.log):
                    if event == "mode":
                        mode = detail
                        break
            sdr = ep.rx.sdr_db()
            return {
                "connection_state": conn,
                "sas": ep.hs.sas if ep.secure_at is not None else None,
                "sdr_db": sdr,
                "mode": mode,
                "frames_heard": len(ep.got_speech),
                "frames_sent": len(ep.sent_speech),
                "tamper_count": self._tamper_count,
                "go_secure_time": (self._secure_time - self._start_time) if self._secure_time else None,
                "rx_stats": dict(ep.rx.stats),
            }

    def status(self):
        ep = self.ep
        if ep.secure_at is None:
            return "setting up (%s)" % ("locked to the far end" if ep.rx.state == "locked" else "listening")
        mode = ep.log[-1][2] if ep.log and ep.log[-1][1] == "mode" else "-"
        sdr = ep.rx.sdr_db()
        return "SECURE  code %s  sending %s  receiving quality %s dB  frames heard %d" % (
            ep.hs.sas, mode, "-" if sdr is None else "%.1f" % sdr, len(ep.got_speech))


def _format_status_line(st):
    """One-line compact status from a LiveUnit.state dict."""
    conn = st["connection_state"]
    if conn != "SECURE":
        return "[%s]" % conn
    parts = ["[SECURE]", "SAS=%s" % st["sas"]]
    if st["mode"]:
        parts.append("mode=%s" % st["mode"])
    if st["sdr_db"] is not None:
        parts.append("SDR=%.1fdB" % st["sdr_db"])
    parts.append("heard=%d" % st["frames_heard"])
    parts.append("sent=%d" % st["frames_sent"])
    if st["tamper_count"]:
        parts.append("TAMPER=%d" % st["tamper_count"])
    return "  ".join(parts)


def run(args):
    import sounddevice as sd
    import soundfile as sf

    speech = None
    if args.say:
        data, rate = sf.read(args.say, dtype="float64", always_2d=True)
        data = data.mean(axis=1)
        if rate != RATE:
            from scipy.signal import resample_poly
            data = resample_poly(data, RATE, rate)
        speech = np.clip(data * 32768, -32768, 32767).astype(np.int16)

    unit = LiveUnit(args.initiator, speech)

    line_in_buf = AudioRingBuffer(RATE * 4)
    heard_file = sf.SoundFile(args.heard, "w", RATE, 1, "PCM_16") if args.heard else None
    stopping = threading.Event()

    mic_stream = None
    if getattr(args, "mic", None) is not None:
        mic_buf = AudioRingBuffer(RATE * 2)

        def mic_callback(indata, frames, when, status):
            mic_buf.write(indata[:, 0].astype(np.float64))

        mic_stream = sd.InputStream(
            samplerate=RATE, blocksize=BLOCK, channels=1, dtype="float32",
            device=args.mic, callback=mic_callback)
        mic_stream.start()

    def line_callback(indata, outdata, frames, when, status):
        outdata[:, 0] = unit.pull_line(frames) * args.level
        line_in_buf.write(indata[:, 0].astype(np.float64))

    def worker():
        while not stopping.is_set():
            avail = line_in_buf.available
            if avail < BLOCK:
                time.sleep(0.005)
                continue
            block = line_in_buf.read(BLOCK)
            unit.push_line(block)
            if heard_file is not None:
                heard_file.write(unit.pull_heard(BLOCK))
            if mic_stream is not None:
                mic_avail = mic_buf.available
                if mic_avail >= BLOCK:
                    unit.add_speech_stream(mic_buf.read(mic_avail))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def on_sigint(sig, frame):
        stopping.set()

    prev_handler = signal.signal(signal.SIGINT, on_sigint)

    try:
        with sd.Stream(samplerate=RATE, blocksize=BLOCK, channels=1, dtype="float32",
                       device=(args.line_in, args.line_out), callback=line_callback):
            if getattr(args, "ui", False):
                from .ui import TerminalUI
                TerminalUI(unit).run(args.seconds)
                stopping.set()
            else:
                start = time.time()
                while not stopping.is_set() and time.time() - start < args.seconds:
                    time.sleep(1.0)
                    if getattr(args, "monitor", False):
                        print("\r" + _format_status_line(unit.state), end="", flush=True)
                    else:
                        print(unit.status(), flush=True)
    finally:
        signal.signal(signal.SIGINT, prev_handler)
        stopping.set()
        thread.join(timeout=2.0)
        if mic_stream is not None:
            mic_stream.stop()
            mic_stream.close()
        if heard_file is not None:
            heard_file.close()

    st = unit.state
    print("\n--- Session summary ---")
    print("  State: %s" % st["connection_state"])
    if st["go_secure_time"] is not None:
        print("  Go-secure time: %.1f s" % st["go_secure_time"])
    if st["sas"]:
        print("  SAS code: %s" % st["sas"])
    print("  Frames sent: %d  heard: %d" % (st["frames_sent"], st["frames_heard"]))
    if st["sdr_db"] is not None:
        print("  Final SDR: %.1f dB" % st["sdr_db"])
    rx = st["rx_stats"]
    print("  Receiver: %d bursts, %d lost, %d jumps, %d repaired" % (
        rx.get("superframes", 0), rx.get("lost", 0), rx.get("jumps", 0), rx.get("repaired", 0)))
    if st["tamper_count"]:
        print("  TAMPER EVENTS: %d" % st["tamper_count"])


def loopback_test(seconds=30.0, verbose=True):
    """Two LiveUnits connected back-to-back in-process, no sound devices needed.

    Returns (unit_a, unit_b) after running for the given duration. Useful for verifying
    the streaming pipeline works without hardware.
    """
    from .cellsim import speech as voices

    said_a = voices.voice("a", seconds + 2)
    said_b = voices.voice("b", seconds + 2)
    a = LiveUnit(True, said_a)
    b = LiveUnit(False, said_b)

    delay_ab = [np.zeros(BLOCK, dtype=np.float64) for _ in range(7)]
    delay_ba = [np.zeros(BLOCK, dtype=np.float64) for _ in range(7)]

    steps = int(seconds * RATE / BLOCK)
    for step in range(steps):
        out_a = a.pull_line(BLOCK)
        out_b = b.pull_line(BLOCK)
        delay_ab.append(out_a)
        delay_ba.append(out_b)
        b.push_line(delay_ab.pop(0))
        a.push_line(delay_ba.pop(0))
        b.pull_heard(BLOCK)
        a.pull_heard(BLOCK)

        if verbose and step % (RATE // BLOCK) == 0:
            t = step * BLOCK / RATE
            print("%.0fs  A: %s  |  B: %s" % (t, _format_status_line(a.state), _format_status_line(b.state)))

    if verbose:
        print("\n--- Loopback results ---")
        for label, u in (("A", a), ("B", b)):
            st = u.state
            print("  %s: %s, frames sent=%d heard=%d" % (label, st["connection_state"], st["frames_sent"], st["frames_heard"]))
            if st["go_secure_time"] is not None:
                print("     go-secure: %.1f s, SAS: %s" % (st["go_secure_time"], st["sas"]))

    return a, b


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list sound devices and exit")
    ap.add_argument("--loopback", action="store_true", help="run two units back-to-back without sound devices")
    ap.add_argument("--initiator", action="store_true", help="this unit starts the key agreement")
    ap.add_argument("--line-in", type=int, help="input device wired to the phone's headset output")
    ap.add_argument("--line-out", type=int, help="output device wired to the phone's microphone input")
    ap.add_argument("--mic", type=int, default=None, help="microphone device for live speech input")
    ap.add_argument("--say", help="WAV file to send as this side's speech")
    ap.add_argument("--heard", help="WAV file to write the decoded far-end speech to")
    ap.add_argument("--level", type=float, default=1.0, help="output gain")
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--monitor", action="store_true", help="compact one-line status (overwrites in place)")
    ap.add_argument("--ui", action="store_true", help="curses-based terminal UI with SAS confirm and mute")
    args = ap.parse_args()
    if args.list:
        import sounddevice as sd
        print(sd.query_devices())
        return
    if args.loopback:
        loopback_test(args.seconds)
        return
    run(args)


if __name__ == "__main__":
    main()
