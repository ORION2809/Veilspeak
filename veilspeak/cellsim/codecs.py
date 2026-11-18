"""Wrappers around the 3GPP reference speech codecs (built into third_party/bin).

    AMR-NB   TS 26.104   2G/3G narrowband, 4.75-12.2 kbit/s
    AMR-WB   TS 26.204   VoLTE wideband, 6.6-23.85 kbit/s
    EVS      TS 26.443   VoLTE, 5.9-24.4 kbit/s and up; also carries AMR-WB ("IO" modes) and
                         includes the standard VoLTE jitter buffer (TS 26.448)

Those are the floating-point versions. With fixed=True the functions use the fixed-point
versions instead (TS 26.073, 26.173, 26.442; tools/build_codecs_fixed.sh), which are the
bit-exact definitions that phones and network equipment are tested against.

Each encode() returns one entry per 20 ms frame, so the network model can drop, delay or
re-rate individual frames exactly as a radio link or packet network would.
"""
import os
import struct
import subprocess
import tempfile

import numpy as np

BIN = os.path.join(os.path.dirname(__file__), "..", "..", "third_party", "bin")

AMRNB_MODES = {4750: "MR475", 5150: "MR515", 5900: "MR59", 6700: "MR67",
               7400: "MR74", 7950: "MR795", 10200: "MR102", 12200: "MR122"}
AMRNB_SIZES = [12, 13, 15, 17, 19, 20, 26, 31, 5, 0, 0, 0, 0, 0, 0, 0]     # payload bytes by frame type
AMRWB_RATES = [6600, 8850, 12650, 14250, 15850, 18250, 19850, 23050, 23850]
AMRWB_SIZES = [17, 23, 32, 36, 40, 46, 50, 58, 60, 5, 0, 0, 0, 0, 0, 0]
EVS_RATES = [5900, 7200, 8000, 9600, 13200, 16400, 24400, 32000, 48000]
G192_GOOD, G192_BAD = 0x6B21, 0x6B20


def _run(*cmd):
    done = subprocess.run([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if done.returncode != 0:
        raise RuntimeError("%s failed:\n%s" % (cmd[0], done.stdout.decode(errors="replace")[-600:]))


def _per_frame(rate, nframes):
    return np.full(nframes, rate) if np.isscalar(rate) else np.resize(np.asarray(rate), nframes)


def _pcm(x):
    return np.clip(np.round(np.asarray(x, dtype=float) * 32768), -32768, 32767).astype("<i2")


# ---------------------------------------------------------------------------------------
# AMR-NB and AMR-WB: frames are bytes, first byte = header (frame type << 3 | quality << 2)
# ---------------------------------------------------------------------------------------
def _amr_split(data, magic, sizes):
    assert data.startswith(magic), "unexpected bitstream header"
    pos, frames = len(magic), []
    while pos < len(data):
        size = 1 + sizes[(data[pos] >> 3) & 15]
        frames.append(data[pos:pos + size])
        pos += size
    return frames


def amr_frame_kind(frame, sid_type):
    ft = (frame[0] >> 3) & 15
    return "speech" if ft < sid_type else "sid" if ft == sid_type else "nodata"


def _amr_damage(frames, lost, lost_header):
    """lost: boolean per frame. A radio bad frame keeps its bits but clears the quality bit
    (lost_header=None); a lost packet is replaced by the given one-byte header."""
    out = []
    for frame, gone in zip(frames, lost):
        if not gone or len(frame) == 1:
            out.append(frame)
        elif lost_header is None:
            out.append(bytes([frame[0] & ~0x04]) + frame[1:])
        else:
            out.append(bytes([lost_header]))
    return out


def _bin(name, fixed):
    return os.path.join(BIN, name.replace("_", "_fx_", 1) if fixed else name)


def amrnb_encode(x, rate=12200, dtx=True, vad=1, fixed=False):
    """x: float audio at 8 kHz. rate: bit/s, or one value per 20 ms frame."""
    pcm = _pcm(x)
    nframes = len(pcm) // 160
    with tempfile.TemporaryDirectory() as tmp:
        src, dst, modes = (os.path.join(tmp, n) for n in ("in.pcm", "out.amr", "modes.txt"))
        pcm[:nframes * 160].tofile(src)
        with open(modes, "w") as f:
            f.write("\n".join(AMRNB_MODES[int(r)] for r in _per_frame(rate, nframes)) + "\n")
        _run(_bin("amrnb_enc_vad%d" % vad, fixed), *(["-dtx"] if dtx else []),
             "-modefile=" + modes, src, dst)
        return _amr_split(open(dst, "rb").read(), b"#!AMR\n", AMRNB_SIZES)


def amrnb_decode(frames, lost=None, packet_loss=False, fixed=False):
    """Returns float audio at 8 kHz. lost: boolean per frame."""
    if lost is not None:
        frames = _amr_damage(frames, lost, 0x7C if packet_loss else None)   # 0x7C: NO_DATA
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.amr"), os.path.join(tmp, "out.pcm")
        with open(src, "wb") as f:
            f.write(b"#!AMR\n" + b"".join(frames))
        _run(_bin("amrnb_dec", fixed), src, dst)
        return np.fromfile(dst, dtype="<i2") / 32768.0


def amrwb_encode(x, rate=12650, dtx=True, fixed=False):
    """x: float audio at 16 kHz."""
    pcm = _pcm(x)
    nframes = len(pcm) // 320
    with tempfile.TemporaryDirectory() as tmp:
        src, dst, modes = (os.path.join(tmp, n) for n in ("in.pcm", "out.awb", "modes.txt"))
        pcm[:nframes * 320].tofile(src)
        with open(modes, "w") as f:
            f.write("\n".join(str(AMRWB_RATES.index(int(r))) for r in _per_frame(rate, nframes)) + "\n")
        if fixed:                       # this program takes one rate for the whole file
            rates = set(int(r) for r in _per_frame(rate, nframes))
            assert len(rates) == 1, "rate changes need the floating-point AMR-WB build"
            _run(_bin("amrwb_enc", True), *(["-dtx"] if dtx else []), "-mime", AMRWB_RATES.index(rates.pop()), src, dst)
        else:
            _run(_bin("amrwb_enc", False), *(["-dtx"] if dtx else []), "-modefile", modes, src, dst)
        return _amr_split(open(dst, "rb").read(), b"#!AMR-WB\n", AMRWB_SIZES)


def amrwb_decode(frames, lost=None, packet_loss=True, fixed=False):
    """Returns float audio at 16 kHz."""
    if lost is not None:
        frames = _amr_damage(frames, lost, 0x74 if packet_loss else None)   # 0x74: SPEECH_LOST
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.awb"), os.path.join(tmp, "out.pcm")
        with open(src, "wb") as f:
            f.write(b"#!AMR-WB\n" + b"".join(frames))
        _run(_bin("amrwb_dec", fixed), *(["-mime"] if fixed else []), src, dst)
        return np.fromfile(dst, dtype="<i2") / 32768.0


# ---------------------------------------------------------------------------------------
# EVS (and AMR-WB carried by the EVS codec): frames are arrays of soft bits (G.192)
# ---------------------------------------------------------------------------------------
def evs_encode(x, fs=16000, rate=13200, dtx=True, max_band=None, channel_aware=None, fixed=False):
    """x: float audio at fs (8000, 16000 or 32000). rate may be an AMR-WB rate (AMR-WB IO
    mode) and may be given per frame. channel_aware: ("LO"|"HI", offset) at 13.2 kbit/s."""
    pcm = _pcm(x)
    hop = fs // 50
    nframes = len(pcm) // hop
    with tempfile.TemporaryDirectory() as tmp:
        src, dst, rates = (os.path.join(tmp, n) for n in ("in.pcm", "out.192", "rates.bin"))
        pcm[:nframes * hop].tofile(src)
        if np.isscalar(rate):
            rate_arg = str(int(rate))
        else:
            _per_frame(rate, nframes).astype("<i4").tofile(rates)
            rate_arg = rates
        opts = ["-q"] + (["-dtx"] if dtx else [])
        if max_band:
            opts += ["-max_band", max_band]
        if channel_aware:
            opts += ["-rf", channel_aware[0], str(channel_aware[1])]
        _run(_bin("EVS_cod", fixed), *opts, rate_arg, fs // 1000, src, dst)
        words = np.fromfile(dst, dtype="<u2")
    frames, pos = [], 0
    while pos < len(words):
        nbits = int(words[pos + 1])
        frames.append(words[pos + 2:pos + 2 + nbits].copy())
        pos += 2 + nbits
    return frames


def evs_frame_kind(frame):
    return "nodata" if len(frame) == 0 else "sid" if len(frame) <= 48 else "speech"


def evs_decode(frames, fs=16000, lost=None, arrival_ms=None, fixed=False):
    """Returns float audio at fs.

    lost: boolean per frame (frame erased).
    arrival_ms: if given, the arrival time of each frame's packet in milliseconds (frame i
    was sent at 20*i). The decoder then runs its jitter buffer, which adapts the playout
    delay and stretches or compresses the audio in time. Lost frames are simply not delivered.
    """
    n = len(frames)
    lost = np.zeros(n, bool) if lost is None else np.asarray(lost, bool)
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.192"), os.path.join(tmp, "out.pcm")
        with open(src, "wb") as f:
            if arrival_ms is None:
                for frame, gone in zip(frames, lost):
                    if gone:
                        f.write(struct.pack("<HH", G192_BAD, 0))
                    else:
                        f.write(struct.pack("<HH", G192_GOOD, len(frame)) + frame.astype("<u2").tobytes())
            else:
                order = np.argsort(np.asarray(arrival_ms), kind="stable")
                for i in order:
                    frame = frames[i]
                    if lost[i] or len(frame) == 0:          # nothing is sent for NO_DATA frames
                        continue
                    f.write(struct.pack("<II", 12 + 2 + len(frame), int(arrival_ms[i])))
                    f.write(struct.pack("<H", 22) + struct.pack(">HI", i & 0xFFFF, 20 * i) + struct.pack("<I", 0))
                    f.write(struct.pack("<HH", G192_GOOD, len(frame)) + frame.astype("<u2").tobytes())
        opts = ["-q"] + (["-VOIP"] if arrival_ms is not None else [])
        _run(_bin("EVS_dec", fixed), *opts, fs // 1000, src, dst)
        return np.fromfile(dst, dtype="<i2") / 32768.0
