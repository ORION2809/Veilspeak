# Veilspeak working model

Two people on an ordinary mobile phone call talk privately. A unit at each end turns speech
into a low-rate bit stream (Codec2), encrypts it (AES-256), adds error correction and sends
it as audio tones inside the call itself. The phone network carries those tones like any
other call audio; anyone listening in hears a modem, not the conversation.

**Scope.** Calls on the phone network: 2G/3G (AMR codec) and VoLTE (AMR-WB and EVS codecs),
including calls that cross between networks. No VoIP apps.

**Status.** Everything here runs in simulation. The simulated calls go through the real
codecs (3GPP's reference implementations), with their silence detectors, rate changes, frame
loss, the VoLTE jitter buffer and transcoding between networks. Nothing has been tried on a
real call yet. See "What simulation cannot show" at the end.

## Run it

```
source .venv/bin/activate
sh tools/build_codecs.sh                      # once: download and build the 3GPP codecs
sh tools/build_codecs_fixed.sh                # optional: the fixed-point versions, for the cross-checks
python -m pytest -q tests                     # 56 tests, about 2 minutes
python -m veilspeak                           # list the call scenarios
python -m veilspeak "VoLTE EVS 13.2, jitter buffer, 1% loss"     # one call; writes WAV files to demo_out/
```

| Tool | What it does | Time |
|---|---|---|
| `tools/campaign.py out.csv` | Six complete two-way calls in each scenario (`--fixed`: through the fixed-point codecs) | 45 min |
| `tools/coverage.py out.csv` | Each mode through every codec at every bit rate (`--fixed`: the fixed-point codecs) | 10 min |
| `tools/rxbench.py` | Receiver regression test: one-way, ten paths, eight runs each | 90 s |
| `tools/soak.py` | Ten-minute one-way runs | 5 min |
| `tools/trace_call.py "<scenario>" seed` | One call, one line per 480 ms: what was sent, what was received, why | 1 min |
| `tools/linetest.py make / check` | Test signal to play into a real call, and the check of a recording of it | seconds |

## How it works

**Signal.** 23 tones, 100 Hz apart, from 400 to 2600 Hz. Every 10 ms each tone takes one of
four phases; the 23 phases together form one symbol drawn from a codebook (P2's scheme, on a
different tone grid). Transmission is in superframes of 480 ms:

```
80 ms pause | 2 header symbols | 38 data symbols
```

The pause is there for the phone codecs' silence detectors: without it AMR and AMR-WB decide
the steady signal is background noise and stop transmitting it within seconds.

**Modes.** The header says which mode a superframe uses.

| Mode | Bits per 10 ms symbol | Error correction | Carries |
|---|---|---|---|
| FAST | 2 × 12 (even tones, odd tones) | two frames of Reed-Solomon (38,26) over GF(4096) | 480 ms of speech, Codec2 1200 |
| ROBUST | 12 | one frame of Reed-Solomon (38,30) over GF(4096) | 480 ms of speech, Codec2 700C |
| IDLE | 6 | Reed-Solomon (38,20) over GF(64) | nothing: keeps the link up when no speech mode gets through |
| CONTROL | 12 | as ROBUST, with a CRC | key-agreement messages |
| TRAIN | none: 40 known symbols | | lets the far end lock and measure the channel |

**Encryption.** Each frame is encrypted with AES-256 in counter mode and carries a 16-bit
HMAC-SHA256 tag and the low 6 bits of its frame counter. The counter also numbers the speech,
so a frame that arrives is always played at the right place. Keys come from an X25519
exchange modelled on ZRTP: the initiator commits to its key before seeing the other, and
both units show a 4-character code that the two people compare by voice. A matching code
means nobody sits in the middle.

**Receiver.** Phone audio arrives as one endless stream with unknown delay, a slightly
different sample clock, and (on VoLTE) a jitter buffer that stretches and squeezes it. The
receiver:

- locks by sliding the known TRAIN symbols along the audio, which needs no knowledge of the
  channel, and learns the channel's gain and noise per tone from them;
- tracks timing and clock drift from the phase slope across tones of every burst it decodes;
- reads each burst where it expects it. If a frame fails, it looks at where the burst ends
  and where a header may have moved to, and tries those places, accepting one only if a
  frame there passes its integrity check;
- splices a burst back together when audio was inserted or cut in the middle of it.

**Link control.** Every frame carries a 2-bit report from the receiving end: send FAST,
send ROBUST, send IDLE, or "I hear nothing". The sender obeys. A mode is dropped only after
repeated failures and retried after a hold-off, or at once when the channel has clearly
improved. An end that hears nothing mixes TRAIN and IDLE superframes into its speech so the
link re-forms by itself.

**Call start.** The call begins as an ordinary one. One person switches their unit on; the
other unit hears the training signal and answers by itself. Keys are agreed about 7 s later.

## Files

| File | Does |
|---|---|
| `modes.py` | Tone grid, superframe layout, the modes, pilot and training sequences |
| `waveform.py`, `codebook.py` | Symbol synthesis and analysis; codebooks with large minimum Lee distance (P2 §3.3) |
| `modem.py` | `Framer` (audio of a superframe) and `Receiver` (everything in "Receiver" above) |
| `fec.py` | Reed-Solomon frames with erasure decoding (P2 §5.1) |
| `crypto.py` | Per-frame encryption, tag, counter, replay window |
| `session.py` | Key agreement and the short code (after ZRTP, RFC 6189) |
| `vocoder.py` | Codec2 1200 and 700C |
| `endpoint.py` | One unit: call start, key agreement, speech both ways, link control |
| `live.py` | Block-based wrapper for sound hardware. Experimental: only ever run in a software loop |
| `cellsim/codecs.py` | The 3GPP codecs, floating-point and fixed-point builds |
| `cellsim/network.py` | Frame loss in runs, handovers, packet jitter and delay spikes |
| `cellsim/phone.py` | Stand-ins for phone audio processing: band filter, noise suppressor, gain control |
| `cellsim/call.py` | One direction of a call: phone, codec legs, transcoding, handover between networks |
| `cellsim/duplex.py` | A whole two-way call, advanced 480 ms at a time |
| `cellsim/scenarios.py` | The call scenarios |

## Results in simulation (2026-11-17)

Full tables and how to regenerate them: `../repro_results/README.md`.

- **168 complete two-way calls in 28 scenarios: every call agreed keys** with matching
  codes, 6.7 to 8.2 s after the unit was switched on. No frame with wrong content was
  accepted.
- **24 of the 28 scenarios delivered 97.7% to 100% of the speech**:

  | Kind of call | Speech delivered |
  |---|---|
  | 2G full rate and half rate, up to 3% frame loss | 99.0% to 100% |
  | VoLTE with AMR-WB (8.85 to 23.85) | 99.9% to 100% |
  | VoLTE with EVS and its jitter buffer | 99.7% to 100% |
  | VoLTE with heavy jitter, delay spikes and 2% loss | 97.7% |
  | Transcoded between networks (2G to 2G, VoLTE to 2G, VoLTE to VoLTE) | 98.6% to 100% |
  | VoLTE call handed over to 2G in mid-call | 98.0% |
  | 12 dB noise suppressor in the phone or in the network | 100% |
  | Each unit hearing its own signal 10 dB down (sidetone) | 99.9% to 100% |

- **Four scenarios lose more:**

  | Kind of call | Speech delivered | Why |
  |---|---|---|
  | 2G rate drop to 7.4 and back, with a handover | 96.8% | the handover gap, and about 1 s to change mode at each rate change |
  | 2G with 7 s at the lowest rate (AMR 4.75) | 78% | nothing carries speech at that rate; speech returns about 1 s after it ends |
  | 2G cell edge, 16 s at AMR 5.9 and 4.75 | 56% | the same |
  | 18 dB noise suppressor at both phones | 76% | the suppressor takes the tones for noise |

- **The fixed-point codecs agree.** They are the bit-exact versions that phones are tested
  against. Each mode gets through the same codec settings in 39 of 40 cases, and 56
  complete calls through them gave the same results scenario by scenario.
- **Ten-minute one-way runs** never lost lock and recovered 99.6% to 100% of frames.

Which codec settings carry what (one-way, codec alone):

| Network | Fast mode (Codec2 1200) | Robust mode only (Codec2 700C) | No speech |
|---|---|---|---|
| 2G/3G AMR | 12.2, 10.2 | 7.95, 7.4, 6.7 | 5.9 (half the frames), 5.15, 4.75 |
| VoLTE AMR-WB | 12.65 and above | 8.85; 6.6 with gaps | |
| VoLTE EVS | 13.2 and above | 9.6, 8.0, 5.9 variable rate, 13.2 channel-aware; 7.2 with gaps | |
| Transcoded through G.711 | | every combination tried, including half rate at both ends | |

## What was learned on the way

- **P2's waveform does not survive real codec settings.** Its Table 4 symbols (8 tones,
  2.5 ms) fail at AMR 7.95 and below and at EVS below 13.2, and silence detectors mute a
  continuous modem signal. Longer symbols on more tones, sent in bursts with pauses, do.
- **The jitter buffer is the hard part of VoLTE.** The EVS reference buffer shortens our
  pauses by anything up to 35 ms and inserts or drops 20 ms pieces in mid-burst. Following
  that took the heavy-jitter case from about 90% to 97.7%.
- **A header is not proof of a burst.** In a search over 170 ms of audio, some stretch of
  random data usually scores as high as a weak header. The receiver accepts a place only
  when a frame there passes its integrity check, and always tries the expected place first.
- **Looking for a moved header needs a tolerant measure.** A measure that is exact only
  when aligned to the sample misses half of all shifted headers when scanned in 2-sample
  steps.
- **Change mode on evidence, not on one bad frame.** Any channel loses single frames. The
  fast mode is dropped after two bad superframes in three, or when the measured
  signal-to-distortion ratio falls below 3.8 dB, and asked for again from 4.5 dB.
- **Each direction has its own training and pilot sequences**, so a unit cannot lock to
  the echo of its own signal. With one shared sequence it would, whenever the far end is
  quiet.

## Limits

- **Delay.** About 0.45 s each way in the fast mode and 0.64 s in the robust mode, on top of
  the call's own delay. That is the design value (a whole error-correction frame has to
  arrive before its speech can be played); the model does not run in real time. A
  conversation at this delay feels like a satellite link.
- **Lowest 2G rates.** No speech at AMR 5.9 and below. It would take a vocoder under about
  500 bit/s.
- **Speech quality.** Codec2 at 700 and 1200 bit/s: intelligible, robotic.
- **Not hidden.** The audio is plainly a modem. A network can detect it and could block it.
- **16-bit tag.** A forged or corrupted frame passes with chance 2⁻¹⁶ per try. That costs up
  to 480 ms of noise, not the key.
- **Roles.** The unit that is switched on first leads. Both people switching on at the same
  moment is not handled.
- **Not built.** Post-quantum key exchange, re-keying during a call, remembering a partner
  between calls (the key agreement supports it; the unit stores nothing), and the real-time
  port (`live.py` is a sketch).

## What simulation cannot show

The codecs here are the real ones. What stands between this model and a real call:

1. **The phone's own audio processing** between the unit and the codec: noise suppression,
   echo control, gain control. Unknown and different on every phone. A unit that feeds the
   codec directly inside the phone avoids it.
2. **Network equipment on transcoded calls** (level control, noise reduction).
3. **Real radio**: loss, jitter and handovers here are statistical models.
4. **Jitter buffers other than the EVS reference one.**
5. **Real-time behaviour**: processing load, actual delay, sound hardware.

## First steps on real phones

1. Put SIMs in two phones and place a call.
2. `python tools/linetest.py make line_test.wav`, feed that file into one phone in place of
   the microphone, record the other phone's earpiece signal, then
   `python tools/linetest.py check recording.wav`. It reports, per mode, how many frames
   survived that call. Repeat per network and codec.
3. Compare with the tables above. Where a real call is worse, the recording is the
   evidence to work from.
4. Only then try the two units live.
