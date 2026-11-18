# Veilspeak v3 — forward plan

**2026-11-18 · supersedes `plan_v2.md`, `plan.md` and `IMPLEMENTATION.md` wherever they conflict.**

This plan is written to match what was actually built, not what was once hoped for. The
source of truth for the design is [veilspeak/README.md](veilspeak/README.md); the source of
truth for every result is [repro_results/README.md](repro_results/README.md). Where this plan
and those two disagree, they are right and this plan is stale.

---

## 0. Summary

**Goal.** Two people, each with a small add-on unit, hold an end-to-end encrypted
conversation over an ordinary mobile phone call. The carrier carries encrypted audio and
cannot recover what was said. The unit turns speech into a low-rate bit stream, encrypts it,
and sends it through the call as audio tones.

**What the project protects.**

| Property | How |
|---|---|
| Confidentiality | AES-256-CTR, a fresh key every call, keys never reused |
| Authentication of the other person | A 4-character code (SAS) read aloud, after an X25519 exchange with commit-before-reveal (ZRTP, RFC 6189) |
| Tamper and replay detection | A 16-bit keyed HMAC-SHA256 tag and a frame counter on every frame |
| Channels | Phone-network calls: 2G/3G (AMR), VoLTE (AMR-WB, EVS), and calls that cross between them |

**What the project does *not* do.** It does not weaken its own cryptography — 256-bit keys,
never reduced, never reused; that line is firm (§9). And it does not try to hide *that*
encryption is in use: Track A's signal is openly a modem, a carrier can detect it and could
block it. The project measures how detectable the signal is, to document that limitation
honestly, but does not build a channel engineered to evade real-world monitoring or
interception (§9).

**Status (2026-11-18).** A complete two-way model exists and works **in simulation only**.
The simulated calls pass through the real phone codecs (3GPP reference implementations, both
floating-point and fixed-point), with their silence detectors, rate changes, frame loss, the
VoLTE jitter buffer and transcoding between networks. **Nothing has been tried on a real phone
call.** Everything below treats real-call evidence as the next and most important thing to get.

**Detectability is measured and documented, not defeated.** An adversary detector
(`veilspeak/covert/`) baselines how distinguishable the Track A signal is from speech: it
scores **detector AUC 1.00 — trivially detectable**, exactly as P1/P2 say. This is kept as an
honest limitation (§4, §6, §9), not as a target to beat; defeating real-world monitoring or
interception is out of scope.

**The one caveat that governs this whole plan.** When paper P2's own system was run through a
codec simulation, the simulation understated its real-call symbol errors by 5 to 160 times
(see [repro_results/README.md](repro_results/README.md), Part 2). Our numbers come from the
same kind of simulation. **Until real calls are measured, every result here is provisional,**
and no forward gate is considered met on simulation alone.

---

## 1. What is already built (the baseline this plan continues from)

All of this is done and tested in simulation. It is the starting line, not work remaining.

- **The signal.** 23 tones 100 Hz apart, from 400 to 2600 Hz; every 10 ms the 23 tone phases
  form one codebook symbol (P2's modulation on a different tone grid). Sent in 480 ms
  superframes: 80 ms pause, 2 header symbols, 38 data symbols. The pause defeats the codecs'
  silence detectors, which otherwise mute a steady modem tone within seconds.
- **Modes.** FAST (Codec2 1200, two RS(38,26) frames), ROBUST (Codec2 700C, one RS(38,30)),
  IDLE/keep-alive (RS(38,20)), CONTROL (key agreement), TRAIN (lock-on). The receiver chooses;
  a 2-bit report in every frame tells the sender which to use.
- **Crypto chain.** Codec2 → AES-256-CTR (one frame at a time) → 16-bit truncated
  HMAC-SHA256 tag + low 6 bits of a frame counter → Reed-Solomon with erasure decoding → modem.
- **Key agreement.** X25519 modelled on ZRTP: commit-before-reveal, then a 4-character code
  the two people compare by voice. In simulation, all 168 + 56 calls agreed keys with matching
  codes, 6.7–8.2 s after the unit was switched on; no frame with wrong content was ever accepted.
- **Receiver.** Lock-on by sliding the known TRAIN symbols; per-tone channel estimation;
  timing and sample-clock-drift tracking to ±150 ppm; repair of jitter-buffer time shifts
  (bursts moved or cut mid-way); a burst is accepted only if a frame in it passes its tag.
- **Call simulator.** 3GPP reference AMR/AMR-WB/EVS (float and fixed-point), silence detection
  on, frame loss in runs, handovers, rate changes, EVS jitter buffer, transcoding through
  G.711, and textbook phone-audio stand-ins (band filter, noise suppressor, gain control).
- **Evidence.** 168 two-way calls over 28 scenarios + 56 through the fixed-point codecs;
  40-setting one-way coverage; seven ten-minute soak runs; a 90 s receiver regression; 42
  automated tests. See [repro_results/README.md](repro_results/README.md) for every table.

In plan_v2's old milestone language, this covers roughly M1–M3 — but in **simulation**, not on
hardware and not on a real call.

---

## 2. What this plan keeps, drops, and adds

This plan exists to be consistent with the built system. The reconciliation is explicit.

### Kept (the spine of the project)
- **Encrypted comms ride inside the voice call**, carried as sound the network treats as call
  audio — not a data channel.
- **The P2 codebook modem core**: tone/codebook modulation, Reed-Solomon, and the
  silence-insertion trick that beats the codecs' voice-activity detectors.
- **The chain**: low-rate vocoder → AES → error correction → modem, now with full-strength keys.

### Dropped, and staying dropped (see §9)

All of these stay out of the system. How detectable the signal is gets measured as a
limitation (§6); concealment itself is not engineered.
1. **Concealment / deniability.** Not a goal. Track A's signal is openly a modem; no source
   paper (P1–P5) achieves concealment, and the wall is capacity vs. detectability, not key
   size. How detectable the signal is gets measured and documented (§6); building a system to
   evade real-world monitoring or interception is out of scope (§9).
2. **Deliberate crypto weakening.** No 64/80-bit "weak-but-enough" keys. 256-bit, never
   reused. Key length never had anything to do with what the signal looks like.
3. **"Speech as speech" / parameter scrambling / the hypersphere idea (P1).** Not used. The
   project sends encrypted *data* over voice, not pseudo-speech.
4. **STT → TTS / meaning extraction / re-speaking in a synthetic voice.** Not in the path. The
   unit vocodes the actual speech with Codec2 and sends it. No transcription, no voice
   synthesis, no voice-identity stripping.
5. **Neural vocoder (LPCNet) as the speech front end.** Not used. Plain Codec2 at 700C and
   1200 bit/s. (A neural vocoder remains a *possible, optional* speech-quality improvement far
   down the list — §6, SQ — never a core dependency.)
6. **Autonomous key distillation (P5 / SKD).** Not used. Replaced by ZRTP. P5 needs a second
   authenticated channel this project does not have, and our reading put its key rate below
   the shared randomness it uses.
7. **P3 FBMC/OQAM modem.** Never implemented or compared, and not on this plan's path.
   Recorded as a road not taken, not as a pending challenger.

### Added vs. the original conversation (good practice to preserve and extend)
- **Honest simulation methodology.** Testing against the real 3GPP reference codecs, the
  fixed-point cross-check (the bit-exact definitions phones are tested against), and the loud,
  repeated "simulation understated P2's real errors by 5–160×" warning. This discipline is now
  a rule of the plan: **real-call measurement gates everything** (§6).
- **Timing recovery and lock-on, written from scratch.** The sliding TRAIN correlation, the
  clock-drift tracking, the splicing of bursts the jitter buffer cut. P2's code assumed
  already-aligned input; this is original engineering and is kept and extended into real time.
- **A concrete integrity design.** 256-bit keys, a 16-bit keyed tag per frame, a frame counter
  for replay rejection and for placing speech correctly after a dropout.

---

## 3. Threat model

| Adversary | Can do | Outcome |
|---|---|---|
| Passive carrier / network observer | Record the call audio | Gets modem audio carrying AES-256 ciphertext. **Content protected.** |
| Record now, decrypt later (classical) | Store calls for years | **Protected.** Ephemeral X25519 + AES-256; keys deleted after the call. |
| Record now, decrypt later (quantum) | Break X25519 in future | **Not protected yet.** Needs the optional ML-KEM hybrid (§6, CR). |
| Active MITM during setup | Run its own key exchange with each side | **Detected** when the two people compare the 4-character SAS; commit-before-reveal stops the MITM from steering the code to match. |
| Active tamper / inject / replay during the call | Flip bits, insert or replay frames | Each forged frame passes only with probability 2⁻¹⁶, costing up to 480 ms of noise, not the key. Replays are rejected by the counter. |
| Carrier wanting to stop the call | Detect and block or degrade non-speech audio | **Not protected. Out of scope** — the signal is openly a modem (§9). |
| Traffic analysis | Who called whom, when, how long | **Not protected. Out of scope.** |
| Compromised phone | Malware on the handset | Partly protected: the unit does the crypto, so phone malware sees only modem audio. Compromise of the unit itself is out of scope. |

---

## 4. Design of record (as built)

Canonical detail is in [veilspeak/README.md](veilspeak/README.md). In brief:

- **Vocoder.** Codec2 1200 (fast) and 700C (robust). Intelligible, robotic. No other vocoder
  is in the path.
- **Cipher.** AES-256-CTR, per frame, keystream XORed onto the vocoder bits, separate keys per
  direction; a (key, counter) pair is never reused.
- **Integrity.** Encrypt-then-MAC: first 16 bits of HMAC-SHA256 over direction ‖ counter ‖
  ciphertext. (Not AES-GCM — truncated GCM tags are weak.)
- **FEC.** Shortened Reed-Solomon with erasure decoding; erasures come from demodulator
  confidence and the tag.
- **Modem.** P2 codebooks (quaternary codes with large minimum Lee distance) on the 23-tone,
  10 ms grid; per-tone phase compensation and weighting at the receiver.
- **Framing.** 480 ms superframes with a leading 80 ms pause; two sync words separate VOICE and
  CONTROL; only the low 6 bits of the counter are sent, the rest implicit.
- **Link control.** Mode changes on evidence, not on one bad frame: fast mode dropped after two
  bad superframes in three or below 3.8 dB SDR, re-requested from 4.5 dB.
- **Setup.** The call starts normally; one unit is switched on, the other answers itself; keys
  are agreed about 7 s later; the SAS is read aloud before the link is trusted.

### Coverage already shown in simulation

| Network | Fast (Codec2 1200) | Robust only (Codec2 700C) | No speech (link stays up) |
|---|---|---|---|
| 2G/3G AMR | 12.2, 10.2 | 7.95, 7.4, 6.7 | 5.9 and below |
| VoLTE AMR-WB | 12.65 and above | 8.85; 6.6 with gaps | |
| VoLTE EVS | 13.2 and above | 9.6, 8.0, 5.9 VBR, 13.2 channel-aware; 7.2 with gaps | |
| Across networks (G.711 transcode) | | every combination tried | |

---

## 5. Targets — set to what the design actually does

These replace plan_v2's aspirational table. They are honest about the two targets v2 missed.

| Property | Target | Note |
|---|---|---|
| Confidentiality / integrity / auth | As §0 | Met in simulation |
| Speech coverage | Fast on AMR ≥ 10.2, AMR-WB ≥ 12.65, EVS ≥ 13.2; robust down to AMR 6.7 / AMR-WB 8.85 / EVS 8.0–9.6 | Met in simulation; **to confirm on real calls** |
| Below AMR 5.9 | No speech; keep-alive holds the link up and speech returns ~1 s after the rate recovers | A design limit, not a bug. A sub-500 bit/s vocoder would be needed to change it. |
| Added one-way delay | ~0.45 s (fast), ~0.64 s (robust), on top of the call | **Design value; v2's 0.2 s target is abandoned.** A full FEC frame must arrive before its speech plays. Feels like a satellite link. Real figure to be measured in the real-time port. |
| Time to go secure | ~7 s + reading the SAS | Met in simulation |
| Speech quality | Intelligible | **Unmeasured.** No listening or intelligibility test has been run (§6, SQ). |

---

## 6. Forward plan

Ordered by risk. The riskiest unknown is no longer "does the modem survive the codecs" — the
simulation answered that — it is **"does any of this hold up on a real call,"** because of the
5–160× caveat. So real-call evidence comes first, and it needs no new code beyond the existing
`tools/linetest.py`.

### RC — Real-call evidence (first, highest value)
The single most important piece of missing evidence. Can start now with the offline code.
- **Build the rig.** Two phones with SIMs. Wire each unit to the phone's headset path
  (TRRS or USB-C headset adapter) with a resistive pad on the line-out → mic-in leg, because the
  mic input expects mic level, not line level. Place an ordinary call.
- **One-way line tests, per network and codec.** `python tools/linetest.py make line_test.wav`,
  feed it into one phone in place of the microphone, record the other phone's earpiece, then
  `python tools/linetest.py check recording.wav`. It reports, per mode, how many frames
  survived. Repeat across 2G AMR (12.2, 7.4), VoLTE AMR-WB (12.65), VoLTE EVS (13.2), and
  whatever rates the networks actually use.
- **Compare with the simulation tables** ([repro_results/README.md](repro_results/README.md),
  §1.3). The difference *is* the result. Record the real-call frame survival and the gap to
  simulation for each mode and codec.
- **Gate GRC.** Real-call per-mode frame survival is measured on at least the four codec
  settings above. Decision recorded: for each, does the mode the simulation promised actually
  carry speech on a real call? If the 5–160× gap pushes robust-mode FER past usable, the
  mitigations are more/longer pauses, a lower rate, or heavier FEC — chosen from the recordings,
  which are the evidence to work from.

### RT — Real-time implementation
`veilspeak/live.py` is today a sketch that has only run in a software loop. The offline model
proves the algorithm; this makes it a usable call.
- **Build.** Streaming pipeline on PortAudio (`sounddevice`): ring buffers, the receiver's
  lock-on / drift-tracking / splice logic running continuously on a live stream, automatic mode
  selection, and the setup UI (switch-on, SAS display and confirm, link-quality meter, tamper
  alarm). Move the demodulator hot loops to numba or C as needed to hit frame timing.
- **Measure.** Actual added one-way delay (click test), real go-secure time, CPU load, and
  whether lock holds over a long live call.
- **Gate GRT.** A 10-minute live two-way call over VoLTE and over 2G (real calls, building on
  RC): keeps lock, no unrecovered desync, SAS matches, measured added latency reported against
  the ~0.45 s / ~0.64 s design values, go-secure ≤ ~15 s.

### SQ — Speech-quality measurement
Nobody has yet assessed how the delivered speech *sounds*; all results count frames.
- **Build/measure.** PESQ and STOI, and Whisper word-error-rate, of the delivered speech
  against a Codec2-only reference (so the modem/channel degradation is isolated from Codec2's
  own). Then a small MUSHRA listening test (webMUSHRA, ~20 listeners, hidden reference,
  Codec2-only as mid anchor) plus a transcription task.
- **Gate GSQ.** Intelligibility and MUSHRA quantified and judged acceptable, or not. If Codec2
  700C/1200 is judged too poor, this is the *only* trigger to consider the optional neural
  vocoder (FreeDV-2020-class LPCNet or a neural post-filter) — as a speech-quality improvement,
  never as a crypto or deniability feature.

### PH — Phone audio-processing robustness
The largest open risk the simulation already flagged: a textbook 18 dB noise suppressor at both
phones took delivery down to 76%. Real phones' noise suppression, echo control and gain control
are unknown and differ by chipset.
- **Measure** (largely falls out of RC real calls): characterize the effect of real handsets'
  audio processing on the modem. Try phones with their noise suppression reduced or off where
  possible.
- **Mitigate.** The structural fix is the hardware path (HW): a unit that feeds the codec
  directly inside/beside the phone bypasses the handset's own audio processing entirely.

### HW — Hardware add-on (optional)
A Pi-class unit (Pi Zero 2 W or CM4) with an I²S codec, wired to the phone, feeding the codec
directly; a Bluetooth HFP variant (mSBC wideband) as a stretch goal. Port the hot paths to C.
Precedent: Rowetel's SM1000 ran Codec2 plus a modem on an STM32F4.
- **Gate GHW.** RC/RT/SQ metrics reproduced on the device; ≥ 4 hours of talk time on battery.

### CR — Crypto and protocol hardening (optional, can run in parallel)
The crypto chain works in simulation; this is defence-in-depth and robustness.
- External review of a 2–3 page protocol spec, ideally by a cryptographer.
- Negative tests as standing CI: a simulated MITM must always produce a SAS mismatch; bit-flip
  and replay injection must always be rejected; fuzz the CONTROL parser; assert no (key, counter)
  pair is ever reused. (Some coverage exists among the 42 tests — audit and fill gaps.)
- Optional features, each clearly off the critical path: ML-KEM-768 hybrid key exchange
  (post-quantum), in-call re-keying / ratchet, retained-secret key continuity between calls, and
  handling both people switching on at the same instant.

**Rough order and effort.** RC first (days of real-call work, no new code). RT is the largest
single piece (several weeks). SQ and PH can overlap RT. HW and CR are optional and later.

### Detectability — measured, not defeated

Track A's signal is openly a modem, and the project documents that honestly instead of
engineering around it. `veilspeak/covert/detect.py` builds the kind of speech-vs-modem detector
a network might run (level-blind acoustic features: tone-comb energy, in-band energy fraction,
spectral flatness, pitch steadiness, the 480 ms burst periodicity, pause structure) and
`tools/covert_baseline.py` scores the current signal: **detector AUC 1.00 — trivially
detectable**, with several single features separating modem from speech perfectly. Exactly what
P1 ("band-limited noise") and P2 ("easily detected") predicted, now measured. Evidence:
`repro_results/covert_B0_baseline.txt`.

It serves two honest purposes only: it states the limitation as a number, and it is a
regression check — if a future waveform change is claimed to be less conspicuous, this measures
whether that is actually true. The project does **not** research or target real-world monitoring
or interception systems to defeat them (§9); a carrier that wants to block the signal can.

---

## 7. Metrics

| What | Metric | Where |
|---|---|---|
| Real-call link | Per-mode frame survival on real calls vs. simulation; FER; resync time | RC, RT |
| Real-time link | Added one-way delay (click test); go-secure time; lock stability; CPU load | RT |
| Speech | PESQ, STOI, Whisper WER vs. Codec2-only; MUSHRA | SQ |
| Phone processing | Delivery vs. handset noise-suppression setting | PH |
| Security | KATs; MITM / replay / bit-flip rejection; fuzzing; external review | CR |
| Detectability (limitation) | Detector AUC (speech vs. signal), documented — not a pass/fail target | measured |

Every link and speech metric is reported for **real calls** once RC is done; simulation figures
are labelled as such and never stand in for a real-call number.

---

## 8. Risk register (updated to the built reality)

| # | Risk | Likelihood | Impact | Where | Standing / mitigation |
|---|---|---|---|---|---|
| 1 | Real calls are much worse than simulation (the 5–160× caveat) | H | H | RC | **The reason RC is first.** Mitigate from the recordings: longer pauses, lower rate, heavier FEC. |
| 2 | Real handset audio processing (NS/AGC/AEC) suppresses the modem | H | H | RC, PH | Already the worst simulated case (18 dB → 76%). Structural fix is the direct-codec HW path. |
| 3 | Real-time port can't hold frame timing / lock on live audio | M | H | RT | Ring buffers, hot loops to C/numba; the offline receiver logic is already proven. |
| 4 | Codec2 700C/1200 too poor for users | M | M | SQ | Measure first. Only then consider the optional neural vocoder. |
| 5 | No speech below AMR 5.9 (cell edge, deep fade) | — | M | — | Accepted limit; keep-alive holds the link and speech returns on rate recovery. Not closing it without a sub-500 bit/s vocoder. |
| 6 | Users skip the SAS check | M | H | RT | Secure indicator stays amber until ✓; key-continuity warning if CR adds retained secrets. |
| 7 | Carrier blocks or degrades non-speech audio | L–M | H | — | **No technical fix and none sought** — the signal is openly a modem (§9). |
| 8 | Added delay (~0.45–0.64 s) is too long for some users | M | M | RT | A design consequence of frame-level FEC. Shorter frames trade coverage for delay; decide after RT measures the real figure. |
| 9 | Regulation / terms of service on encrypted or non-voice use of voice service | ? | M | Before field use | Check local rules before any extended real-call trials. |
| 10 | Both people switch on at once; no partner memory between calls | L | L | CR | Known unhandled cases; optional CR work. |

---

## 9. The firm line, and what is out of scope

One boundary is firm and non-negotiable — because it is bad engineering and nobody asked for it:

- **No deliberately weakened cryptography.** Full-strength standard primitives only; 256-bit
  keys, never reduced, never reused. Key length was never what made the signal detectable, so
  weakening would only make the system both detectable *and* breakable.

Out of scope, by decision:

- **Concealment / undetectability / unblockability.** Track A is openly a modem. The project
  measures how detectable the signal is and documents it as a limitation (§6), but does not
  research real-world monitoring or interception systems in order to defeat them, and does not
  build a channel engineered to evade them. A system whose purpose is to be undetectable to
  real-world telecom monitoring is an interception-evasion capability, which this project does
  not build.
- **Transcription, synthesis and voice-identity stripping.** Not in the path; Track A always
  vocodes the actual speech.
- **Traffic analysis** (who called whom, when, how long) is not addressed.

---

## Appendix — provenance

**Papers.** Only P2 (Krasnowski et al., *Data over Voice*, arXiv 2102.10869) supplied a method:
the modulation principle, the demodulator, the vocoder→AES→RS→modem chain, and the
silence-insertion observation. P1 (2102.09809) contributed only the outline of an SAS-checked
call; its scheme was not used. P3 (FBMC/OQAM, IEEE Access 2024), P4 (survey, IEEE Access 2024)
and P5 (SKD, Symmetry 2025) contributed no component — see the README's table and §2 above for
why each was set aside.

**From outside the papers.** Key agreement follows ZRTP (RFC 6189). The test environment is
3GPP's reference AMR / AMR-WB / EVS codecs (TS 26.104/26.073, 26.204/26.173, 26.443/26.442).
The P2 reproduction used the authors' Octave code from OSF (osf.io/gujfp, CC BY-NC-ND 4.0),
run unmodified and copied into none of this repository's code.

**What each document is for.**
[veilspeak/README.md](veilspeak/README.md) — the design, canonical.
[repro_results/README.md](repro_results/README.md) — every result and how to regenerate it.
`plan_v2.md`, `plan.md`, `IMPLEMENTATION.md` — earlier plans, kept for history, superseded here.
