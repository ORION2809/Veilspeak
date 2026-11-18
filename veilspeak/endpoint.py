"""One end of a secure call: the unit that sits between a person and their phone.

It produces one superframe of audio at a time for the phone's microphone input
(next_superframe) and consumes the audio coming out of the phone (push_audio). Both
directions run continuously and independently, so the two people can talk at once.

Life of a call
  0. Until it is switched on the unit passes the person's speech through untouched. The
     initiator is switched on by its user; the other unit switches itself on when it hears
     the initiator's training signal.
  1. Both ends send TRAIN superframes so each receiver can lock and learn the channel. The
     two directions use different training and pilot sequences, so a unit cannot mistake the
     echo of its own signal for the far end.
  2. Key agreement: each end keeps resending its current handshake step in CONTROL
     superframes until the other end's next step arrives, with a TRAIN superframe every
     fourth until then in case the other end has not locked yet.
  3. Speech: every superframe carries 480 ms of encrypted speech, in FAST mode (Codec2 1200)
     or ROBUST mode (Codec2 700C). Each frame also carries a 2-bit report telling the far
     end which mode this end can currently receive; the far end obeys it. When an end hears
     nothing valid, or is told it is not being heard, it mixes in TRAIN and IDLE superframes
     so the link can re-form without any other signalling.
"""
import binascii
from collections import deque

import numpy as np

from . import modes as M
from . import session
from .fec import FrameCodec
from .modem import Framer, Receiver
from .vocoder import FRAME_SAMPLES, Vocoder

START_TRAIN = 2                 # TRAIN superframes sent before anything else
DUAL_START_TIMEOUT = 8          # superframes of TRAIN before checking for dual-start
FRAMES_PER_SF = M.SF_SAMPLES // FRAME_SAMPLES       # 12 speech frames of 40 ms
# Superframe n carries the speech frames 12n - lead .. 12n - lead + 11 of the sender's stream.
#   lead 4: the unit keeps feeding fresh speech frames into the superframe while it is on the
#           air (the Reed-Solomon code is systematic, so speech bits go out first and the
#           parity last). Added one-way delay about 0.45 s in FAST mode, 0.64 s in ROBUST.
#   lead 12: the unit sends the previous superframe's speech as a block: about 1 s.
# The simulator uses 4; the real-time wrapper (live.py) works on whole superframes and uses 12.
SPEECH_LEAD = 4
LOST, WANT_IDLE, WANT_ROBUST, WANT_FAST = range(4)  # the 2-bit link report
_WANT_KIND = {WANT_IDLE: M.IDLE, WANT_ROBUST: M.ROBUST, WANT_FAST: M.FAST}
CTRL_DATA = M.MODES[M.CONTROL].msg_bytes - 3        # 42 payload bytes per CONTROL frame

# Measured over every rate of the three codecs: FAST lost no frame in 2,900 at a
# signal-to-distortion ratio of 4 dB or more, 10% at 3-4 dB and 24% at 2-3 dB.
FAST_SDR_DB = 4.5               # ask for FAST from here up
FAST_FLOOR_DB = 3.8             # and stop asking below this
ROBUST_SDR_DB = -4.0            # below this ROBUST is not worth trying
DEAF_AFTER = 5                  # superframes without a valid frame before an end calls itself deaf
# What a deaf end sends: mostly speech in the safe mode (the far end may hear it perfectly
# well), a training burst in case the far end has lost it too, and one IDLE frame, the only
# kind that gets through the lowest codec rates.
_DEAF_CYCLE = (M.TRAIN, M.IDLE) + (M.ROBUST,) * 6
# What an end sends when told it is not heard: something to lock to, the sturdiest frame (so
# the far end can judge the channel), then speech.
_LOST_CYCLE = (M.TRAIN, M.IDLE, M.ROBUST, M.ROBUST)


class _DualStartProbe:
    """Minimal receiver sink for detecting a same-role signal during dual-start."""
    def frame(self, kind, index, symbols, confidence, when):
        return None
    def superframe(self, kind, when, info):
        pass


class Endpoint:
    def __init__(self, initiator, speech=None, name="", speech_lead=SPEECH_LEAD, start_at=0):
        """speech: int16 samples at 8 kHz, what this side says. start_at: the superframe at
        which the unit is switched on, or None to switch on when the far end's training
        signal is heard."""
        self.initiator, self.name = initiator, name or ("initiator" if initiator else "responder")
        self.speech = speech
        self.lead = speech_lead
        self.start_at = start_at
        self.active_at = None                       # tx superframe at which the unit switched on
        self._tx_tail = np.zeros(0)
        self.framer = Framer(0 if initiator else 1)
        self.rx = Receiver(self, 1 if initiator else 0)
        self.codecs = {kind: FrameCodec(mode) for kind, mode in M.MODES.items()}
        self.hs = session.Handshake(initiator)
        self.sealer = self.opener = None
        self.tx_sf = 0                              # superframes sent so far
        self.secure_at = None                       # tx superframe index when speech started
        self.peer_secure = False                    # a valid encrypted frame from the peer was seen
        self._ctrl_pos = 0
        self._ctrl_parts = {}
        self._ctrl_wait = 0
        # what the far end asked us to send, and when we last heard it say so
        self.peer_report = WANT_ROBUST
        self.heard_at = None                        # our tx superframe count when a valid encrypted
                                                    # frame last arrived (a clock that never stops)
        self._fresh = False                         # the newest window of audio carried a report
        self._lost_run = 0                          # windows in a row whose report was LOST
        self._anchor = None                         # (counter, stream time) of the newest accepted frame
        # our own view of the incoming direction, reported to the far end
        self.want = WANT_ROBUST
        self.rx_slots = 0                           # 480 ms windows of audio processed by our receiver
        self._hist = deque(maxlen=8)                # per window: (kind seen, frames ok, frames, share corrected)
        self._hold = {WANT_FAST: 0, WANT_ROBUST: 0}         # window before which a failed mode is not retried
        self._backoff = {WANT_FAST: 10, WANT_ROBUST: 6}
        self._floor = {WANT_FAST: None, WANT_ROBUST: None}  # lowest SDR seen since that mode failed
        self._clean_run = 0
        self._low = 0                               # windows in a row with the SDR too low for FAST
        self.voc_tx = {1200: Vocoder(1200), 700: Vocoder(700)}
        self.voc_rx = {1200: Vocoder(1200), 700: Vocoder(700)}
        self.sent_speech = {}                       # speech frame index -> bits sent
        self.got_speech = {}                        # speech frame index -> bits received
        self.heard = {}                             # speech frame index -> decoded int16 samples
        self.log = []                               # (tx superframe, event, detail)
        self._dual_start_detected = False
        self._peer_step0 = None                     # the step-0 payload from a peer also acting as initiator
        self._probe_rx = None                       # secondary receiver listening for same-role signals

    # =====================================================================================
    # transmit
    # =====================================================================================
    def next_superframe(self):
        """Audio for the next 480 ms."""
        sf = self.tx_sf
        self.tx_sf += 1
        if self.active_at is None:
            if sf >= self.start_at if self.start_at is not None else self.rx.mu is not None:
                self.active_at = sf
                self.log.append((sf, "on", "switched on" if self.start_at is not None else "heard the far end"))
            else:
                return self._speech(FRAMES_PER_SF * sf, FRAMES_PER_SF) / 32768.0      # plain call
        if sf - self.active_at < START_TRAIN:
            return self.framer.superframe(M.TRAIN)
        if self.secure_at is None:
            audio = self._handshake_superframe(sf)
            if audio is not None:
                return audio
        return self._speech_superframe(sf)

    def read(self, n):
        """The next n samples of audio for the phone, for callers that work in small blocks."""
        while len(self._tx_tail) < n:
            self._tx_tail = np.concatenate([self._tx_tail, self.next_superframe()])
        out, self._tx_tail = self._tx_tail[:n], self._tx_tail[n:]
        return out

    def _handshake_superframe(self, sf):
        hs = self.hs
        if hs.failed:
            return self.framer.superframe(M.TRAIN)
        # dual-start detection: both sides set as initiator, neither can hear the other
        # because both send role-0 and listen for role-1. After a timeout with no lock,
        # the unit with the lexicographically higher commit switches to responder.
        if (self.initiator and not self._dual_start_detected
                and self.rx.mu is None
                and sf - self.active_at >= DUAL_START_TIMEOUT
                and not hs.keys_ready):
            if self._probe_rx is not None and self._probe_rx.mu is not None:
                # probe detected a role-0 signal: a second initiator is out there
                self._switch_to_responder(sf)
                hs = self.hs
            elif self._probe_rx is None:
                self._probe_rx = Receiver(_DualStartProbe(), 0)
        if self.initiator and (hs.complete or self.peer_secure):
            self._go_secure(sf)
            return None
        if not self.initiator and hs.complete:
            self._ctrl_wait += 1
            if self.peer_secure or self._ctrl_wait > 20:
                self._go_secure(sf)
                return None
        out = hs.outgoing()
        answered = hs.keys_ready if self.initiator else hs.complete
        if out is None or (not answered and (sf - self.active_at) % 4 == 1):
            return self.framer.superframe(M.TRAIN)
        step, payload = out
        chunks = [payload[i:i + CTRL_DATA] for i in range(0, len(payload), CTRL_DATA)]
        index = self._ctrl_pos % len(chunks)
        self._ctrl_pos += 1
        body = bytes([step << 6 | index << 4 | len(chunks) << 2]) + chunks[index].ljust(CTRL_DATA, bytes(1))
        body += binascii.crc_hqx(body, 0xFFFF).to_bytes(2, "big")
        return self.framer.superframe(M.CONTROL, [self.codecs[M.CONTROL].encode(body)])

    def _switch_to_responder(self, sf):
        """Dual-start resolution: this initiator becomes the responder."""
        self._dual_start_detected = True
        self._probe_rx = None
        self.log.append((sf, "dual-start", "switching to responder"))
        self.initiator = False
        self.name = self.name + " (was initiator)"
        self.framer = Framer(1)
        self.rx = Receiver(self, 0)
        self.hs = session.Handshake(False)
        self._ctrl_pos = 0
        self._ctrl_parts = {}
        self.active_at = sf

    def _go_secure(self, sf):
        self.sealer, self.secure_at = self.hs.sealer(), sf
        if self.opener is None:
            self.opener = self.hs.opener()
        self.log.append((sf, "secure", self.hs.sas))

    def _deaf(self):
        return self.heard_at is None or self.tx_sf - self.heard_at > DEAF_AFTER

    def _tx_kind(self, sf):
        """What to send in this superframe once speech has started."""
        since = sf - self.secure_at
        if since < 6:
            return M.ROBUST                           # just secured: the far end is locked and listening,
                                                      # and its first reports predate our first frames
        if self._deaf():
            return _DEAF_CYCLE[since % len(_DEAF_CYCLE)]
        if self.peer_report == LOST:
            # One such report may be out of date by the time it arrives (the far end sent it
            # just before it heard us again); a second one means it.
            return _LOST_CYCLE[since % len(_LOST_CYCLE)] if self._lost_run >= 2 else M.ROBUST
        kind = _WANT_KIND[self.peer_report]
        if kind == M.FAST and not self._fresh:
            # The newest window brought no report. Whatever spoiled it (a codec rate drop,
            # say) has probably spoiled our own direction too, so do not wait to be told.
            return M.ROBUST
        return kind

    def _speech_superframe(self, sf):
        kind = self._tx_kind(sf)
        if not self.log or self.log[-1][1:] != ("mode", M.MODES[kind].name if kind != M.TRAIN else "train"):
            self.log.append((sf, "mode", M.MODES[kind].name if kind != M.TRAIN else "train"))
        if kind == M.TRAIN:
            return self.framer.superframe(M.TRAIN)
        mode = M.MODES[kind]
        status = self._report()
        frames = []
        since = sf - self.secure_at                     # frame counters start when speech starts
        for half in range(mode.frames):
            if mode.vocoder:
                first = FRAMES_PER_SF * since - self.lead + half * mode.speech_frames
                voc = self.voc_tx[mode.vocoder]
                plain = voc.encode(self._speech(first + FRAMES_PER_SF * self.secure_at, mode.speech_frames))
                for i, bits in enumerate(voc.split(plain, mode.speech_frames)):
                    self.sent_speech[first + i] = (mode.vocoder, bits)
            else:
                plain = bytes(mode.msg_bytes - 3)
            sealed = self.sealer.seal(2 * since + half, kind, status, plain)
            frames.append(self.codecs[kind].encode(sealed))
        return self.framer.superframe(kind, frames)

    def _speech(self, first, count):
        start, n = first * FRAME_SAMPLES, count * FRAME_SAMPLES
        out = np.zeros(n, dtype=np.int16)
        if self.speech is not None and start + n > 0:
            lo = max(start, 0)
            chunk = self.speech[lo:start + n]
            out[lo - start:lo - start + len(chunk)] = chunk
        return out

    # =====================================================================================
    # receive
    # =====================================================================================
    def push_audio(self, samples):
        self.rx.push(samples)
        if self._probe_rx is not None and not self._dual_start_detected:
            self._probe_rx.push(samples)

    def frame(self, kind, index, symbols, confidence, when):
        """Called by the receiver for each FEC frame; returns the corrected symbols if valid."""
        if kind == M.CONTROL:
            hit = self.codecs[kind].decode(
                symbols, confidence,
                lambda m: binascii.crc_hqx(m[:-2], 0xFFFF) == int.from_bytes(m[-2:], "big"))
            if hit is None:
                return None
            if not self.peer_secure:                  # once speech flows the key agreement is over
                self._control(hit[0])
            return hit[1]
        if self.opener is None and self.hs.keys_ready:
            self.opener = self.hs.opener()
        if self.opener is None:
            return None
        expected = None
        if self._anchor is not None:
            counter0, when0 = self._anchor
            expected = counter0 - counter0 % 2 + 2 * int(round((when - when0) / M.SF_SAMPLES)) + index
        hit = self.codecs[kind].decode(symbols, confidence,
                                       lambda m: self.opener.find(m, kind, expected) is not None)
        opened = self.opener.open(hit[0], kind, expected) if hit else None
        if opened is None:
            return None
        counter, status, plain = opened
        if self._anchor is None or counter >= self._anchor[0]:
            self._anchor = (counter, when)
            self.peer_secure, self.peer_report, self.heard_at = True, status, self.tx_sf
        mode = M.MODES[kind]
        if mode.vocoder:
            first = FRAMES_PER_SF * (counter // 2) - self.lead + (counter % 2) * mode.speech_frames
            voc = self.voc_rx[mode.vocoder]
            for i, bits in enumerate(voc.split(plain, mode.speech_frames)):
                self.got_speech[first + i] = (mode.vocoder, bits)
                self.heard[first + i] = voc.decode(bits)
        return hit[1]

    def _control(self, body):
        step, index, total = body[0] >> 6, (body[0] >> 4) & 3, (body[0] >> 2) & 3
        if total == 0 or index >= total:
            return
        parts = self._ctrl_parts.setdefault(step, {})
        parts[index] = body[1:1 + CTRL_DATA]
        if len(parts) == total:
            payload = b"".join(parts[i] for i in range(total))[:self.hs._step_bytes[step]]
            # dual-start: if we're an initiator and we see step 0 from the peer,
            # the peer is also an initiator
            if step == 0 and self.initiator and not self._dual_start_detected:
                self._peer_step0 = payload
            before = self.hs.outgoing()
            try:
                self.hs.receive(step, payload)
            except session.HandshakeError as exc:
                self.log.append((self.tx_sf, "handshake failed", str(exc)))
                return
            if self.hs.outgoing() != before:
                self._ctrl_pos = 0
                self.log.append((self.tx_sf, "handshake step received", step))

    def superframe(self, kind, when, info):
        """Called by the receiver once per 480 ms window of audio (kind None: nothing valid)."""
        self.rx_slots += 1
        seen = info.get("kind_seen")
        wear = [worn for valid, worn in info.get("frames", ()) if valid]
        self._fresh = kind in _WANT_KIND.values() and bool(wear)
        if self._fresh:
            self._lost_run = self._lost_run + 1 if self.peer_report == LOST else 0
        if seen in _WANT_KIND.values():
            self._hist.append((seen, len(wear), M.MODES[seen].frames, float(np.mean(wear)) if wear else 0.0))
        elif seen is None:
            self._hist.append((None, 0, 1, 0.0))
        self._decide(info.get("sdr_db"))

    # -- which mode can we receive? ---------------------------------------------------------
    def _decide(self, sdr):
        """Updates self.want from the receiver's signal-to-distortion ratio and recent results.

        A mode is dropped only on repeated failure, since single frames are lost to radio
        errors on any channel. A mode that failed is tried again after a hold-off that doubles
        each time, so a channel on the edge does not flap, or as soon as the channel has
        clearly improved since the failure."""
        recent = list(self._hist)
        for mode, floor in self._floor.items():
            if sdr is not None and self.want < mode:
                self._floor[mode] = sdr if floor is None else min(floor, sdr)

        def last(kind, n):
            return [h for h in recent if h[0] == kind][-n:]

        if self.want == WANT_FAST:
            fast = last(M.FAST, 3)
            failed = sum(1 for h in fast if h[1] < h[2])
            worn = len(fast) >= 2 and np.mean([h[3] for h in fast]) > 0.08
            silent = len(recent) >= 3 and all(h[0] is None for h in recent[-3:])
            self._low = self._low + 1 if sdr is not None and sdr < FAST_FLOOR_DB else 0
            if failed >= 2 or worn or silent or self._low >= 2:
                self._drop(WANT_FAST, 120)
        elif self.want == WANT_ROBUST:
            robust = last(M.ROBUST, 6)
            clean = len(recent) >= 4 and all(h[0] is not None and h[1] == h[2] and h[3] < 0.03 for h in recent[-4:])
            if len(robust) == 6 and all(h[1] == 0 for h in robust):
                self._drop(WANT_ROBUST, 60)
            elif sdr is not None and sdr >= FAST_SDR_DB and clean and self._may_retry(WANT_FAST, sdr, 2.5):
                self.want = WANT_FAST
        else:
            idle = last(M.IDLE, 2)
            got_robust = bool(recent) and recent[-1][0] == M.ROBUST and recent[-1][1] > 0
            if got_robust or (sdr is not None and sdr >= ROBUST_SDR_DB and idle and all(h[1] == h[2] for h in idle)
                              and self._may_retry(WANT_ROBUST, sdr, 3.0)):
                self.want = WANT_ROBUST
        if recent and recent[-1][0] is not None and recent[-1][1] == recent[-1][2]:
            self._clean_run += 1
            if recent[-1][0] == M.ROBUST:
                self._backoff[WANT_ROBUST] = 6           # it works: the next failure starts from scratch
            if self._clean_run >= 20 and recent[-1][0] == M.FAST:
                self._backoff[WANT_FAST] = 10
        else:
            self._clean_run = 0

    def _drop(self, mode, longest):
        """Stop asking for a mode that keeps failing."""
        self._hold[mode] = self.rx_slots + self._backoff[mode]
        self._backoff[mode] = min(2 * self._backoff[mode], longest)
        self._floor[mode] = None
        self.want = mode - 1
        self._clean_run = self._low = 0
        self._hist.clear()

    def _may_retry(self, mode, sdr, gain_db):
        floor = self._floor[mode]
        return self.rx_slots >= self._hold[mode] or (floor is not None and sdr >= floor + gain_db)

    def _report(self):
        return LOST if self._deaf() else self.want
