"""One direction of a phone call, from the sending unit's audio output to the receiving
unit's audio input:

    unit -> sending phone (filter, noise suppressor, gain control)
         -> one or more codec legs (silence detection, rate changes, frame loss, jitter buffer)
         -> receiving phone (filter, noise suppressor) -> unit

Everything is causal and seeded, so running a longer signal reproduces the earlier part
exactly. That lets a whole call (key agreement, then speech both ways) be simulated turn by
turn while the codecs keep their state across the pauses, as they do on a real call.
"""
from dataclasses import dataclass, field, replace

import numpy as np
from scipy.signal import resample_poly

from .. import dsp
from . import codecs as C
from . import network, phone

FS = 8000


@dataclass(frozen=True)
class Phone:
    band: tuple = (200.0, 3400.0)     # telephone-band filter, or None
    ns_db: float = 0.0                # noise suppressor strength (maximum attenuation); 0 = off
    ns_memory_s: float = 1.5
    agc: bool = False


@dataclass(frozen=True)
class Leg:
    codec: str                        # "amrnb", "amrwb", "evs", "g711", or "ns" (noise reduction in the network)
    rate: object = None               # bit/s, or ((time_s, rate), ...) for changes during the call; dB for "ns"
    dtx: bool = True                  # silence detection and comfort noise
    vad: int = 1                      # AMR-NB silence detector option (1 or 2)
    loss: float = 0.0                 # share of frames lost
    burst: float = 1.0                # mean length of a loss run, frames
    handovers: tuple = ()             # ((time_s, gap_ms), ...)
    jitter_ms: float = None           # packet jitter; turns on the VoLTE jitter buffer (EVS codec)
    spike_prob: float = 0.0           # chance per packet of a delay spike
    spike_ms: float = 120.0
    evs_fs: int = 16000               # 16000: wideband; 8000: narrowband EVS
    channel_aware: tuple = None       # EVS 13.2 with partial redundancy: ("LO" or "HI", offset in frames)
    fixed: bool = False               # use the fixed-point reference codec instead of the floating-point one


@dataclass(frozen=True)
class Handover:
    """The call moves to another network part-way through (a VoLTE call falling back to 2G,
    say). Until at_s the audio goes through the legs in `before`, afterwards through those in
    `after`, with a break in the audio and a different path delay. The new codec starts
    fresh: it has heard nothing of the call before the handover."""
    before: tuple
    after: tuple
    at_s: float
    gap_ms: float = 300.0             # audio lost during the handover
    delay_ms: float = 15.0            # how much later the audio arrives afterwards


@dataclass(frozen=True)
class Path:
    """One direction of a call."""
    name: str
    legs: tuple                       # Leg and Handover entries, in order
    sender: Phone = Phone()
    receiver: Phone = Phone()
    level_db: float = 0.0             # coupling gain between unit and phone
    noise_db: float = -60.0           # line noise at the phone input, dBFS
    clock_ppm: float = 0.0            # sample-clock offset between unit and phone
    seed: int = 0

    def with_seed(self, seed):
        return replace(self, seed=seed)


def _phone(x, p, fs):
    if p.band:
        x = phone.bandpass(x, fs, *p.band)
    if p.ns_db:
        x = phone.NoiseSuppressor(fs, p.ns_db, p.ns_memory_s).process(x)
    if p.agc:
        x = phone.agc(x, fs)
    return x


def run_leg(leg, x, seed, info=None):
    """x: audio at 8 kHz -> audio at 8 kHz after this codec leg."""
    if leg.codec == "g711":
        return phone.alaw(x)
    if leg.codec == "ns":                 # noise reduction applied by the network where it has the audio decoded
        return phone.NoiseSuppressor(FS, float(leg.rate), 1.5).process(x)
    n = len(x) // 160
    rate = network.per_frame_rate(n, leg.rate)
    lost = (network.burst_loss(n, leg.loss, leg.burst, np.random.default_rng([seed, 0]))
            | network.handover_loss(n, leg.handovers))
    if leg.codec == "amrnb":
        frames = C.amrnb_encode(x, rate, leg.dtx, leg.vad, leg.fixed)
        kinds = [C.amr_frame_kind(f, 8) for f in frames]
        y = C.amrnb_decode(frames, lost[:len(frames)], fixed=leg.fixed)
    elif leg.codec == "amrwb":
        frames = C.amrwb_encode(resample_poly(x, 2, 1), rate, leg.dtx, leg.fixed)
        kinds = [C.amr_frame_kind(f, 9) for f in frames]
        y = resample_poly(C.amrwb_decode(frames, lost[:len(frames)], fixed=leg.fixed), 1, 2)
    elif leg.codec == "evs":
        up = leg.evs_fs // FS
        frames = C.evs_encode(x if up == 1 else resample_poly(x, up, 1), leg.evs_fs, rate, leg.dtx,
                              channel_aware=leg.channel_aware, fixed=leg.fixed)
        kinds = [C.evs_frame_kind(f) for f in frames]
        arrivals = None
        if leg.jitter_ms is not None:
            arrivals = network.packet_arrivals(len(frames), 60.0, leg.jitter_ms, leg.spike_prob, leg.spike_ms, seed)
        y = C.evs_decode(frames, leg.evs_fs, lost[:len(frames)], arrivals, fixed=leg.fixed)
        y = y if up == 1 else resample_poly(y, 1, up)
    else:
        raise ValueError(leg.codec)
    if info is not None:
        info.append({"codec": leg.codec, "frames": len(kinds),
                     "speech": kinds.count("speech"), "sid": kinds.count("sid"),
                     "nodata": kinds.count("nodata"), "lost": int(lost[:len(kinds)].sum())})
    return y


def run(path, x, info=None, memo=None):
    """Carries audio x (8 kHz, float) over one direction of the call. memo: a dict the caller
    keeps for this direction when it calls again with x extended (saves recomputing the
    start of the resampling; the result is the same)."""
    rng = np.random.default_rng(path.seed)
    y = np.asarray(x, dtype=float) * 10 ** (path.level_db / 20)
    if path.clock_ppm:
        step = 1 + path.clock_ppm * 1e-6
        done = memo.get("clock") if memo is not None else None
        keep = max(len(done) - 64, 0) if done is not None else 0      # the last few depend on what came after
        y = np.concatenate([done[:keep] if keep else np.zeros(0),
                            dsp.resample_at(y, np.arange(keep, int(len(y) / step)) * step)])
        if memo is not None:
            memo["clock"] = y
    y = y + rng.normal(0, 10 ** (path.noise_db / 20), len(y))
    y = _phone(y, path.sender, FS)
    for i, leg in enumerate(path.legs):
        if isinstance(leg, Handover):
            y = _handover(leg, y, path.seed * 1000 + 10 * (i + 1), info)
        else:
            y = run_leg(leg, y, path.seed * 100 + i + 1, info)
    y = _phone(y, path.receiver, FS)
    return y


def _handover(h, x, seed, info):
    cut = int(h.at_s * FS)
    first, second = x, np.concatenate([np.zeros(min(cut, len(x))), x[cut:]])
    for j, leg in enumerate(h.before):
        first = run_leg(leg, first, seed + j, info)
    if len(x) <= cut:
        return first
    for j, leg in enumerate(h.after):
        second = run_leg(leg, second, seed + 5 + j, info)
    out = np.zeros(len(x))
    n = min(cut, len(first))
    out[:n] = first[:n]
    resume, shift = cut + int(h.gap_ms * FS / 1000), int(h.delay_ms * FS / 1000)
    stop = min(len(out), len(second) + shift)
    if stop > resume:
        out[resume:stop] = second[resume - shift:stop - shift]
    return out
