"""A whole two-way call between two endpoints over simulated phone paths.

The codecs here are batch programs, but every stage of a path is causal and seeded, so
running a longer transmission reproduces the earlier output exactly. The call is therefore
advanced one superframe (480 ms) at a time: each end produces its next superframe from what
it has heard so far, then both paths are re-run on everything sent so far and each end is
given the newly arrived audio. The codecs keep their state across the whole call, as on a
real call, and each end reacts only to audio it could already have received.
"""
import numpy as np

from .. import modes as M
from ..endpoint import Endpoint, FRAMES_PER_SF
from ..vocoder import FRAME_SAMPLES
from . import call

GUARD = 400                      # samples held back at the end of a partial run (50 ms)


def simulate(path_ab, path_ba, speech_a, speech_b, seconds=45.0, feedback_every=1, trace=None,
             sidetone_db=None, start_s=0.0):
    """Runs a call: A is the initiator. Returns (report dict, endpoint A, endpoint B).

    The call begins as an ordinary one, each person's speech going through the phones as it
    is. At start_s A's unit is switched on; B's unit switches itself on when it hears A's.
    feedback_every: how often (in superframes) each unit is given the audio that has arrived;
    1 is as on a real call, larger values run faster and make the units react late.
    sidetone_db: level at which each unit also hears its own transmission (a phone feeds some
    of the microphone signal back to the earpiece), or None."""
    start = int(round(start_s * 8000 / M.SF_SAMPLES))
    a, b = Endpoint(True, speech_a, "A", start_at=start), Endpoint(False, speech_b, "B", start_at=None)
    tx = {"a": [], "b": []}
    fed = {"a": 0, "b": 0}
    info = {"ab": [], "ba": []}
    memo = {"ab": {}, "ba": {}}
    last_heard = {}
    steps = int(seconds * 8000 / M.SF_SAMPLES)
    secure_step = {}
    for step in range(steps):
        tx["a"].append(a.next_superframe())
        tx["b"].append(b.next_superframe())
        for name, ep in (("a", a), ("b", b)):
            if ep.secure_at is not None and name not in secure_step:
                secure_step[name] = step
        if not (step % feedback_every == feedback_every - 1 or step == steps - 1):
            continue
        for src, dst, ep, path, key in (("a", "b", b, path_ab, "ab"), ("b", "a", a, path_ba, "ba")):
            sent = np.concatenate(tx[src])
            leg_info = []
            heard = call.run(path, sent, leg_info, memo[key])
            info[key] = leg_info
            last_heard[key] = heard
            limit = min(len(heard), len(sent)) - GUARD
            if limit > fed[dst]:
                chunk = heard[fed[dst]:limit]
                if sidetone_db is not None:
                    own = np.concatenate(tx[dst])
                    lo, hi = max(fed[dst] - 40, 0), limit - 40          # own signal, 5 ms late
                    side = np.zeros(len(chunk))
                    side[len(chunk) - (hi - lo):] = own[lo:hi]
                    chunk = chunk + side * 10 ** (sidetone_db / 20)
                ep.push_audio(chunk)
                fed[dst] = limit
        if trace:
            trace(step, a, b)
    out = report(a, b, secure_step, info, steps, start)
    out["line_ab"] = last_heard.get("ab")
    return out, a, b


def _direction(sender, receiver, steps):
    """Speech delivery from sender to receiver over the part of the call after both ends
    were secure. Speech in the last two superframes is left out: it may still be in flight."""
    frames = range(FRAMES_PER_SF, FRAMES_PER_SF * (steps - 2 - sender.secure_at))   # numbered from secure start
    sent = sum(1 for n in frames if n in sender.sent_speech)
    ok = sum(1 for n in frames if n in sender.sent_speech and receiver.got_speech.get(n) == sender.sent_speech[n])
    wrong = sum(1 for n in frames if n in receiver.got_speech and receiver.got_speech[n] != sender.sent_speech.get(n))
    gap = longest = 0
    for n in frames:
        gap = 0 if (n in sender.sent_speech and receiver.got_speech.get(n) == sender.sent_speech[n]) else gap + 1
        longest = max(longest, gap)
    by_mode = {}
    for n in frames:
        if n in sender.sent_speech and receiver.got_speech.get(n) == sender.sent_speech[n]:
            rate = sender.sent_speech[n][0]
            by_mode[rate] = by_mode.get(rate, 0) + 1
    total = len(frames)
    return {"speech_frames": total, "sent": sent, "delivered": ok, "wrong": wrong,
            "delivered_share": ok / total if total else 0.0,
            "fast_share": by_mode.get(1200, 0) / total if total else 0.0,
            "robust_share": by_mode.get(700, 0) / total if total else 0.0,
            "longest_gap_s": longest * FRAME_SAMPLES / 8000,
            "receiver": dict(receiver.rx.stats), "sdr_db": receiver.rx.sdr_db()}


def report(a, b, secure_step, legs, steps, start=0):
    secured = a.secure_at is not None and b.secure_at is not None
    out = {"secure": secured, "sas_match": secured and a.hs.sas == b.hs.sas,
           "time_to_secure_s": (max(secure_step.values()) + 1 - start) * M.SF_SAMPLES / 8000 if secured else None,
           "legs": legs}
    if secured:
        out["a_to_b"] = _direction(a, b, steps)
        out["b_to_a"] = _direction(b, a, steps)
    return out


def heard_audio(endpoint, sender):
    """The speech this endpoint decoded, as one int16 array on the sender's timeline (silence
    where frames are missing), starting from the sender's first secure superframe."""
    first = FRAMES_PER_SF
    last = max(endpoint.heard, default=first)
    out = np.zeros((last - first + 1) * FRAME_SAMPLES, dtype=np.int16)
    for n, pcm in endpoint.heard.items():
        if first <= n <= last:
            out[(n - first) * FRAME_SAMPLES:(n - first + 1) * FRAME_SAMPLES] = pcm
    return out
