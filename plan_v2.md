# Veilspeak v2 — Secure Voice over Any Voice Channel

**Build plan · 2026-11-06 · supersedes `plan.md` and `IMPLEMENTATION.md` wherever they conflict**

---

## 0. Summary

**Goal.** Two people, each with a small add-on unit (a laptop in the prototype), hold a real-time,
end-to-end encrypted conversation over an ordinary phone call or VoIP app. Neither the carrier
nor the app vendor can recover what was said.

**What this plan delivers (Track A).**

| Property | Target |
|---|---|
| Confidentiality | AES-256, fresh keys every call, forward secrecy |
| Authentication | Short authentication string (SAS) read aloud, plus key continuity across calls |
| Tamper detection | 16-bit keyed tag on every frame, with an alarm |
| Channels | VoLTE (AMR-WB/EVS), 2G/3G where still available, WhatsApp, Signal, Telegram, FaceTime, Meet |
| Speech | Codec2 quality, intelligible (≈ MELPe/Codec2 class) |
| Added one-way latency | ≤ 200 ms in low mode, ≤ 160 ms in high mode |
| Time to go secure | ≤ 15 s |

**What it does not deliver.** It does not hide *that* encryption is in use. The transmitted
audio is clearly a modem signal, and a carrier can detect or block it. Deniability is a
separate research track (§8) with its own go/no-go gate. It never blocks Track A.

**Why this design.** Each piece has real-call results in the source papers, mainly P2
(Krasnowski 2022) for the modem and framing and P3/P5 for channel results. The pieces that
don't hold up have been removed (§1).

**Status, 2026-11-17 (evening): a complete two-way model exists and works in simulation.**
Code and design are in `veilspeak/` (`veilspeak/README.md`); the evidence is in
`repro_results/README.md`. Nothing has been tried on a real call.

- **Scope narrowed.** Work is on phone-network calls only: 2G/3G (AMR) and VoLTE (AMR-WB,
  EVS), including calls that cross between networks. The VoIP-app channels in this plan are
  parked.
- **The design differs from §3.4–3.6.** P2's Table 4 waveform does not survive phone codecs
  as networks run them: their silence detectors mute it, and it fails at AMR 7.95 and below
  and at EVS below 13.2. The model uses 23 tones in 10 ms symbols, sent in 480 ms superframes
  that start with an 80 ms pause, in a 2.4 kbit/s mode (Codec2 1200) and a 1.2 kbit/s mode
  (Codec2 700C). Where this plan and `veilspeak/README.md` differ, the README is current.
- **Simulated calls.** 168 complete two-way calls over 28 scenarios, through 3GPP's reference
  codecs with silence detection on. All 168 agreed keys with matching codes, 6.7 to 8.2 s
  after the unit was switched on. No frame with wrong content was accepted.
  - 24 scenarios delivered 97.7% to 100% of speech: every 2G and VoLTE codec at its usual
    rates, frame loss, the VoLTE jitter buffer, transcoding between networks, a handover
    from VoLTE to 2G in mid-call, sidetone, and a 12 dB noise suppressor.
  - A 2G rate drop with a handover delivered 96.8%.
  - Speech stops while a 2G call sits at AMR 5.9 or below (the cell-edge rates); the link
    stays up and speech returns about a second after the rate recovers.
  - An 18 dB noise suppressor at both phones lets through 76%.
- **Cross-checks.** 56 more calls through the fixed-point codecs (the bit-exact versions
  phones are tested against) gave the same results scenario by scenario. Ten-minute one-way
  runs never lost lock.
- **Targets in the table above that the model misses.** Added one-way delay is about 0.45 s
  in the fast mode and 0.64 s in the robust mode, by design (the target was 0.2 s).
- **Not done.** Real calls, real-time operation on hardware (M4), ML-KEM hybrid, in-call
  ratchet.

---

## 1. What changed from `plan.md`, and why

| Area | `plan.md` | v2 | Reason |
|---|---|---|---|
| Core scheme | P1 analog parameter scrambler → pseudo-speech | Vocoder → AES-256-CTR + tag → Reed-Solomon → P2 codebook modem | P2's digital chain worked on real 3G and VoIP calls (effective BER 3.7×10⁻³ on 3G, <10⁻⁴ on FaceTime). P1 is wideband (300–6700 Hz), breaks on sync errors above 0.3 ms, was only shown on VoIP, and was decrypted offline. |
| Key agreement | P5 key distillation (SKD) over a public channel | In-band X25519 (+ optional ML-KEM), commit/reveal, SAS read aloud, retained secrets | P5's claimed key rate (6–10 kb/s) is higher than the shared randomness it uses (~1.19 kb/s). Its open phase uses cleartext parameters the carrier also sees. It needs an authenticated channel anyway. |
| Key strength | 64–80-bit "weak-but-enough" | 256-bit keys, never reused | Key length doesn't change what the signal looks like. 64/80-bit keys can be brute-forced. Reusing a key leaks plaintext differences (two-time pad). |
| Speech front end | STT → TTS in the live path | Low-rate vocoder (Codec2 1200/2400) | STT/TTS adds 0.5–2 s, and recognition errors change meaning without anyone noticing. |
| Modulation | P1 harmonic synthesis + FBMC/OQAM | P2 codebook modem; P3 FBMC as the M1 challenger | P1 and FBMC can't be combined (fixed vs. pitch-dependent carriers, 8 vs. 16 kHz). P2 has the best measured results. |
| Deniability | Core property | Separate gated research track | No source paper achieves it. P1's output "resembles band-limited noise"; P2 says its signal is "easily detected by… DLP and CMF systems". |
| Device | Inside the phone, before the native codec | External unit (laptop → dongle) | There is no public iOS/Android API to inject audio into call audio. Every paper used an external unit or precomputed audio. |
| Integrity | None | 16-bit keyed tag per frame + tamper alarm | P1 §4.1 and P2 §5.2 both flag the missing integrity check. |

---

## 2. Threat model (Track A)

| Adversary | Can do | Outcome |
|---|---|---|
| Passive network observer / carrier / app vendor | Record the call audio (after removing transport encryption) | Gets modem audio carrying AES-256 ciphertext. **Content protected.** |
| Record-now-decrypt-later (classical) | Store calls for years | **Protected.** Ephemeral X25519 + AES-256. |
| Record-now-decrypt-later (quantum) | Break X25519 in future | Protected only if the ML-KEM hybrid is enabled (§3.5). |
| Active MITM during setup | Run its own key exchange with each side | **Detected** if users compare the SAS. Success chance 2⁻²⁰ per call thanks to commit/reveal. |
| Active tampering during the call | Flip bits, inject or replay frames | Each forged frame passes with probability 2⁻¹⁶. Replays are rejected by the counter. A tamper alarm fires on repeated tag failures. |
| Carrier wanting to stop the call | Block, degrade or flag non-speech audio | **Not protected.** Out of scope for Track A. |
| Traffic analysis | Who called whom, when, for how long | **Not protected.** |
| Compromised phone | Malware on the phone | Partly protected. The add-on unit does the crypto, so phone malware sees only modem audio (P1/P2 motivation). Compromising the unit itself is out of scope. |

---

## 3. System architecture

### 3.1 Form factor

| Stage | Setup |
|---|---|
| Prototype (M0–M5), cellular | One laptop per side, wired to the phone's headset port (TRRS or USB-C headset adapter). Use a resistive pad on the line-out → mic-in path, because the phone mic input expects mic level, not line level. The phone places a normal call. |
| Prototype, VoIP shortcut | Run WhatsApp, Signal or Telegram Desktop on each Mac and route audio through **BlackHole** virtual audio devices. No phone hardware is needed, which makes iteration fast. Confirm results on mobile apps before trusting them. |
| Product (M6) | Small wired add-on: Raspberry Pi Zero 2 W or CM4 with an I²S codec, then a Bluetooth HFP variant (**mSBC wideband only**; CVSD is narrowband and lossy). Precedent: Rowetel's SM1000 ran Codec2 plus a modem on an STM32F4. |

### 3.2 Pipeline

```
SENDER (add-on unit)
 mic ─► Codec2 ───────► encrypt + tag ───────────► RS encode ─────► DoV modem ───► phone call audio
        1200 (low) or    AES-256-CTR, IV = frame    GF(64),           P2 harmonic 4-PSK codebooks,
        2400 (high)      counter; 16-bit trunc.     RS(28,20) low     10 ms sync header per frame,
        + PLC on rx      HMAC-SHA256 tag            RS(40,28) high    silence insertion (P2),
                                                                      amplitude pulsing (P3),
                                                                      bad-band avoidance (P3)

RECEIVER (add-on unit)
 phone audio ─► sync (header correlation, ─► demod (Eq.10: phase-shift ─► RS decode ─► verify tag ─► decrypt ─► Codec2 ─► speaker
                fractional timing)            comp. + spectral weighting;     (+erasures)   (fail → erase,              decode
                                              flag low-confidence symbols                  count for alarm)            + PLC
                                              as erasures)
 channel tracker: decision-directed update of per-harmonic phase shift φ̂ₖ and variance σ̂²ₖ from RS-corrected frames
```

### 3.3 Components

| Component | Choice | Notes |
|---|---|---|
| Vocoder | Codec2 **1200** (48 bits / 40 ms) in low mode; Codec2 **2400** (48 bits / 20 ms) in high mode. Fallback: Codec2 **700C**. | `libcodec2` (LGPL). Proven in P2. MELPe 1200 is an alternative (P3/P5) but is licensed. |
| Packet loss concealment | Repeat the last frame's parameters with an energy fade over 2 frames, then mute | Covers RS failures, tag failures and inserted silences. |
| Cipher | AES-256-CTR. Keystream XORed onto the vocoder bits. IV = 32-bit implicit frame counter ‖ block index. Separate keys per direction. | Never reuse a (key, counter) pair; assert this in code. ChaCha20 is acceptable instead. |
| Integrity | Encrypt-then-MAC. Tag = first 16 bits of HMAC-SHA256(K_mac,dir, dir ‖ ctr32 ‖ ciphertext). | **Do not use AES-GCM with short tags**; truncated GCM tags are weak. The tag also checks RS erasure guesses, replacing P2's 8-bit CRC. |
| FEC | Shortened Reed-Solomon over GF(2⁶) with erasure decoding | Erasures come from demodulator confidence plus energy anomalies. A lost 20 ms VoIP packet is about 8 DoV symbols, which RS(28,20) can fix as erasures but not as plain errors. |
| Modem | P2 codebook DoV: harmonics at 400 Hz steps from 400 to 3200 Hz (8 harmonics), 4-PSK per harmonic, codebooks chosen from quaternary codes with maximum Lee distance, 400 baud | P2's Octave code is on OSF (osf.io/gujfp → `Data_over_Voice.zip`), not GitHub, and is licensed **CC BY-NC-ND 4.0**. Use it as a reference to validate a clean-room implementation written from the paper, or get the author's permission before porting it. P3's FBMC/OQPSK (M=128, 16 subcarriers, PHYDYAS K=4) competes in M1; no public code exists for it. |
| Channel estimation | 2–4 s training at setup (P2 Fig. 10), then decision-directed tracking with exponential averaging (α≈0.05) | Tracking matters because AMR/EVS change rate mid-call; P2 estimated only once. |
| Surviving VAD and noise suppression | Silence in place of every N-th frame (P2, N≈16, tune per channel), amplitude pulsing 1.0/0.8/0.7 (P3), optionally alternating two codebooks on non-overlapping bands (cited in P2) | Each inserted silence costs one speech frame (PLC hides it). Measure time-to-suppression in M1. |
| Level | Modem RMS ≈ −18 dBFS, peaks ≤ −3 dBFS | Avoids clipping in the phone's AGC and limiter. The modem is phase-based, so AGC gain changes don't matter. |
| Duplex | Full duplex if M1 shows it survives the phones' echo cancellers; otherwise push-to-talk | Decided at gate G1. The protocol supports both. |

### 3.4 Frame formats

Derived from P2 Table 4. The only change is replacing the 16-bit counter + 8-bit CRC with an
8-bit counter + 16-bit keyed tag.

| Field | Low mode (2G/3G/VoLTE) | High mode (VoIP) |
|---|---|---|
| DoV frame duration | 80 ms | 60 ms |
| Modulation rate / codebook | 400 baud / 64 (6 bits/symbol) | 400 baud / 4096 (12 bits/symbol) |
| Gross bit rate | 2400 bps | 4800 bps |
| Sync header | 4 DoV symbols (10 ms) | 4 DoV symbols (10 ms) |
| RS code | RS(28,20) over GF(64): t = 4 errors or 8 erasures | RS(40,28) over GF(64): t = 6 errors or 12 erasures (1 DoV symbol = 2 RS symbols) |
| Message (120 / 168 bits) | 96 ciphertext (2 × Codec2-1200 frames) + 8 counter LSBs + 16 tag | 144 ciphertext (3 × Codec2-2400 frames) + 8 counter LSBs + 16 tag |
| Net speech rate | 1200 bps | 2400 bps |

- **Frame types.** Two different sync words mark VOICE and CONTROL frames, so the type costs
  no payload bits.
- **Counter.** Only the low 8 bits are sent; the full 32-bit counter is implicit. The counter
  wraps every 20.5 s (low) or 15.4 s (high). After a dropout, the receiver tries
  candidates near the counter value expected from elapsed time and accepts the one whose tag
  verifies (false-accept 2⁻¹⁶ per try).
- **Replay.** Frames must have a strictly increasing counter, since a voice channel never
  reorders.

### 3.5 Session protocol (call setup)

Modelled on ZRTP (RFC 6189), carried over CONTROL frames. Long messages are split across
frames; every message enters the transcript hash.

```
 normal call connected → both users press SECURE
 1. ACQUIRE   sync preamble both ways; detect peer                         (≤ 1 s)
 2. TRAIN     2–4 s known sequence each way → φ̂ₖ, σ̂²ₖ; choose mode and codebook  (2–4 s)
 3. HELLO     version, supported modes, 128-bit nonce
 4. COMMIT    initiator → H(pk_I ‖ nonce_I)        (prevents a MITM from choosing keys to collide the SAS)
 5. DH        responder → pk_R;  initiator → pk_I   (X25519; optional ML-KEM-768 encapsulation)
 6. DERIVE    HKDF-SHA256( X25519 ‖ [ML-KEM ss] ‖ [retained secret], transcript hash ) →
              K_enc(I→R), K_enc(R→I), K_mac(I→R), K_mac(R→I), K_confirm, SAS (20 bits), next retained secret
 7. CONFIRM   each side sends HMAC(K_confirm, transcript)   → both hold the same keys
 8. SECURE    encrypted voice starts. The UI shows the SAS (4 characters).
              Users read the SAS to each other over the encrypted voice and press ✓ or ✗ (✗ = abort).
              The secure indicator stays amber until ✓.
```

- **Setup cost.** About 200 bytes of control traffic, roughly 2–3 s at 1.2 kbps including
  turnarounds. Total time to go secure is about 6–10 s, plus reading the SAS.
- **ML-KEM-768 hybrid (optional).** Adds about 18 kbit, roughly 8 s in high mode. Default
  off in low mode.
- **Key continuity.** After a call where the SAS was confirmed, store a retained secret per
  contact and mix it into the next call's key derivation. A mismatch shows "keys changed —
  verify SAS".
- **In-call ratchet (optional).** K_{e+1} = HKDF(K_e, "ratchet") every 1024 frames; delete
  K_e once frames in epoch e+1 verify. This protects earlier parts of a call if the device is
  seized mid-call.
- **Crypto code.** Use `cryptography` (pyca) or libsodium. No custom primitives.

### 3.6 Latency budget (one-way, added on top of the call's own delay)

| Stage | Low mode | High mode |
|---|---|---|
| Collect speech for one DoV frame | 80 ms | 60 ms |
| Transmit the DoV frame (receiver needs all of it) | 80 ms | 60 ms |
| Sync, demod, RS, crypto, vocoder | ~15 ms | ~15 ms |
| Receive buffer margin | ~20 ms | ~20 ms |
| **Added total** | **≈ 195 ms** | **≈ 155 ms** |

Mouth-to-ear delay will be about 300–450 ms once the network's own delay is included. That
is workable for radio-style conversation, but it is above ITU-T G.114's comfort zone. The
options to reduce it are shorter DoV frames (lower efficiency) or the high mode.

---

## 4. Build plan — milestones and gates

The riskiest assumption is tested first: **does the modem survive today's channels**
(VoLTE/EVS, phone audio processing, full duplex)? P2 never tested VoLTE. P5 reports
VoLTE BER 1.5×10⁻³ for its own modem.

### M0 — Test rig and channel harness (1 week)
- **Build.**
  - The real-call rig from §3.1 for both sides.
  - The BlackHole VoIP rig.
  - Simulated codec chains: AMR-NB (12.2/7.4/4.75), AMR-WB (12.65), EVS (13.2, with
    VAD/DTX on), Opus SILK (12–16 kbps).
  - Optionally, the WebRTC audio processing module (NS, AGC2, AEC3) in front of the codecs.
  - Harness: `play.wav → channel → rec.wav`, with chirp markers for sample-accurate
    alignment.
- **Gate G0.** Any WAV round-trips through every simulated and real channel with
  automatic alignment.

### M1 — Channel characterization and modem bake-off (3 weeks)
- **Build.**
  - Implement P2's DoV in NumPy: codebook design, TX, RX with the Eq. 10 metric,
    training-based estimation. Write it clean-room from the paper and check it against P2's
    Octave reference from OSF, which is CC BY-NC-ND, so don't port it line by line.
  - Implement P3's FBMC/OQPSK as the challenger.
  - Use precomputed signals only; no real-time yet.
- **Measure, per channel.**
  - SER versus gross rate (1.2–4.8 kbps) for 8 harmonics @ 2.5 ms and 10 harmonics @ 5 ms.
  - Per-harmonic phase shift and variance.
  - Time to suppression with and without silence insertion (N = 8/16/32) and amplitude pulsing.
  - **Simultaneous two-way transmission** (does the phone's echo canceller eat the uplink?).
  - Behaviour when the codec changes rate.
  - VoIP packet-loss patterns.
- **Gate G1.**
  - On VoLTE plus at least 2 VoIP apps: raw SER ≤ 2×10⁻² at the low-mode gross rate.
  - No suppression during 5 minutes of continuous transmission.
  - Decisions recorded: modem per channel (P2 or P3), full duplex or push-to-talk, and
    the N for silence insertion.
- **If it fails.** Drop to 10 harmonics @ 5 ms at 1.2 kbps gross (P2 measured SER < 10⁻⁴
  on 3G) with Codec2 700C. Intelligibility drops, but the link works.

### M2 — Offline end-to-end link (2 weeks)
- **Build.**
  - Codec2 wrapper with PLC.
  - Framing from §3.4.
  - RS GF(64) with erasure flagging.
  - Encrypt + tag using fixed test keys.
  - Counter recovery and resync.
  - Run the recorded M1 captures through the full chain.
- **Measure.**
  - Frame error rate (FER) after FEC, effective BER and tag-failure rate.
  - PESQ and STOI against the Codec2-only reference.
  - Whisper word error rate (WER) against the Codec2-only reference.
- **Gate G2.**
  - FER ≤ 2 % on VoLTE and the chosen VoIP apps (P2 reached 1.9 % on 3G and ≤ 0.2 % on VoIP).
  - PESQ drop ≤ 0.3 and WER increase ≤ 5 points compared with Codec2 alone.

### M3 — Session protocol and crypto (2 weeks, in parallel with M2)
- **Build.**
  - The §3.5 state machine over CONTROL frames.
  - Retained secrets.
  - Ratchet.
  - Replay rejection.
  - Tamper alarm.
- **Test.**
  - Known-answer test vectors.
  - A simulated MITM that relays with its own key exchange must produce a SAS mismatch every time.
  - Bit-flip and replay injection must be rejected by the tags.
  - Fuzz the CONTROL parser.
  - Code asserts that no (key, counter) pair is ever reused.
- **Gate G3.**
  - All negative tests pass.
  - A 2–3 page protocol spec has been reviewed by someone outside the project, ideally a
    cryptographer.

### M4 — Real-time laptop prototype (4 weeks)
- **Build.**
  - Streaming pipeline (PortAudio callbacks, ring buffers).
  - Per-frame header sync with fractional timing recovery (handles sound-card clock drift
    and VoIP time-stretching).
  - Decision-directed channel tracking.
  - Automatic mode selection.
  - UI: SECURE button, SAS display and confirm, link-quality meter, tamper alarm, and
    push-to-talk if G1 chose it.
  - Move hot loops to numba or C if needed.
- **Gate G4.**
  - 10-minute live calls on VoLTE and 2 VoIP apps.
  - Added latency ≤ 200 ms (low) / ≤ 160 ms (high), measured with a click test.
  - FER ≤ 3 %, no unrecovered desyncs, go-secure ≤ 15 s.

### M5 — Field evaluation (3 weeks)
- **Calls.** 50 calls per channel (as in P3) at different times and places, including on
  the move and across handovers.
- **Listening test.** MUSHRA with about 20 listeners (webMUSHRA), using a hidden reference,
  Codec2-only as the mid anchor, and a low anchor. Add a transcription-based intelligibility
  task.
- **Security review.** Review of the implementation.
- **Gate G5 (prototype done).**
  - Median FER ≤ 2 %.
  - MUSHRA within 10 points of the Codec2-only anchor.
  - No open high-severity security findings.

### M6 — Hardware add-on (6–8 weeks, optional)
- **Build.** Pi-class unit with an I²S codec, wired to the phone first; Bluetooth HFP with
  mSBC as a stretch goal. Port the hot paths to C.
- **Gate G6.**
  - M5 metrics reproduced on the device.
  - At least 4 hours of talk time on battery.

**Effort.** About 15 weeks for one engineer through M5, or about 10 weeks with two (M2 and M3
in parallel). Add 6–8 weeks for M6.

---

## 5. Metrics

| What | Metric | Where |
|---|---|---|
| Modem | Raw SER/BER per channel and rate; time to suppression | M1 |
| Link | FER after FEC, effective BER, tag-failure rate, resync time after a dropout | M2, M4, M5 |
| Speech | PESQ and STOI against Codec2-only; Whisper WER against Codec2-only; MUSHRA (M5) | M2, M5 |
| Latency | Added one-way delay (click test); time to go secure | M4, M5 |
| Security | KATs, MITM/replay/bit-flip tests, fuzzing, external review | M3, M5 |

P1 notes that PESQ suits neural vocoders poorly. That doesn't matter here because every
comparison is against Codec2 alone.

---

## 6. Software stack and repo layout

- **Language and maths.** Python 3.11+, NumPy/SciPy, numba for demodulator loops; C for M6.
- **Vocoder.** `libcodec2` via ctypes or pycodec2.
- **FEC.** `reedsolo` with `c_exp=6`, a GF(64) primitive polynomial, and `erase_pos` for
  erasure decoding.
- **Crypto.** `cryptography` (pyca) for X25519, HKDF, AES-CTR and HMAC; `liboqs-python` for
  optional ML-KEM.
- **Audio.** `sounddevice` (PortAudio), with BlackHole on macOS.
- **Codec simulators.** ffmpeg with libopencore-amrnb/amrwb, libopus, the 3GPP EVS reference
  code, and the WebRTC APM through a small C++ harness.
- **Evaluation.** `pesq`, `pystoi`, faster-whisper with `jiwer` for WER, and webMUSHRA.
- **Reference code.** All of it is in Krasnowski's "CipherSpeech" OSF project, osf.io/gujfp.
  The OSF content is licensed **CC BY-NC-ND 4.0** (non-commercial, no shared derivatives):
  run it and compare against it, but write your own implementation.
  - `Data_over_Voice.zip` (69 MB): P2's full Octave code. Includes codebook search,
    synthesis and demodulation with channel correction, shortened Reed-Solomon with erasures,
    codec channel wrappers (AMR, Opus-SILK, Speex, Codec2, G.726/728, iLBC) and 3G/VoIP
    parameter sets.
  - `Speech_Encryption.zip` (871 MB): P1's Python simulation plus the narrowband 8 kHz
    LPCNet.
  - `AKE_over_Voice/`: Tamarin models of an authenticated key exchange over voice channels
    (ICISSP 2020). Use these as the starting point for M3's protocol verification.
  - `Krasnowski_Thesis.pdf`: the PhD thesis covering all of the above.
  - The GitHub repos PiotrKrasnowski/Data_over_Voice and /Speech_Encryption are mostly stubs
    pointing to OSF. Speech_Encryption has a partial, MIT-licensed early version of P1's
    Python code.

```
voicecrypt/
  vocoder/    codec2 wrapper, PLC
  crypto/     kdf, frame cipher, tags, ratchet
  protocol/   session state machine, control messages, SAS, retained secrets
  fec/        RS over GF(64), erasure decoding
  modem/      codebook design, tx, rx, sync, channel estimation and tracking
  channel/    simulated codec chains, real-call record/playback harness
  rt/         streaming pipeline, UI
  eval/       metrics, batch call scripts, listening-test export
tests/
papers_text/  plain-text versions of the five source papers
```

---

## 7. Risk register

| # | Risk | Likelihood | Impact | Seen at | Mitigation |
|---|---|---|---|---|---|
| 1 | VoLTE/EVS damages the modem (untested in P2) | M | H | M1 | Lower rate; P3 FBMC; later, a wideband variant using AMR-WB's extra band |
| 2 | Phone noise suppression / echo cancellation suppresses the modem, or full duplex fails | H | M | M1 | Silence insertion, pulsing, alternating codebooks, **push-to-talk fallback** |
| 3 | VoIP packet loss, PLC and jitter-buffer time-stretching | H | M | M1/M4 | Erasure flagging, per-frame resync, optional 2-frame interleaving (+80 ms) |
| 4 | Codec rate switches mid-call | M | M | M1 | Decision-directed tracking; short pilot every N frames |
| 5 | Codec2 quality is unacceptable to users | M | M | M5 | LPCNet 1.6 kbps codec (FreeDV 2020) in high mode; neural post-filter |
| 6 | Users skip the SAS check | H | H | M4 | Indicator stays amber until ✓; key-continuity warnings |
| 7 | Carriers or apps block or degrade non-speech audio | L–M | H | M5 | No full technical fix; support several channels |
| 8 | Regulation or terms of service | ? | H | Before M5 field trials | Check local rules on encrypted voice and on non-voice use of voice service |
| 9 | 3G networks are being switched off | H | L | Now | Prioritise VoLTE and VoIP; test 2G/3G only where available |
| 10 | No reference implementation of timing sync exists. P2's demodulator assumes already-aligned input; its header only selects the codebook. Channel corrections are static, per codec. | H | M | M1/M4 | Build acquisition (preamble correlation) and fractional timing/drift tracking ourselves; borrow approaches from the FreeDV modems in the codec2 repo |
| 12 | AMR drops below 10.2 kbps mid-call (weak signal); low mode then fails completely (measured in the working model) | M | H | M1 | Add a robust mode: 200 baud symbols at about 1.2 kbps gross with a 450–700 bps vocoder; switch on tag-failure rate |
| 11 | P2's reference setup won't run on macOS: its codec wrappers call Linux x86 binaries and it needs Octave with the signal and communications packages | C | L | M0 | Linux container (Docker) with Octave; rebuild AMR, Opus, Speex and Codec2 from source |

**Where the risks stand after the simulation work (2026-11-17).** All of this is from
simulated calls; a real call can still disagree.

- **#1 VoLTE/EVS.** Both speech modes pass AMR-WB at 12.65 and above and EVS at 13.2 and
  above; the robust mode passes AMR-WB 8.85 and EVS 8.0 and 9.6.
- **#2 Phone audio processing.** A textbook noise suppressor costs nothing at 12 dB and a
  quarter of the speech at 18 dB on both phones. Real phones may behave differently; this is
  the largest open risk and the first thing to measure. Full duplex works throughout.
- **#3 Jitter buffer.** The EVS jitter buffer shortens the pauses by up to 35 ms and inserts
  or drops 20 ms pieces anywhere. The receiver follows both; heavy jitter with delay spikes
  and 2% loss still delivers 97.7%.
- **#4 Rate switches.** Handled: the link drops to the robust mode within about a second
  and returns by itself.
- **#10 Timing.** Built and tested: lock, drift tracking to 150 ppm, time shifts.
- **#12 Low 2G rates.** The robust mode covers AMR down to 6.7. Nothing carries speech at
  5.9 and below; a vocoder under 500 bit/s would be needed.

---

## 8. Track B — Deniability (research, optional, gated)

**Question.** Can an encrypted message cross a voice call while the audio looks like an
ordinary conversation, both to automated classifiers and to a human listener?

**Why it's separate.** None of P1–P5 achieve this. The basic trade-off is **capacity versus
detectability**, not key size versus detectability. Only a much smaller payload has a chance.

**Approach to test (offline first).**
1. **Shrink the payload.** STT (Whisper) → text → compress → AEAD. Speech at ~15 characters
   per second compresses to roughly 30–50 bps.
2. **Generative steganography.** The ciphertext drives token sampling of a language model
   that writes cover text (Meteor, CCS 2021; Discop, IEEE S&P 2023). The cover is then
   distributed like the model's normal output.
3. **Transport.** Cover text → TTS → call → receiver ASR → re-tokenize → extract → decrypt →
   TTS.

**Expect.**
- A few bits per cover token, so the cover speech runs several times longer than the message.
- Delivery takes seconds. This is a voice-note or walkie-talkie mode, **not a live call**.

**Open problems.**
- Meteor and Discop need the **exact** token sequence, which ASR plus a phone codec will
  not reliably give. This needs ECC and a vocabulary designed for recovery.
- A TTS voice can itself be flagged by synthetic-speech detectors.
- Model-generated chit-chat may not convince a human who knows the callers.

**Milestones.**
- **B0 (2 weeks) — build the adversary first.**
  - A dataset of real calls through the M0 channels.
  - Detectors: speech/non-speech classifier, synthetic-speech (spoof) detector, and
    statistical features (pitch continuity, pause distribution, modulation spectrum).
  - Baseline the Track A signal and P1 pseudo-speech against them. Expect near-100 %
    detection, and document it.
- **B1 (4 weeks) — offline text-stego prototype.** Measure payload bps, end-to-end
  recovery rate and delay.
- **Gate GB.** Continue only if all three hold:
  1. message recovery ≥ 99 % with ECC on at least 2 channels;
  2. detector AUC ≤ 0.6 (including the spoof detector);
  3. a blind listener test is at chance.

  Otherwise stop and write up the negative result.

---

## 9. Later options (not on the critical path)

- **Better speech.**
  - The LPCNet-based 1.6 kbps codec (FreeDV 2020 uses 1733 bps) fits the high-mode payload.
  - A neural post-filter on Codec2 output.
- **"Neutral voice".** Speech is already encrypted end-to-end, so voice identity reaches only
  the intended receiver. If anonymity from the receiver is still wanted, add speaker
  anonymization before the vocoder.
- **P1 analog mode for wideband VoIP.** It degrades gracefully instead of failing at the FEC
  limit. Use a 256-bit seed, and only start this after M5, once sync can hold ≤ 0.3 ms through
  jitter buffers.
- **On-phone integration.** As an Android system app on a custom ROM, to remove the
  external unit. This is research only; stock iOS/Android don't allow it.

---

## Appendix — Where each decision comes from

| Decision | Source |
|---|---|
| Vocoder → AES-CTR → RS → DoV chain; frame sizes; 10 ms header + counter; silence insertion; channel estimation with Eq. 10 | P2 §3–5, Tables 2–5 |
| No room for a full MAC; vocal key verification | P2 §5.2 → replaced by a 16-bit truncated HMAC in place of the CRC |
| SAS-authenticated key exchange over the voice channel | P1 §1 Fig. 1; P2 §5.2; ZRTP (RFC 6189) |
| Amplitude pulsing; avoiding error-prone subcarriers per channel; FBMC as alternative modem | P3 §III–V |
| BER on GSM 3G / VoLTE / WhatsApp / Meet for a MELPe-1200 modem system | P5 Table 4 |
| Need for external units; phone voice processing degrades signals | P1 §6; P3 §II-B |
| Synchronization and quality assessment as open problems; AI-based improvements | P4 §VI-B |
| Rejected: SKD key agreement, weakened keys, per-frame "speech-like" ciphertext, STT→TTS live path, FBMC + P1 combination | Review of `plan.md` against P1–P5 (see conversation, 2026-11-06) |
