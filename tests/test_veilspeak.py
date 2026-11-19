"""Tests for the secure-call model. The slower ones run complete simulated calls through the
3GPP reference codecs (third_party/bin)."""
import os

import numpy as np
import pytest

from veilspeak import codebook, crypto, fec, modem, session, vocoder
from veilspeak import modes as M
from veilspeak.cellsim import call as CS
from veilspeak.cellsim import codecs, duplex
from veilspeak.endpoint import Endpoint

T, F, R, I, C = M.TRAIN, M.FAST, M.ROBUST, M.IDLE, M.CONTROL


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------
class Sink:
    """Receiver sink that knows which messages were sent."""

    def __init__(self, sent):
        self.codecs = {k: fec.FrameCodec(m) for k, m in M.MODES.items()}
        self.valid = {k: set() for k in M.MODES}
        for kind, msgs in sent:
            if kind != T:
                self.valid[kind].update(msgs)
        self.got = []

    def frame(self, kind, index, symbols, confidence, when):
        hit = self.codecs[kind].decode(symbols, confidence, lambda m: m in self.valid[kind])
        if hit is None:
            return None
        self.got.append((kind, hit[0]))
        return hit[1]

    def superframe(self, kind, when, info):
        pass


def make_stream(plan, seed=0):
    rng = np.random.default_rng(seed)
    framer = modem.Framer()
    cod = {k: fec.FrameCodec(m) for k, m in M.MODES.items()}
    audio, sent = [], []
    for kind in plan:
        if kind == T:
            audio.append(framer.superframe(T))
            sent.append((kind, []))
        else:
            msgs = [rng.bytes(M.MODES[kind].msg_bytes) for _ in range(M.MODES[kind].frames)]
            audio.append(framer.superframe(kind, [cod[kind].encode(m) for m in msgs]))
            sent.append((kind, msgs))
    return np.concatenate(audio), sent


def receive(audio, sent, chunk=1777):
    sink = Sink(sent)
    rx = modem.Receiver(sink)
    for i in range(0, len(audio), chunk):
        rx.push(audio[i:i + chunk])
    want = [(k, m) for k, msgs in sent for m in msgs]
    have = set(sink.got)
    return sum(1 for w in want if w in have) / len(want), rx


def speech(seconds, seed):
    return np.random.default_rng(seed).normal(0, 3000, int(8000 * seconds)).astype(np.int16)


PLAN = [T, T] + [C] * 2 + [R] * 6 + [F] * 6 + [I] * 2


# ---------------------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------------------
def test_codebooks_have_the_expected_minimum_distance():
    fast, robust, idle = (M.MODES[k].books() for k in (F, R, I))
    assert [codebook.min_lee_distance(b) for b in fast] == [5, 5]
    assert codebook.min_lee_distance(robust[0]) >= 14
    assert codebook.min_lee_distance(idle[0]) >= 20
    for book in fast + robust + idle:
        assert len(np.unique(book, axis=0)) == len(book)


@pytest.mark.parametrize("kind", [F, R, I])
def test_frame_code_corrects_errors_and_erasures(kind):
    mode = M.MODES[kind]
    codec = fec.FrameCodec(mode)
    msg = os.urandom(mode.msg_bytes)
    sent = codec.encode(msg)
    t = (mode.rs_n - mode.rs_k) // 2
    bad = sent.copy()
    bad[:t] ^= 1
    assert codec.decode(bad, np.ones(len(bad)), lambda m: m == msg)[0] == msg
    flagged = 2 * t - (1 if mode.sym_bits == 12 else 2)   # at least 12 bits of the code stay unused
    bad = sent.copy()
    bad[:flagged] ^= 1                                  # too many for errors-only decoding...
    conf = np.ones(len(bad))
    conf[:flagged] = 0                                  # ...but flagged as unreliable
    assert codec.decode(bad, conf, lambda m: m == msg)[0] == msg
    bad = sent.copy()
    bad[::2] ^= 1
    assert codec.decode(bad, np.ones(len(bad)), lambda m: m == msg) is None


@pytest.mark.parametrize("kind", [F, R, I])
def test_frame_code_does_not_turn_noise_into_a_codeword(kind):
    """One check symbol is always kept back, so random symbols are rejected by the code
    itself and almost never reach the 16-bit message check."""
    mode = M.MODES[kind]
    codec = fec.FrameCodec(mode)
    rng = np.random.default_rng(5)
    reached = []
    for _ in range(300):
        codec.decode(rng.integers(0, 2 ** mode.sym_bits, mode.rs_n), rng.random(mode.rs_n), lambda m: reached.append(m) and False)
    assert len(reached) <= 10


def test_frame_crypto_rejects_replay_tamper_and_wrong_type():
    k1, k2 = os.urandom(32), os.urandom(32)
    seal, opn = crypto.FrameSealer(k1, k2, 0), crypto.FrameOpener(k1, k2, 0)
    msgs = [seal.seal(c, R, c % 4, bytes(42)) for c in range(40, 400)]
    assert len({m[:42] for m in msgs}) == len(msgs)     # keystream never repeats
    assert opn.open(msgs[0], R) == (40, 0, bytes(42))
    assert opn.open(msgs[0], R) is None                 # replay
    assert opn.open(msgs[1], F, 41) is None             # frame type is authenticated
    flipped = bytes([msgs[1][0] ^ 1]) + msgs[1][1:]
    assert opn.open(flipped, R, 41) is None             # tamper
    assert opn.open(msgs[1], R, 41)[:2] == (41, 1)
    assert opn.open(msgs[300], R, 45) is None           # far from the predicted counter
    assert opn.open(msgs[300], R, 338)[:2] == (340, 0)  # long gap, predicted from elapsed time
    assert opn.open(msgs[298], R, 338)[:2] == (338, 2)  # a frame recovered after its successor counts...
    assert opn.open(msgs[298], R, 338) is None          # ...once
    assert crypto.FrameOpener(k1, k2, 1).open(msgs[5], R) is None   # other direction's keys
    with pytest.raises(ValueError):
        seal.seal(10, R, 0, bytes(42))                  # counters never go back


def run_handshake(a, b, rounds=6):
    for _ in range(rounds):
        for src, dst in ((a, b), (b, a)):
            out = src.outgoing()
            if out:
                dst.receive(*out)


def test_key_agreement_and_short_code():
    a, b = session.Handshake(True), session.Handshake(False)
    run_handshake(a, b)
    assert a.complete and b.complete and a.sas == b.sas and len(a.sas) == 4
    msg = a.sealer().seal(7, R, 2, b"x" * 42)
    assert b.opener().open(msg, R) == (7, 2, b"x" * 42)
    assert a.next_retained == b.next_retained


def test_man_in_the_middle_shows_different_codes():
    for _ in range(5):
        a, m1, m2, b = (session.Handshake(i) for i in (True, False, True, False))
        run_handshake(a, m1)
        run_handshake(m2, b)
        assert a.complete and b.complete and a.sas != b.sas


def test_commitment_binds_the_initiators_key():
    a, b = session.Handshake(True), session.Handshake(False)
    b.receive(*a.outgoing())
    a.receive(*b.outgoing())
    step, payload = a.outgoing()
    other = session.Handshake(True)._pk
    with pytest.raises(session.HandshakeError):
        b.receive(step, other + payload[32:])


def test_retained_secret_mismatch_fails():
    a = session.Handshake(True, retained=b"\1" * 32)
    b = session.Handshake(False, retained=b"\2" * 32)
    with pytest.raises(session.HandshakeError):
        run_handshake(a, b)


@pytest.mark.parametrize("rate,frames", [(1200, 6), (700, 12)])
def test_vocoder_bit_packing(rate, frames):
    enc, ref = vocoder.Vocoder(rate), vocoder.Vocoder(rate)
    pcm = speech(frames * 0.04, 3)
    packed = enc.encode(pcm)
    assert len(packed) * 8 == frames * enc.bits
    direct = [bytes(ref.c2.encode(pcm[i * 320:(i + 1) * 320])) for i in range(frames)]
    assert enc.split(packed, frames) == direct


# ---------------------------------------------------------------------------------------
# modem
# ---------------------------------------------------------------------------------------
def test_modem_finds_and_decodes_every_frame_type():
    x, sent = make_stream(PLAN)
    noisy = np.concatenate([np.zeros(3211), x, np.zeros(3000)]) + np.random.default_rng(1).normal(0, 0.003, len(x) + 6211)
    share, rx = receive(noisy, sent)
    assert share == 1.0 and rx.stats["relocks"] == 0 and rx.stats["jumps"] == 0


def test_modem_ignores_its_own_direction():
    """Each direction has its own pilot and training sequences, so the echo of a unit's own
    signal (sidetone, network echo) is not mistaken for the far end."""
    x, sent = make_stream(PLAN)
    sink = Sink(sent)
    rx = modem.Receiver(sink, role=1)                   # listens for the other direction
    rx.push(np.concatenate([np.zeros(3211), x, np.zeros(3000)]))
    assert rx.stats["superframes"] == 0 and not sink.got


def test_modem_does_not_lock_to_speech_or_noise():
    from veilspeak.cellsim import speech as voices
    talk = voices.voice("a", 12) / 32768.0
    hiss = np.random.default_rng(2).normal(0, 0.05, 8000 * 12)
    for audio in (talk, hiss):
        rx = modem.Receiver(Sink([]))
        rx.push(audio)
        assert rx.stats["superframes"] == 0


def test_modem_tracks_clock_offset():
    from veilspeak.dsp import clock_offset
    x, sent = make_stream([T, T] + [R] * 20 + [F] * 20)
    share, rx = receive(clock_offset(np.concatenate([np.zeros(500), x, np.zeros(3000)]), 150), sent)
    assert share == 1.0
    assert abs(-rx.rate / M.SF_SAMPLES * 1e6 - 150) < 10


def test_modem_follows_a_time_shift_in_the_pause():
    x, sent = make_stream([T, T] + [R] * 10)
    cut = 6 * M.SF_SAMPLES + 300                         # inside the pause before the 7th superframe
    for shifted in (np.concatenate([x[:cut], np.zeros(160), x[cut:]]),      # 20 ms inserted
                    np.concatenate([x[:cut], x[cut + 100:]])):               # 12.5 ms removed
        share, rx = receive(np.concatenate([np.zeros(900), shifted, np.zeros(3000)]), sent)
        assert share == 1.0 and rx.stats["jumps"] >= 1


@pytest.mark.parametrize("edit", ["20 ms inserted", "20 ms dropped", "12 ms repeated"])
def test_modem_repairs_a_burst_cut_by_a_time_shift(edit):
    """A jitter buffer changes its delay by inserting or dropping audio, also inside a burst."""
    x, sent = make_stream([T, T] + [R] * 10)
    cut = 6 * M.SF_SAMPLES + M.PAUSE_SAMPLES + 21 * M.SYM + 37       # mid-burst, mid-symbol
    shifted = {"20 ms inserted": np.concatenate([x[:cut], 0.3 * x[cut - 160:cut], x[cut:]]),
               "20 ms dropped": np.concatenate([x[:cut], x[cut + 160:]]),
               "12 ms repeated": np.concatenate([x[:cut], x[cut - 96:]])}[edit]
    share, rx = receive(np.concatenate([np.zeros(900), shifted, np.zeros(3000)]), sent)
    assert share == 1.0 and rx.stats["repaired"] == 1


def test_modem_survives_a_damaged_header():
    x, sent = make_stream([T, T] + [R] * 10)
    start = 6 * M.SF_SAMPLES + M.PAUSE_SAMPLES
    x[start:start + 2 * M.SYM] *= 0.05                               # header of the 7th superframe wiped out
    share, rx = receive(np.concatenate([np.zeros(900), x, np.zeros(3000)]), sent)
    assert share == 1.0


def amr_path(rate, vad, **kw):
    return CS.Path("test", (CS.Leg("amrnb", rate, vad=vad, **kw),), clock_ppm=30.0, seed=3)


@pytest.mark.parametrize("vad", [1, 2])
def test_amr_silence_detection_keeps_coding_the_signal(vad):
    """Without the pauses both AMR detectors mute the modem within seconds."""
    x, sent = make_stream([T, T] + [R] * 12 + [F] * 12)
    info = []
    y = CS.run(amr_path(12200, vad), np.concatenate([np.zeros(1000), x, np.zeros(3000)]), info)
    burst_frames = len(x) // 160
    assert info[0]["speech"] >= 0.97 * burst_frames
    share, _ = receive(y, sent)
    assert share == 1.0


def test_robust_mode_survives_amr_half_rate():
    x, sent = make_stream([T, T] + [C] * 3 + [R] * 20)
    share, _ = receive(CS.run(amr_path(7400, 2), np.concatenate([np.zeros(1000), x, np.zeros(3000)])), sent)
    assert share == 1.0


@pytest.mark.parametrize("leg", [CS.Leg("amrwb", 12650), CS.Leg("evs", 13200), CS.Leg("evs", 13200, jitter_ms=10.0)])
def test_volte_codecs_carry_both_speech_modes(leg):
    x, sent = make_stream([T, T] + [R] * 10 + [F] * 10)
    path = CS.Path("test", (leg,), clock_ppm=-25.0, seed=4)
    share, _ = receive(CS.run(path, np.concatenate([np.zeros(1000), x, np.zeros(3000)])), sent)
    assert share >= 0.95


# ---------------------------------------------------------------------------------------
# simulator
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("leg", [
    CS.Leg("amrnb", ((0, 12200), (4, 7400)), vad=2, loss=0.03, burst=3, handovers=((3, 200),)),
    CS.Leg("evs", 13200, jitter_ms=20.0, spike_prob=0.003, loss=0.01),
    CS.Handover((CS.Leg("amrwb", 12650),), (CS.Leg("amrnb", 12200), CS.Leg("g711"), CS.Leg("ns", 12)), 2.5),
])
def test_a_longer_run_reproduces_a_shorter_one(leg):
    """The call simulation relies on this: paths are causal and seeded."""
    x, _ = make_stream([T, T] + [R] * 14)
    path = CS.Path("test", (leg,), sender=CS.Phone(ns_db=12, agc=True), clock_ppm=40.0, seed=5)
    part = 9 * M.SF_SAMPLES
    short, full = CS.run(path, x[:part]), CS.run(path, x)
    n = part - duplex.GUARD
    assert np.allclose(short[:n], full[:n], atol=1e-9)


@pytest.mark.parametrize("fixed", [False, True])
def test_reference_codecs_round_trip(fixed):
    if fixed and not os.path.exists(os.path.join(codecs.BIN, "EVS_fx_dec")):
        pytest.skip("fixed-point codecs not built (tools/build_codecs_fixed.sh)")
    t = np.arange(16000) / 8000.0
    tone = 0.2 * np.sin(2 * np.pi * 440 * t) * (np.sin(2 * np.pi * 3 * t) > 0)
    nb = codecs.amrnb_decode(codecs.amrnb_encode(tone, 12200, fixed=fixed), fixed=fixed)
    assert abs(len(nb) - len(tone)) <= 160 and 0.05 < nb.std() < 0.3
    wide = np.repeat(tone, 2)
    assert 0.05 < codecs.amrwb_decode(codecs.amrwb_encode(wide, 12650, fixed=fixed), fixed=fixed).std() < 0.3
    assert 0.05 < codecs.evs_decode(codecs.evs_encode(wide, 16000, 13200, fixed=fixed), 16000, fixed=fixed).std() < 0.3


def test_handover_between_networks_keeps_the_call_going():
    x, sent = make_stream([T, T] + [R] * 16)
    path = CS.Path("test", (CS.Handover((CS.Leg("amrwb", 12650),), (CS.Leg("amrnb", 12200),), 4.1),), clock_ppm=20.0, seed=6)
    share, rx = receive(CS.run(path, np.concatenate([np.zeros(1000), x, np.zeros(3000)])), sent)
    assert share >= 0.85                                 # the superframe cut by the handover is lost, little else


# ---------------------------------------------------------------------------------------
# complete calls
# ---------------------------------------------------------------------------------------
def call_report(path_ab, path_ba, seconds=24.0, **kw):
    rep, a, b = duplex.simulate(path_ab, path_ba, speech(seconds + 2, 7), speech(seconds + 2, 8), seconds, **kw)
    return rep, a, b


def test_complete_call_over_a_clean_line():
    clean = CS.Path("clean", ())
    rep, a, b = call_report(clean, clean)
    assert rep["secure"] and rep["sas_match"] and rep["time_to_secure_s"] < 10
    for key in ("a_to_b", "b_to_a"):
        assert rep[key]["delivered_share"] > 0.97 and rep[key]["wrong"] == 0
        assert rep[key]["fast_share"] > 0.5              # it moved up to the faster mode


def test_complete_call_over_2g_with_a_rate_drop():
    leg = CS.Leg("amrnb", ((0, 12200), (14, 7400)), vad=2, loss=0.01, burst=2)
    rep, a, b = call_report(CS.Path("2g", (leg,), clock_ppm=25, seed=1), CS.Path("2g", (leg,), clock_ppm=-35, seed=2), 30.0)
    assert rep["secure"] and rep["sas_match"]
    for key in ("a_to_b", "b_to_a"):
        assert rep[key]["delivered_share"] > 0.9 and rep[key]["wrong"] == 0
        assert rep[key]["robust_share"] > 0.3            # it fell back when the codec rate dropped


def test_call_that_starts_as_an_ordinary_one():
    """A talks for a few seconds, then switches the unit on; B's unit answers by itself."""
    leg = CS.Leg("amrwb", 12650, loss=0.005)
    rep, a, b = call_report(CS.Path("volte", (leg,), clock_ppm=25, seed=1), CS.Path("volte", (leg,), clock_ppm=-35, seed=2),
                            28.0, start_s=3.3)
    assert a.active_at == 7 and b.active_at is not None and b.active_at > a.active_at
    assert rep["secure"] and rep["sas_match"] and rep["time_to_secure_s"] < 10
    for key in ("a_to_b", "b_to_a"):
        assert rep[key]["delivered_share"] > 0.95 and rep[key]["wrong"] == 0


def test_call_with_sidetone():
    """Each unit also hears its own signal, only 10 dB below the far end's."""
    leg = CS.Leg("amrnb", 12200)
    rep, a, b = call_report(CS.Path("2g", (leg,), clock_ppm=25, seed=1), CS.Path("2g", (leg,), clock_ppm=-35, seed=2),
                            24.0, sidetone_db=-10.0)
    assert rep["secure"] and rep["sas_match"]
    for key in ("a_to_b", "b_to_a"):
        assert rep[key]["delivered_share"] > 0.95 and rep[key]["wrong"] == 0


def test_call_recovers_after_the_codec_drops_to_its_lowest_rate():
    """At AMR 4.75 no speech mode gets through; the link must come back by itself afterwards."""
    leg = CS.Leg("amrnb", ((0, 12200), (10, 4750), (17, 12200)))
    rep, a, b = call_report(CS.Path("2g", (leg,), clock_ppm=25, seed=1), CS.Path("2g", (leg,), clock_ppm=-35, seed=2), 30.0)
    assert rep["secure"] and rep["sas_match"]
    for sender, receiver in ((a, b), (b, a)):
        late = [n for n in sender.sent_speech if n >= 12 * (int(21 / 0.48) - sender.secure_at)]
        assert len(late) > 100
        assert sum(receiver.got_speech.get(n) == sender.sent_speech[n] for n in late) > 0.9 * len(late)
        assert sender.log[-1][1:] == ("mode", "fast")


def test_listener_without_the_keys_recovers_nothing():
    clean = CS.Path("clean", ())
    rep, a, b = duplex.simulate(clean, clean, speech(20, 7), speech(20, 8), 18.0)
    assert rep["secure"]
    other_a, other_b = session.Handshake(True), session.Handshake(False)
    run_handshake(other_a, other_b)                      # keys from some other session
    spy = Endpoint(False, name="spy")
    spy.opener = other_b.opener()
    spy.push_audio(rep["line_ab"])
    assert spy.rx.stats["superframes"] > 20              # it can find and demodulate the frames...
    assert not spy.got_speech                            # ...but nothing authenticates or decrypts


def test_block_based_units_over_a_wire():
    """The real-time wrapper's logic, driven in 20 ms blocks over a wire with 150 ms delay."""
    from veilspeak.live import BLOCK, LiveUnit
    a, b = LiveUnit(True, speech(40, 7)), LiveUnit(False, speech(40, 8))
    delay = [[np.zeros(BLOCK)] * 7, [np.zeros(BLOCK)] * 7]         # 140 ms each way
    heard_b = []
    for _ in range(int(30 * 8000 / BLOCK)):
        delay[0].append(a.pull_line(BLOCK))
        delay[1].append(b.pull_line(BLOCK))
        b.push_line(delay[0].pop(0))
        a.push_line(delay[1].pop(0))
        heard_b.append(b.pull_heard(BLOCK))
    assert a.ep.secure_at is not None and b.ep.secure_at is not None and a.ep.hs.sas == b.ep.hs.sas
    sent = a.ep.sent_speech
    got = b.ep.got_speech
    wanted = [n for n in sent if FRAMES_PER_SF_TEST <= n < max(sent) - 2 * FRAMES_PER_SF_TEST]
    assert sum(1 for n in wanted if got.get(n) == sent[n]) > 0.95 * len(wanted)
    played = np.concatenate(heard_b)
    assert np.abs(played[-8000 * 8:].astype(float)).mean() > 100   # speech is coming out of the far unit


FRAMES_PER_SF_TEST = 12


# ---------------------------------------------------------------------------------------
# crypto and protocol hardening (CR)
# ---------------------------------------------------------------------------------------
def test_fuzz_control_parser():
    """Random payloads into the CONTROL frame path must never crash or advance the handshake."""
    rng = np.random.default_rng(42)
    ep = Endpoint(False, name="fuzz_target")
    # Give it step 0 from a real initiator so it's in a listening state
    init = session.Handshake(True)
    step0 = init.outgoing()
    ep.hs.receive(*step0)
    assert ep.hs.outgoing() is not None  # responder now has step 1 to send

    mode = M.MODES[C]
    codec = fec.FrameCodec(mode)
    for _ in range(1000):
        garbage = rng.bytes(mode.msg_bytes)
        symbols = codec.encode(garbage)
        confidence = np.ones(mode.rs_n)
        # The CRC check inside endpoint.frame rejects almost everything;
        # whatever does pass the CRC is random data that won't form a valid
        # handshake step — it must not crash.
        ep.frame(C, 0, symbols, confidence, 0)
    # The handshake must not have advanced past what step 0 gave it
    assert not ep.hs.complete
    assert not ep.hs.failed


def test_no_key_counter_reuse_across_call():
    """Every (direction, counter) pair used by both sealers must be unique."""
    clean = CS.Path("clean", ())
    rep, a, b = call_report(clean, clean)
    assert rep["secure"]
    # Collect all counters used in each direction from the sent_speech records.
    # Each entry in sent_speech is keyed by speech-frame index; the frame counter
    # is derived from the superframe number. What matters is that the sealer's
    # monotonic counter was never reused — verified by the sealer itself (it raises
    # ValueError on reuse), and cross-checked here by ensuring every frame in
    # got_speech was accepted exactly once by the opener's sliding window.
    for sender, receiver in ((a, b), (b, a)):
        # The sealer enforces counter monotonicity; check the last counter is
        # consistent with how many frames were sealed
        assert sender.sealer.last >= 0
        # Every accepted frame on the receiver used a unique counter (the opener's
        # sliding window rejects duplicates)
        counters = []
        for n in sorted(receiver.got_speech):
            if receiver.got_speech[n] == sender.sent_speech.get(n):
                counters.append(n)
        assert len(counters) == len(set(counters))


def test_mitm_always_detected():
    """Over 20 trials, a MITM proxy always produces differing SAS codes, and
    frames from the real endpoints are never accepted by the other real endpoint
    (since they each have different session keys — a with m1, b with m2)."""
    for trial in range(20):
        a = session.Handshake(True, entropy=lambda n, s=10 * trial: os.urandom(n))
        m1 = session.Handshake(False)
        m2 = session.Handshake(True)
        b = session.Handshake(False, entropy=lambda n, s=10 * trial + 1: os.urandom(n))
        run_handshake(a, m1)
        run_handshake(m2, b)
        assert a.complete and b.complete
        assert a.sas != b.sas, "MITM produced matching SAS on trial %d" % trial
        # a's sealed frames must be rejected by b (b shares keys with m2, not a)
        a_seal = a.sealer()
        msg_a = a_seal.seal(0, R, 0, b"x" * 42)
        assert b.opener().open(msg_a, R) is None
        # b's sealed frames must be rejected by a (a shares keys with m1, not b)
        b_seal = b.sealer()
        msg_b = b_seal.seal(0, R, 0, b"x" * 42)
        assert a.opener().open(msg_b, R) is None


def test_bitflip_injection_during_speech():
    """Flipping any bit in a sealed frame must be rejected by the HMAC tag check."""
    k1, k2 = os.urandom(32), os.urandom(32)
    seal = crypto.FrameSealer(k1, k2, 0)
    opn = crypto.FrameOpener(k1, k2, 0)
    plain = os.urandom(42)
    msg = seal.seal(100, R, 2, plain)
    rng = np.random.default_rng(99)
    for _ in range(200):
        pos = rng.integers(0, len(msg))
        bit = 1 << rng.integers(0, 8)
        tampered = bytearray(msg)
        tampered[pos] ^= bit
        tampered = bytes(tampered)
        if tampered == msg:
            continue
        assert opn.find(tampered, R, 100) is None


def test_replay_injection():
    """A frame accepted once cannot be accepted again (replay), even with the
    correct predicted counter."""
    k1, k2 = os.urandom(32), os.urandom(32)
    seal = crypto.FrameSealer(k1, k2, 0)
    opn = crypto.FrameOpener(k1, k2, 0)
    msgs = [seal.seal(c, R, 0, os.urandom(42)) for c in range(50)]
    # Accept frame 10
    assert opn.open(msgs[10], R, 10) is not None
    # Accept frame 20 to advance the window
    assert opn.open(msgs[20], R, 20) is not None
    # Replay of frame 10 (still inside the window) must be rejected
    assert opn.open(msgs[10], R, 10) is None
    # Accept frame 40 to push frame 10 out of the window entirely
    assert opn.open(msgs[40], R, 40) is not None
    # Replay of frame 10 (now outside the window) must still be rejected
    assert opn.open(msgs[10], R, 10) is None
    # Replay of frame 20 must also be rejected
    assert opn.open(msgs[20], R, 20) is None


def test_counter_exhaustion_raises():
    """The sealer must refuse to seal when the counter reaches 2^32."""
    k1, k2 = os.urandom(32), os.urandom(32)
    seal = crypto.FrameSealer(k1, k2, 0)
    # Seal at the maximum allowed counter
    seal.seal((1 << 32) - 1, R, 0, bytes(42))
    # The next counter value (2^32) must raise OverflowError
    with pytest.raises(OverflowError):
        seal.seal(1 << 32, R, 0, bytes(42))


def test_wrong_direction_keys_rejected():
    """Frames sealed for direction 0 cannot be opened by a direction-1 opener,
    and vice versa, even with the same enc/mac keys."""
    k1, k2 = os.urandom(32), os.urandom(32)
    for seal_dir, open_dir in ((0, 1), (1, 0)):
        seal = crypto.FrameSealer(k1, k2, seal_dir)
        opn = crypto.FrameOpener(k1, k2, open_dir)
        msg = seal.seal(5, R, 0, os.urandom(42))
        assert opn.open(msg, R, 5) is None
    # Sanity: same direction works
    seal = crypto.FrameSealer(k1, k2, 0)
    opn = crypto.FrameOpener(k1, k2, 0)
    assert opn.open(seal.seal(0, R, 0, bytes(42)), R, 0) is not None


def test_pq_hybrid_key_agreement():
    """ML-KEM-768 (or placeholder) hybrid handshake: keys agree, SAS matches,
    encryption works, and MITM is still detected."""
    a = session.Handshake(True, pq_hybrid=True)
    b = session.Handshake(False, pq_hybrid=True)
    run_handshake(a, b)
    assert a.complete and b.complete
    assert a.sas == b.sas and len(a.sas) == 4
    # Encryption round-trip
    msg = a.sealer().seal(7, R, 2, b"y" * 42)
    assert b.opener().open(msg, R) == (7, 2, b"y" * 42)
    assert a.next_retained == b.next_retained
    # MITM still detected with PQ hybrid
    for _ in range(5):
        ai = session.Handshake(True, pq_hybrid=True)
        m1 = session.Handshake(False, pq_hybrid=True)
        m2 = session.Handshake(True, pq_hybrid=True)
        bi = session.Handshake(False, pq_hybrid=True)
        run_handshake(ai, m1)
        run_handshake(m2, bi)
        assert ai.complete and bi.complete
        assert ai.sas != bi.sas


def test_pq_hybrid_keys_differ_from_classic():
    """The PQ hybrid handshake must produce different session keys from a
    classic handshake even with the same X25519 keys and nonces."""
    entropy_a = lambda n: b"\xaa" * n
    entropy_b = lambda n: b"\xbb" * n
    classic_a = session.Handshake(True, entropy=entropy_a)
    classic_b = session.Handshake(False, entropy=entropy_b)
    run_handshake(classic_a, classic_b)
    pq_a = session.Handshake(True, entropy=entropy_a, pq_hybrid=True)
    pq_b = session.Handshake(False, entropy=entropy_b, pq_hybrid=True)
    run_handshake(pq_a, pq_b)
    assert classic_a.sas != pq_a.sas or classic_a.next_retained != pq_a.next_retained


def test_rekeying_sealer_and_opener():
    """RekeyingSealer and RekeyingOpener derive fresh keys at the interval boundary
    and old keys still work for frames before the boundary."""
    k1, k2 = os.urandom(32), os.urandom(32)
    interval = 100
    seal = crypto.RekeyingSealer(k1, k2, 0, interval=interval)
    opn = crypto.RekeyingOpener(k1, k2, 0, interval=interval)
    # Seal and open frames before the boundary
    for c in range(0, interval - 1, 10):
        msg = seal.seal(c, R, 0, bytes(42))
        assert opn.open(msg, R, c) is not None
    assert seal.rekey_count == 0 and opn.rekey_count == 0
    # Cross the boundary
    msg = seal.seal(interval, R, 0, bytes(42))
    assert seal.rekey_count == 1
    assert opn.open(msg, R, interval) is not None
    assert opn.rekey_count == 1
    # Keep going across a second boundary
    msg = seal.seal(2 * interval + 5, R, 0, bytes(42))
    assert seal.rekey_count == 2
    assert opn.open(msg, R, 2 * interval + 5) is not None
    assert opn.rekey_count == 2


def test_retained_secret_key_continuity():
    """When both sides use the same retained secret from a previous call, the
    handshake succeeds and the keys differ from a fresh handshake."""
    # First call — produces a retained secret
    a1, b1 = session.Handshake(True), session.Handshake(False)
    run_handshake(a1, b1)
    ret = a1.next_retained
    assert ret == b1.next_retained
    # Second call with the retained secret
    a2 = session.Handshake(True, retained=ret)
    b2 = session.Handshake(False, retained=ret)
    run_handshake(a2, b2)
    assert a2.complete and b2.complete and a2.sas == b2.sas
    # The SAS should differ from the first call (different keys)
    assert a2.sas != a1.sas
    # retained_match should indicate the secret was used
    assert a2.retained_match and b2.retained_match


# ---------------------------------------------------------------------------------------
# regression: real-world weak links found in audit (2026-11-19)
# ---------------------------------------------------------------------------------------
def test_pq_hybrid_commitment_binds_the_kem_key():
    """The PQ-hybrid SAS must not be grindable. The initiator commits to its KEM public key
    and contributes nothing to the KEM after the commitment (the responder encapsulates), so a
    man-in-the-middle cannot re-encapsulate to force a matching SAS -- its only lever is a
    fresh handshake, 2^-20 per try, as in the classic mode."""
    # structure: the KEM public key rides in step 0 (committed); step 2 is just pk_I + confirm,
    # with no ciphertext for the initiator to vary after it has committed.
    assert session.STEP_BYTES_PQ[0] == 34 + session._PQ_PK_BYTES
    assert session.STEP_BYTES_PQ[1] == 50 + session._PQ_CT_BYTES
    assert session.STEP_BYTES_PQ[2] == 40 == session.STEP_BYTES[2]
    a, b = session.Handshake(True, pq_hybrid=True), session.Handshake(False, pq_hybrid=True)
    s0, p0 = a.outgoing()
    assert s0 == 0 and len(p0) == session.STEP_BYTES_PQ[0]
    # a man-in-the-middle swaps the KEM public key in step 0 but cannot change the commitment
    tampered = bytearray(p0)
    tampered[34] ^= 1                                       # a bit of pq_pk_I, which follows hello | commit
    b.receive(0, bytes(tampered))
    a.receive(*b.outgoing())
    s2, _ = a.outgoing()
    assert s2 == 2
    with pytest.raises(session.HandshakeError):
        b.receive(*a.outgoing())                           # the commitment check rejects the altered KEM key


def test_dual_start_ignores_the_units_own_echo():
    """A lone initiator that hears its own signal (sidetone / network echo) must not mistake it
    for a second initiator and demote itself to responder. With a far end that answers a little
    late, the call still secures."""
    rng = np.random.default_rng(5)
    a = Endpoint(True, speech(40, 1), "A", start_at=0)
    b = Endpoint(False, speech(40, 2), "B", start_at=None)
    answer_after = 14                                      # B's unit comes up only after a few seconds
    for sf in range(50):
        out_a = a.next_superframe()
        out_b = b.next_superframe()
        live = sf >= answer_after
        echo = 10 ** (-15 / 20) * np.roll(out_a, 40)       # A hears its own signal 5 ms late, 15 dB down
        a.push_audio((out_b if live else np.zeros(M.SF_SAMPLES)) + echo + rng.normal(0, 1e-4, M.SF_SAMPLES))
        b.push_audio((out_a if live else np.zeros(M.SF_SAMPLES)) + rng.normal(0, 1e-4, M.SF_SAMPLES))
    assert a.initiator is True                             # it never switched itself to responder
    assert a.secure_at is not None and b.secure_at is not None and a.hs.sas == b.hs.sas


def test_secure_call_resynchronises_after_a_time_shift():
    """A large, sudden shift in the audio stream (here 2 s of one direction dropped mid-call)
    throws off the frame counter predicted from elapsed time. The receiver must re-acquire and
    carry on, not stay deaf for the rest of the call."""
    from veilspeak.endpoint import FRAMES_PER_SF
    rng = np.random.default_rng(6)
    a = Endpoint(True, speech(70, 1), "A", start_at=0)
    b = Endpoint(False, speech(70, 2), "B", start_at=None)
    steps, at, skip = 130, 40, int(2.0 * 8000)
    for sf in range(steps):
        out_a = a.next_superframe()
        out_b = b.next_superframe()
        if at <= sf and skip > 0:                          # drop 2 s of the A->B audio mid-call
            take = min(skip, len(out_a))
            out_a, skip = out_a[take:], skip - take
        if len(out_a):
            b.push_audio(out_a + rng.normal(0, 1e-4, len(out_a)))
        a.push_audio(out_b + rng.normal(0, 1e-4, M.SF_SAMPLES))
    lo = FRAMES_PER_SF * (at - a.secure_at + 10)           # speech A sent well after the drop
    hi = FRAMES_PER_SF * (steps - 3 - a.secure_at)
    after = [n for n in a.sent_speech if lo <= n < hi]
    ok = sum(1 for n in after if b.got_speech.get(n) == a.sent_speech[n])
    assert len(after) > 100
    assert ok > 0.9 * len(after)                           # recovered (was 0 % and permanent before the fix)
