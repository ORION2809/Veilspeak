# Veilspeak

Veilspeak carries encrypted speech as audio tones inside an ordinary mobile phone call. A
unit at each end turns speech into a low-rate bit stream, encrypts it, and sends it through
the call as sound; the network carries it like any other call audio.

**Status (2026-11-18).** A complete two-way model exists and works in simulated calls that
pass through the real phone codecs. **It has not been tried on a real phone call.** Every
result in this file comes from simulation.

This file covers three things: how the five research papers were used, how the model was
built, and what was and was not achieved. Design detail is in
[veilspeak/README.md](veilspeak/README.md); every result table and the commands that
produce them are in [repro_results/README.md](repro_results/README.md).

## 1. How the research papers were used

The project started from five papers (PDFs in this folder, text in `papers_text/`). One of
them supplied the core method. The others were read and reviewed but nothing in the model
is built from them.

| | Paper | What it proposes | What was used here | What was not used, and why |
|---|---|---|---|---|
| P1 | Krasnowski, Lebrun, Martin: *Introducing an experimental distortion-tolerant speech encryption scheme for secure voice communication* (arXiv 2102.09809) | Scrambles speech parameters (loudness, pitch, timbre) and sends them as pseudo-speech; a neural vocoder restores the speech | Only the outline of a call that it describes: exchange keys, check them with a short authentication code, then send encrypted speech | The scheme itself. It is designed for a wideband channel (16 kHz sampling, 300–6700 Hz), and 2G calls are narrowband. Its real-world test was over FaceTime |
| P2 | Krasnowski, Lebrun, Martin: *Introducing a novel Data over Voice technique for secure voice communication* (arXiv 2102.10869) | A modem for voice channels: short harmonic waveforms drawn from codebooks, with Reed-Solomon coding, tested on 3G calls and VoIP apps | **The core method.** See below | Its published waveform parameters (its Table 4), which did not survive the codec settings tested here |
| P3 | Čubrilović et al.: *FBMC/OQAM-Based Secure Voice Communications Over Voice Channels* (IEEE Access, 2024) | A different modem (filter-bank multicarrier) for voice channels, tested on GSM and VoIP | Nothing | It was listed in the plan as an alternative modem. It was never implemented or compared |
| P4 | Pekerti, Sasongko, Indrayanto: *Secure End-to-End Voice Communication: A Comprehensive Review…* (IEEE Access, 2024) | A survey of the field | Background only | It contains no component to build |
| P5 | Radomirović et al.: *A Class of Perfectly Secret Autonomous Low-Bit-Rate Voice Communication Systems* (Symmetry 17:365, 2025) | One-time-pad encryption of MELPe speech, with keys made by a key-distillation protocol over a public channel | Nothing | The paper requires a second, authenticated channel besides the call; this project has only the call. Our review also doubted the key rate (see below) |

### What was taken from P2

- **The modulation principle.** Each symbol is a set of harmonics, each in one of four
  phases, drawn from a codebook built from quaternary codes with a large minimum distance.
- **The demodulator.** Per-harmonic phase compensation and weighting (the paper's
  Eqs. 4–10).
- **The frame chain.** Low-rate vocoder, then AES in counter mode, then Reed-Solomon coding
  with erasure decoding, then the modem.
- **The observation that phone networks mute a steady modem signal**, and the remedy of
  inserting silence.

### What was changed from P2, and why

- **Waveform.** P2's published settings use 8 harmonics and 2.5 ms symbols. In our tests
  through the codecs (ideal timing, 2026-11-17) that waveform failed at AMR 7.95 kbit/s and
  below and at EVS below 13.2 kbit/s. The model uses 23 tones and 10 ms symbols instead.
- **Silence.** P2 inserts silence in place of some frames (every 16th in its example). In
  our first model, a continuous signal lost 98% of its frames through AMR 12.2 with silence
  detection on, and P2's remedy removed that loss in a 60 s test. The final design builds
  the silence in: 400 ms bursts separated by 80 ms pauses. That pattern kept both AMR
  silence detectors, AMR-WB and EVS treating the signal as speech in our tests; 40 ms
  pauses did not.
- **Timing recovery.** P2's published code assumes the signal is already aligned. Finding
  and keeping timing in a live audio stream was written from scratch.
- **Integrity check.** P2 notes that the bandwidth leaves no room for the usual message
  authentication. The model puts a 16-bit truncated authentication tag on every frame.
- **Vocoder.** Codec2 (open source) at 1200 and 700 bit/s.

### What came from outside the five papers

- **Key agreement** follows ZRTP (RFC 6189): an X25519 exchange with a hash commitment, and
  a 4-character code that the two people compare by voice.
- **Test environment.** 3GPP's reference implementations of the AMR, AMR-WB and EVS codecs.

### Reproduction of P2 (2026-11-06)

Before building anything, the authors' own Octave code (from OSF, licence CC BY-NC-ND) was
run through simulated codecs. It was run unmodified; nothing from it is in this repository's
code.

| P2 claim | Result |
|---|---|
| Codecs at 10–16 kbit/s leave a multi-tone signal at about 2–6 dB signal-to-noise | Reproduced |
| Distortion per tone is a fixed phase shift plus roughly Gaussian noise | Reproduced |
| Symbol errors rise in steps with codebook size and bit rate | Trend reproduced |
| Symbol error rates on real calls | **Not reproducible in simulation.** The paper's real 3G figures are 5 to 160 times worse than our AMR simulation of the same settings |
| Full system at 1.2 and 2.4 kbit/s | The published code's parameter sets differ from the paper's Table 4 and carry 1.07 and 2.13 kbit/s |

The fourth row matters for everything below: **a codec simulation understated real-call
errors for P2's own system.** The same may be true of ours.

### Review findings that shaped the plan (2026-11-06)

These are conclusions from reading the papers, not from experiments.

- **Deniability.** The original plan (`plan.md`) aimed to make the encrypted audio pass for
  ordinary speech. None of the five papers achieves that. P1 says its output "resembles
  band-limited noise"; P2 says a non-speech signal "can be easily detected" by monitoring
  systems. The model does not attempt it.
- **P5's key rate.** P5 reports a secret-key rate of up to 8.8 kbit/s. In our reading, the
  random input to its protocol is 80 bits per 67.5 ms frame, about 1.19 kbit/s, which is
  less than the key rate reported. This is our reading of the paper, not a test of it.
- **Key strength.** The original plan used 64- to 80-bit keys. The model uses 256-bit keys
  that are never reused.

## 2. How it was implemented

Everything is Python, in `veilspeak/`. It processes audio offline; it is not a real-time
implementation.

### The path of speech through one unit

1. **Vocoder.** Codec2 compresses speech to 1200 or 700 bit/s.
2. **Encryption.** AES-256 in counter mode, one frame at a time, with a 16-bit HMAC-SHA256
   tag and the low 6 bits of a frame counter.
3. **Error correction.** Reed-Solomon coding, with erasure decoding at the receiver.
4. **Modem.** 23 tones, 100 Hz apart, from 400 to 2600 Hz. Every 10 ms the 23 tone phases
   together form one symbol from a codebook.
5. **Framing.** Superframes of 480 ms: an 80 ms pause, 2 header symbols, 38 data symbols.

The receiving unit does the same in reverse.

### Modes

| Mode | Bits per 10 ms symbol | Error correction | Carries |
|---|---|---|---|
| Fast | 2 × 12 | two Reed-Solomon (38,26) frames | 480 ms of speech at 1200 bit/s |
| Robust | 12 | one Reed-Solomon (38,30) frame | 480 ms of speech at 700 bit/s |
| Idle | 6 | Reed-Solomon (38,20) | no speech; keeps the link up |
| Control | 12 | as robust, with a CRC | key-agreement messages |
| Train | known symbols | none | lets the far end lock on |

### Receiver

- **Locking.** It slides the known training symbols along the audio until they fit, then
  measures the channel's gain and noise per tone.
- **Tracking.** It follows timing and sample-clock drift from each burst it decodes.
- **Time shifts.** A VoLTE phone's jitter buffer stretches and squeezes audio. In our
  measurements of the EVS reference decoder it shortened the pauses by up to 35 ms and
  inserted or dropped 20 ms pieces in mid-burst. The receiver looks for a burst that has
  moved and splices a burst that was cut.
- **Acceptance.** A burst counts only if a frame in it passes its integrity check.

### Link control

Every frame carries a 2-bit report from the receiving end: send fast, send robust, send
idle, or "I hear nothing". The sender obeys. A unit asks for the fast mode when its measured
signal-to-distortion ratio is 4.5 dB or more, and drops it after repeated failures.

### Starting a call

The call begins as an ordinary one. One person switches the unit on; the other unit detects
the training signal and answers by itself. The two then agree keys.

### The call simulator

All testing was done in `veilspeak/cellsim/`:

- **Codecs.** 3GPP's reference code for AMR (2G/3G), AMR-WB and EVS (VoLTE), with silence
  detection on. Both the floating-point and the fixed-point versions were used.
- **Network effects.** Frame loss in runs, handovers, codec rate changes, packet jitter with
  the EVS jitter buffer, transcoding through G.711 between networks, and a handover from
  VoLTE to 2G in mid-call.
- **Phone effects.** A telephone-band filter, a sample-clock offset, line noise, sidetone,
  and textbook versions of a noise suppressor and gain control. These are generic
  stand-ins, not any real phone's processing.
- **Two-way calls.** Both units run together and each reacts only to audio it could already
  have received.

## 3. What was achieved

All figures are from simulated calls on 2026-11-17 and 2026-11-18.

### Complete two-way calls

168 calls: 6 calls of 45 s in each of 28 scenarios, through the floating-point codecs.

- **Key agreement:** all 168 calls agreed keys with matching codes, 6.7 to 8.2 s of call
  time after the unit was switched on.
- **Wrong frames:** no frame with wrong content was accepted in any call.
- **Speech delivered:** 24 of the 28 scenarios delivered between 97.7% and 100%.

"Speech delivered" is the share of 40 ms speech frames that arrived bit for bit, counted from
the moment both ends had keys. It says nothing about how the speech sounds.

| Scenario | Speech delivered | Lowest in any call and direction | Longest gap | Same scenario, fixed-point codecs (2 calls) |
|---|---|---|---|---|
| 2G full rate, good signal | 100% | 100% | 0.0 s | 100% |
| 2G full rate, other silence detector | 100% | 100% | 0.0 s | 100% |
| 2G full rate, fading (3% frames lost in runs) | 99.0% | 98.6% | 0.2 s | 98.8% |
| 2G half rate (AMR 7.4), 1% loss | 99.9% | 98.6% | 0.5 s | 100% |
| 2G, rate drops 12.2 > 7.4 > 12.2, one handover | 96.8% | 95.1% | 0.5 s | 97.0% |
| 2G cell edge: 12.2 > 5.9 > 4.75 > 12.2 | 56.4% | 49.3% | 16.3 s | 58.9% |
| 2G deep fade: 12.2 > 4.75 for 7 s > 12.2 | 78.4% | 75.0% | 7.9 s | 80.2% |
| VoLTE AMR-WB 12.65, 0.5% loss | 99.9% | 99.3% | 0.2 s | 100% |
| VoLTE AMR-WB 23.85 | 100% | 100% | 0.0 s | 100% |
| VoLTE AMR-WB 8.85 (reduced rate) | 100% | 100% | 0.0 s | 100% |
| VoLTE EVS 13.2, jitter buffer, 1% loss | 99.9% | 98.6% | 0.5 s | 100% |
| VoLTE EVS 13.2, heavy jitter, delay spikes, 2% loss | 97.7% | 95.3% | 0.5 s | 98.8% |
| VoLTE EVS 9.6, jitter buffer | 100% | 100% | 0.0 s | 99.8% |
| VoLTE AMR-WB 12.65 inside an EVS phone, jitter buffer | 99.7% | 98.6% | 0.5 s | 100% |
| 2G to 2G, both full rate (transcoded) | 100% | 100% | 0.0 s | 100% |
| 2G to 2G, both half rate (transcoded) | 98.6% | 95.7% | 0.5 s | 98.7% |
| VoLTE to 2G full rate (transcoded) | 100% | 100% | 0.0 s | 100% |
| VoLTE to 2G full rate (transcoded), network noise reduction 12 dB | 100% | 100% | 0.0 s | 100% |
| VoLTE to 2G half rate (transcoded) | 100% | 100% | 0.0 s | 100% |
| VoLTE to VoLTE across operators (transcoded) | 100% | 100% | 0.0 s | 100% |
| VoLTE EVS with jitter buffer to 2G half rate (transcoded) | 98.6% | 97.1% | 0.5 s | 98.3% |
| VoLTE call handed over to 2G after 20 s | 98.0% | 95.7% | 1.4 s | 97.8% |
| 2G full rate + noise suppressor 12 dB + gain control | 100% | 100% | 0.0 s | 100% |
| VoLTE AMR-WB 12.65 + noise suppressor 12 dB + gain control | 100% | 100% | 0.0 s | 100% |
| VoLTE AMR-WB 12.65 + noise suppressor 18 dB at both phones | 76.4% | 69.0% | 2.4 s | 71.8% |
| 2G full rate, low level (-12 dB), noisy line, 120 ppm clocks | 100% | 100% | 0.0 s | 100% |
| 2G full rate, sidetone 10 dB below the far end | 100% | 100% | 0.0 s | 100% |
| VoLTE EVS 13.2, jitter buffer, sidetone 10 dB below the far end | 99.9% | 99.3% | 0.2 s | 99.8% |

The four scenarios below 97.7%:

- **2G rate drop with a handover (96.8%).** The loss is the handover gap plus about a second
  to change mode at each rate change.
- **2G cell edge (56%) and deep fade (78%).** No speech mode gets through AMR 5.9 kbit/s and
  below. The link stays up, and speech returns about a second after the rate recovers.
- **18 dB noise suppressor at both phones (76%).** The suppressor treats the tones as noise.

### Which codec settings carry speech

One-way, no frame loss or jitter, 40 codec settings (full table in
[repro_results/README.md](repro_results/README.md), section 1.3).

| Network | Calls can use the fast mode | Robust mode only | No speech |
|---|---|---|---|
| 2G/3G AMR | 12.2, 10.2 | 7.95, 7.4, 6.7 | 5.9 (about half the frames), 5.15, 4.75 |
| VoLTE AMR-WB | 12.65 and above | 8.85; 6.6 with losses (94–97% of frames) | |
| VoLTE EVS | 13.2 and above | 9.6, 8.0, 13.2 channel-aware; 7.2 and 5.9 variable rate with losses (91–100%) | |
| Transcoded through G.711 | | all eight combinations tested | |

A setting counts for the fast mode when at least 99% of fast-mode frames arrive and the
signal-to-distortion ratio is at or above the 4.5 dB at which a unit asks for that mode.
EVS 13.2 sits at that threshold with the fixed-point codec (4.5 dB, against 7.3 dB with
the floating-point one).

### Cross-checks

- **Fixed-point codecs.** These are the bit-exact codec definitions that phones are tested
  against. 56 more calls through them (2 per scenario, last column above) agreed with the
  floating-point results to within 1.5 points in 25 of 28 scenarios. The other three are
  the low-delivery scenarios, where two calls are too few to compare. In the one-way test
  the usable mode was the same for 39 of 40 codec settings.
- **Ten-minute runs.** Seven one-way runs of ten minutes never lost lock and recovered
  99.6% to 100% of frames.
- **Tests.** 42 automated tests pass (`python -m pytest -q tests`).

## 4. What was not achieved, and what the results do not show

### Not done

- **No real call.** Nothing has been sent through a real phone or network. The attached
  test phone has no SIM.
- **No real-time implementation.** The model runs offline. `veilspeak/live.py` is a sketch
  that has only run in a software loop.
- **No measurement of speech quality.** The results count frames. Nobody has assessed how
  intelligible Codec2 at 700 or 1200 bit/s is through this system.
- **No comparison with P3's modem.**
- **Not built:** post-quantum key exchange, re-keying during a call, remembering a partner
  between calls, handling both people switching on at once.

### Known limits of the design

- **Delay.** About 0.45 s each way in the fast mode and 0.64 s in the robust mode, on top of
  the call's own delay. These are design values, not measurements. The plan's target was
  0.2 s.
- **Lowest 2G rates.** No speech at AMR 5.9 kbit/s and below.
- **Not hidden.** The audio is plainly a modem signal. A network can detect it and could
  block it.
- **16-bit tag.** A forged or corrupted frame passes with a chance of 1 in 65,536 per
  attempt. The cost is up to 480 ms of wrong audio, not the key.

### What simulation cannot show

- **The phone's own audio processing** between the unit and the codec: noise suppression,
  echo control and gain control. It differs by phone and is unknown to us. The 18 dB
  result above shows the design is sensitive to it.
- **Network equipment on transcoded calls**, such as level control and noise reduction.
- **Real radio conditions.** Loss, jitter and handovers here follow simple statistical
  models.
- **Jitter buffers other than the EVS reference one.**
- **The gap between simulation and real calls.** P2's own real-call error rates were 5 to
  160 times worse than our codec simulation of its system (section 1). Our figures should
  be read with that in mind until real calls are measured.

### The next piece of evidence

`tools/linetest.py` makes a test recording to feed into a real call and checks a recording
of the far end, reporting how many frames survived in each mode. It has only been run on
simulated recordings.

## 5. Where things are

| Path | Contents |
|---|---|
| `veilspeak/` | The model. `veilspeak/README.md` describes the design and each file |
| `veilspeak/cellsim/` | The call simulator |
| `tools/` | `campaign.py`, `coverage.py`, `rxbench.py`, `soak.py`, `trace_call.py`, `linetest.py`, and the codec build scripts |
| `tests/` | The automated tests |
| `repro_results/` | All result files and `README.md` with every table |
| `plan_v2.md` | The project plan, with a status block. `plan.md` and `IMPLEMENTATION.md` are the earlier plan it replaced |
| `papers_text/` | Text of the five papers |
| `third_party/` | The 3GPP reference codecs (downloaded and built by `tools/build_codecs.sh`) |

To run one simulated call and write what each person hears as WAV files:

```
source .venv/bin/activate
python -m veilspeak "VoLTE EVS 13.2, jitter buffer, 1% loss"
```

## Sources

- P1: [arXiv 2102.09809](https://arxiv.org/abs/2102.09809)
- P2: [arXiv 2102.10869](https://arxiv.org/abs/2102.10869); authors' code at
  [osf.io/gujfp](https://osf.io/gujfp)
- P3: IEEE Access, 2024, DOI 10.1109/ACCESS.2024.3424315
- P4: IEEE Access, 2024, DOI 10.1109/ACCESS.2024.3405317
- P5: Symmetry 2025, 17, 365, DOI 10.3390/sym17030365
- ZRTP: RFC 6189
- 3GPP reference codecs: TS 26.104 and 26.073 (AMR), TS 26.204 and 26.173 (AMR-WB),
  TS 26.443 and 26.442 (EVS)
