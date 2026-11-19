# Testing Veilspeak on two real phones

Everything in this project has been measured in simulation only. This is the procedure for the
first real-phone tests, in the order that de-risks them. It will **not** "just work flawlessly"
the first time — the phone's own audio processing (echo canceller, noise suppressor, automatic
gain), the point where you inject and capture audio, and real-time timing are exactly the parts
simulation cannot show. The goal of this procedure is to make each failure **visible and
diagnosable**, so you learn what the real channel does instead of guessing.

Work through the stages in order. Do not start a stage until the previous one passes.

---

## What you need

- Two phones with SIMs on the network(s) you want to test (2G/3G, VoLTE).
- A way to put audio **into** a phone in place of its microphone and take audio **out** of its
  earpiece **electrically** — a wired headset breakout with an attenuator, or a USB/Bluetooth
  audio interface. Do **not** play through a loudspeaker into the microphone; room acoustics
  ruin the result.
- A computer running this project (the same one the simulations run on is fine).
- `pip install sounddevice soundfile` for the live stages.

> Note on injection: most current handsets have no analog headset jack. Going in over USB-C or
> Bluetooth adds **another** lossy audio codec (e.g. Bluetooth mSBC) and latency that is not in
> the simulation. If you use Bluetooth/USB audio, treat Stage 1 results as that path's results,
> not the cellular codec's alone.

---

## Stage 0 — software is ready (no phones)

```
source .venv/bin/activate
sh tools/build_codecs.sh          # once, if third_party/bin is empty
python tools/preflight.py         # must print GO
```

`preflight.py` confirms the codecs round-trip, that complete simulated calls secure with a
matching SAS and deliver speech, and that the real-time wrapper works back-to-back. If it does
not print **GO**, fix that first — the problem is in software, not the phones.

---

## Stage 1 — one-way line test (the first real-phone step)

This needs no keys and no second unit. It plays a known test signal through a call and measures
how much survived, per mode. Run it on every network and codec you care about.

```
python tools/linetest.py make line_test.wav            # ~1 minute test signal, 8 kHz
python tools/linetest.py make line_test.wav --rate 48000   # if your interface wants 48 kHz
```

1. Call phone B from phone A and answer.
2. Feed `line_test.wav` into phone A in place of its microphone, with the phone's own level
   meter showing a normal speech level.
3. Record phone B's earpiece **electrically** (headset/line output into a line input). Any
   sample rate, mono or stereo.
4. Check the recording:

   ```
   python tools/linetest.py check recording.wav
   ```

   It reports the recording's level, clipping, DC offset and channel, then per mode (FAST,
   ROBUST, IDLE) the share of frames recovered and the measured signal-to-distortion ratio, and
   a one-line verdict. Note which codec and rate the call used (the phone's engineering/field-
   test screen shows it) and label each recording with it.

Repeat per network, per codec, and at a few signal strengths (full bars, and a weak-signal spot
so the network drops to a lower rate). Compare against the simulated tables in
[`repro_results/`](../repro_results/). Where the real call is worse, the recording is the
evidence to work from.

**Target to move on:** at least one speech mode clears ~90% on your main network/codec.

---

## Stage 2 — two-way live secure call (the whole system)

Only after Stage 1 shows a mode that gets through. This runs a real unit at each end.

1. Pick the sound devices wired to each phone:

   ```
   python -m veilspeak.live --list
   ```

2. Calibrate the output level first so the injected tones are at speech level and **not
   clipping** — rerun a Stage 1 line test through the exact wiring you will use live and adjust
   `--level` until `linetest.py check` reports peak below about -3 dBFS and no clipping.

3. Start the unit on each phone. One side is the initiator:

   ```
   # phone A's computer
   python -m veilspeak.live --initiator --line-in <in> --line-out <out> --ui --seconds 120
   # phone B's computer
   python -m veilspeak.live             --line-in <in> --line-out <out> --ui --seconds 120
   ```

   `--say file.wav` sends a recording as your speech, `--mic <dev>` sends live microphone,
   `--heard out.wav` records what this end decoded.

4. When both screens show a code, **read the 4-character SAS aloud and confirm it matches** on
   both ends before trusting the call. Press `c` to confirm, space to mute.

**Expected, by design:** about 0.5–1 s of added delay each way on top of the call's own delay —
it will feel like a satellite link, and speech is intelligible but robotic (Codec2 at 700–1200
bit/s). That is normal, not a fault.

---

## When something fails — symptom to cause

| Symptom | Likely cause | What to do |
|---|---|---|
| `linetest check` finds **no training burst** | signal didn't get through, wrong channel, or wrong recording | try `--channel 1`; confirm you recorded the right phone; check the level line isn't silent |
| recording level **< -45 dBFS** or silent | capture gain too low, or tones muted by the phone | raise recording gain; try a louder injection level |
| **clipping** reported | injection or recording level too hot | lower `--level` / interface gain; clipping corrupts tone phases |
| FAST fails but ROBUST passes | codec too aggressive for 1200 bps (low rate, transcoding) | expected on low-rate/transcoded calls; use ROBUST |
| **nothing** in any mode, strong clean recording | the phone's noise suppressor is muting the tones | test with the phone's noise suppression off if possible; this is the #1 real-phone risk |
| live call **never secures** | one end not initiator, or far end answered too late, or levels off | exactly one `--initiator`; raise level; confirm Stage 1 passed on this wiring |
| live call secures then **goes silent for good** after a gap | a multi-second audio discontinuity | the receiver re-acquires after ~3 s; if it does not, capture the `--heard` file and the run and report it |
| SAS **differs** between the two screens | a man-in-the-middle, **or** a corrupted handshake | do not trust the call; retry; if it persists on a clean line it is a bug to report |

---

## What to record for every run

So a failure is reproducible: the network and codec/rate (from the phone's field-test screen),
the wiring (direct line / USB / Bluetooth), the `linetest check` output or the live session
summary, and the `--heard` WAV. Keep them next to the matching simulated numbers in
`repro_results/` — the gap between the two is the real finding.
