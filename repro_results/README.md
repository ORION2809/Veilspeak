# Results

This folder holds two bodies of evidence:

1. **Part 1: the working model in simulated phone calls** (2026-11-17). The code in
   `veilspeak/` against a call simulator built on 3GPP's reference codecs.
2. **Part 2: the reproduction of paper P2** (2026-11-06), which the design started from.

Nothing here comes from a real phone call.

# Part 1. The working model in simulated calls, 2026-11-17

## Summary

- **168 complete two-way calls in 28 scenarios: every call agreed keys** with matching
  codes, 6.7 to 8.2 s after the unit was switched on. No frame with wrong content was
  accepted in any call.
- **24 scenarios delivered 97.7% to 100% of the speech.** They cover 2G full and half rate,
  VoLTE with AMR-WB and EVS, the VoLTE jitter buffer, frame loss, calls transcoded between
  networks, a handover from VoLTE to 2G in mid-call, sidetone and a 12 dB noise suppressor.
- **Four scenarios lose more**, and why is understood:
  - 2G rate drop with a handover: 96.8%. The loss is the handover and about a second of
    switching modes at each rate change.
  - 2G at the cell edge (AMR 5.9 and 4.75) and a 7-second deep fade: no speech mode gets
    through those rates. The link stays up and speech returns about a second after the
    rate recovers.
  - An 18 dB noise suppressor at both phones: 76%.
- **The fixed-point codecs give the same picture.** These are the bit-exact versions that
  phones are tested against: the mode each codec setting supports is the same in 39 of 40
  settings (section 1.3), and 56 complete calls through them gave the same results scenario
  by scenario (section 1.2).
- **Ten-minute runs** hold lock throughout and recover 99.6% to 100% of frames.

## How a simulated call works

Two units, A and B, each wired to a phone. The call starts as an ordinary one with both
people talking. After 1.4 to 4.3 s A's unit is switched on; B's unit hears it and answers;
the two agree keys; speech then flows both ways until 45 s.

Each direction of the call is: unit → sample-clock offset (25 and 35 parts per million) →
line noise → telephone-band filter → the codec legs of the scenario, with silence detection
on → telephone-band filter → unit. The simulation advances 480 ms at a time, so each unit
reacts only to audio it could already have received.

"Speech delivered" is the share of 40 ms speech frames that arrive bit for bit, counted from
the moment both ends are secure. A frame that fails its integrity check is dropped, never
played.

## 1.1 Complete calls

Six calls per scenario, 45 s each (`campaign.csv`, one row per call; `campaign.log`).
"Worst direction" is the lowest share in either direction of any of the six calls.

| Scenario | Speech delivered | Worst direction of any call | Longest gap | Share in fast mode |
|---|---|---|---|---|
| **2G** | | | | |
| 2G full rate, good signal | 100% | 100% | 0.0 s | 92% |
| 2G full rate, other silence detector | 100% | 100% | 0.0 s | 92% |
| 2G full rate, fading (3% frames lost in runs) | 99.0% | 98.6% | 0.2 s | 91% |
| 2G half rate (AMR 7.4), 1% loss | 99.9% | 98.6% | 0.5 s | 0% |
| 2G, rate drops 12.2 > 7.4 > 12.2, one handover | 96.8% | 95.1% | 0.5 s | 56% |
| 2G cell edge: 12.2 > 5.9 > 4.75 > 12.2 | 56.4% | 49.3% | 16.3 s | 34% |
| 2G deep fade: 12.2 > 4.75 for 7 s > 12.2 | 78.4% | 75.0% | 7.9 s | 62% |
| **VoLTE** | | | | |
| VoLTE AMR-WB 12.65, 0.5% loss | 99.9% | 99.3% | 0.2 s | 89% |
| VoLTE AMR-WB 23.85 | 100% | 100% | 0.0 s | 92% |
| VoLTE AMR-WB 8.85 (reduced rate) | 100% | 100% | 0.0 s | 0% |
| VoLTE EVS 13.2, jitter buffer, 1% loss | 99.9% | 98.6% | 0.5 s | 86% |
| VoLTE EVS 13.2, heavy jitter, delay spikes, 2% loss | 97.7% | 95.3% | 0.5 s | 74% |
| VoLTE EVS 9.6, jitter buffer | 100% | 100% | 0.0 s | 25% |
| VoLTE AMR-WB 12.65 inside an EVS phone, jitter buffer | 99.7% | 98.6% | 0.5 s | 62% |
| **Between networks** | | | | |
| 2G to 2G, both full rate (transcoded) | 100% | 100% | 0.0 s | 0% |
| 2G to 2G, both half rate (transcoded) | 98.6% | 95.7% | 0.5 s | 0% |
| VoLTE to 2G full rate (transcoded) | 100% | 100% | 0.0 s | 0% |
| VoLTE to 2G full rate (transcoded), network noise reduction 12 dB | 100% | 100% | 0.0 s | 0% |
| VoLTE to 2G half rate (transcoded) | 100% | 100% | 0.0 s | 0% |
| VoLTE to VoLTE across operators (transcoded) | 100% | 100% | 0.0 s | 0% |
| VoLTE EVS with jitter buffer to 2G half rate (transcoded) | 98.6% | 97.1% | 0.5 s | 0% |
| VoLTE call handed over to 2G after 20 s | 98.0% | 95.7% | 1.4 s | 27% |
| **Phone processing stand-ins, coupling, sidetone** | | | | |
| 2G full rate + noise suppressor 12 dB + gain control | 100% | 100% | 0.0 s | 91% |
| VoLTE AMR-WB 12.65 + noise suppressor 12 dB + gain control | 100% | 100% | 0.0 s | 65% |
| VoLTE AMR-WB 12.65 + noise suppressor 18 dB at both phones | 76.4% | 69.0% | 2.4 s | 0% |
| 2G full rate, low level (-12 dB), noisy line, 120 ppm clocks | 100% | 100% | 0.0 s | 92% |
| 2G full rate, sidetone 10 dB below the far end | 100% | 100% | 0.0 s | 0% |
| VoLTE EVS 13.2, jitter buffer, sidetone 10 dB below the far end | 99.9% | 99.3% | 0.2 s | 15% |

Notes:

- **Fast mode** is 2.4 kbit/s carrying Codec2 1200; otherwise speech goes in the robust
  mode, 1.2 kbit/s carrying Codec2 700C. Every call spends its first 3 s or so in the
  robust mode, so 92% is the most a 45 s call can show.
- **Cell edge.** For 16 s of each call the codec is at 5.9 or 4.75, where no speech mode
  gets through reliably. That is close to half the time speech could flow, so little more
  than half can arrive.
- **Sidetone.** Each unit hears its own signal 10 dB below the far end's. Speech is
  unaffected; the fast mode is mostly not used because the unit's own signal counts as noise.
  A unit could subtract its own signal, since it knows what it sent; the model does not.
- **Noise suppressors** here are textbook ones, not any phone's. See "What these results do
  not show".

## 1.2 The same calls through the fixed-point codecs

Two calls per scenario with every codec replaced by its fixed-point version (TS 26.073,
26.173 and 26.442), the bit-exact definitions that phones and network equipment are tested
against (`campaign_fixed.csv`, `campaign_fixed.log`). All 56 calls agreed keys with matching
codes in 6.7 to 8.2 s, and no frame with wrong content was accepted.

| Scenario | Floating-point codecs (6 calls) | Fixed-point codecs (2 calls) | Worst direction, fixed-point |
|---|---|---|---|
| 2G full rate, good signal | 100% | 100% | 100% |
| 2G full rate, other silence detector | 100% | 100% | 100% |
| 2G full rate, fading (3% frames lost in runs) | 99.0% | 98.8% | 98.0% |
| 2G half rate (AMR 7.4), 1% loss | 99.9% | 100% | 100% |
| 2G, rate drops 12.2 > 7.4 > 12.2, one handover | 96.8% | 97.0% | 96.0% |
| 2G cell edge: 12.2 > 5.9 > 4.75 > 12.2 | 56.4% | 58.9% | 52.8% |
| 2G deep fade: 12.2 > 4.75 for 7 s > 12.2 | 78.4% | 80.2% | 78.8% |
| VoLTE AMR-WB 12.65, 0.5% loss | 99.9% | 100% | 100% |
| VoLTE AMR-WB 23.85 | 100% | 100% | 100% |
| VoLTE AMR-WB 8.85 (reduced rate) | 100% | 100% | 100% |
| VoLTE EVS 13.2, jitter buffer, 1% loss | 99.9% | 100% | 100% |
| VoLTE EVS 13.2, heavy jitter, delay spikes, 2% loss | 97.7% | 98.8% | 98.0% |
| VoLTE EVS 9.6, jitter buffer | 100% | 99.8% | 99.3% |
| VoLTE AMR-WB 12.65 inside an EVS phone, jitter buffer | 99.7% | 100% | 100% |
| 2G to 2G, both full rate (transcoded) | 100% | 100% | 100% |
| 2G to 2G, both half rate (transcoded) | 98.6% | 98.7% | 96.0% |
| VoLTE to 2G full rate (transcoded) | 100% | 100% | 100% |
| VoLTE to 2G full rate (transcoded), network noise reduction 12 dB | 100% | 100% | 100% |
| VoLTE to 2G half rate (transcoded) | 100% | 100% | 100% |
| VoLTE to VoLTE across operators (transcoded) | 100% | 100% | 100% |
| VoLTE EVS with jitter buffer to 2G half rate (transcoded) | 98.6% | 98.3% | 97.2% |
| VoLTE call handed over to 2G after 20 s | 98.0% | 97.8% | 97.3% |
| 2G full rate + noise suppressor 12 dB + gain control | 100% | 100% | 100% |
| VoLTE AMR-WB 12.65 + noise suppressor 12 dB + gain control | 100% | 100% | 100% |
| VoLTE AMR-WB 12.65 + noise suppressor 18 dB at both phones | 76.4% | 71.8% | 58.6% |
| 2G full rate, low level (-12 dB), noisy line, 120 ppm clocks | 100% | 100% | 100% |
| 2G full rate, sidetone 10 dB below the far end | 100% | 100% | 100% |
| VoLTE EVS 13.2, jitter buffer, sidetone 10 dB below the far end | 99.9% | 99.8% | 99.3% |

The two sets agree to within 1.5 points in 25 of the 28 scenarios. The other three are the
ones where speech is partly lost anyway (cell edge, deep fade, 18 dB noise suppressor), where
two calls are too few to say more.

## 1.3 Which mode gets through which codec

One-way, no frame loss and no jitter, so this measures the codec alone: 4 runs of 70
superframes per mode and codec setting (`coverage.csv`, `coverage_fixed.csv`). SDR is the
signal-to-distortion ratio the receiver measures. A unit asks for the fast mode from 4.5 dB.

| Codec setting | Fast frames | Robust frames | Keep-alive frames | SDR | A call gets | Fixed-point codec: fast / robust / SDR |
|---|---|---|---|---|---|---|
| 2G/3G AMR 12.20 | 100% | 100% | 100% | 6.8 dB | fast mode | 100% / 100% / 6.8 dB |
| 2G/3G AMR 10.20 | 100% | 100% | 100% | 5.1 dB | fast mode | 100% / 100% / 5.0 dB |
| 2G/3G AMR 7.95 | 12.5% | 100% | 100% | 1.5 dB | robust mode | 7.1% / 100% / 1.5 dB |
| 2G/3G AMR 7.40 | 72.1% | 100% | 100% | 1.7 dB | robust mode | 69.0% / 100% / 1.7 dB |
| 2G/3G AMR 6.70 | 0.0% | 100% | 100% | -0.7 dB | robust mode | 0.0% / 100% / -0.7 dB |
| 2G/3G AMR 5.90 | 0.0% | 48.9% | 100% | -2.0 dB | no speech, link stays up | 0.0% / 44.9% / -2.0 dB |
| 2G/3G AMR 5.15 | 0.0% | 1.1% | 100% | -2.9 dB | no speech, link stays up | 0.0% / 0.7% / -2.9 dB |
| 2G/3G AMR 4.75 | 0.0% | 0.0% | 100% | -3.2 dB | no speech, link stays up | 0.0% / 0.0% / -3.2 dB |
| 2G/3G AMR 12.20, second silence detector | 100% | 100% | 100% | 6.8 dB | fast mode | 100% / 100% / 6.8 dB |
| 2G/3G AMR 7.40, second silence detector | 72.1% | 100% | 100% | 1.7 dB | robust mode | 69.0% / 100% / 1.7 dB |
| VoLTE AMR-WB 23.85 | 100% | 100% | 100% | 10.5 dB | fast mode | 100% / 100% / 10.5 dB |
| VoLTE AMR-WB 23.05 | 100% | 100% | 100% | 10.6 dB | fast mode | 100% / 100% / 10.7 dB |
| VoLTE AMR-WB 19.85 | 100% | 100% | 100% | 9.4 dB | fast mode | 100% / 100% / 9.4 dB |
| VoLTE AMR-WB 18.25 | 100% | 100% | 100% | 8.9 dB | fast mode | 100% / 100% / 8.9 dB |
| VoLTE AMR-WB 15.85 | 100% | 100% | 100% | 7.5 dB | fast mode | 100% / 100% / 7.5 dB |
| VoLTE AMR-WB 14.25 | 100% | 100% | 100% | 6.7 dB | fast mode | 100% / 100% / 6.7 dB |
| VoLTE AMR-WB 12.65 | 100% | 100% | 100% | 5.7 dB | fast mode | 100% / 100% / 5.7 dB |
| VoLTE AMR-WB 8.85 | 54.7% | 100% | 100% | 2.4 dB | robust mode | 59.1% / 100% / 2.4 dB |
| VoLTE AMR-WB 6.60 | 0.0% | 97.1% | 100% | -0.7 dB | robust mode with gaps | 0.0% / 93.8% / -0.8 dB |
| VoLTE EVS 24.4 wideband | 100% | 100% | 100% | 11.9 dB | fast mode | 100% / 100% / 11.9 dB |
| VoLTE EVS 16.4 wideband | 100% | 100% | 100% | 8.1 dB | fast mode | 100% / 100% / 8.2 dB |
| VoLTE EVS 13.2 wideband | 100% | 100% | 100% | 7.3 dB | fast mode | 100% / 100% / 4.5 dB |
| VoLTE EVS 9.6 wideband | 96.7% | 100% | 100% | 2.9 dB | robust mode | 96.6% / 100% / 3.0 dB |
| VoLTE EVS 8.0 wideband | 0.0% | 100% | 100% | 0.1 dB | robust mode | 0.0% / 100% / 0.7 dB |
| VoLTE EVS 7.2 wideband | 0.0% | 90.6% | 100% | -0.9 dB | robust mode with gaps | 0.0% / 98.6% / -0.4 dB |
| VoLTE EVS 5.9 variable rate | 0.0% | 98.6% | 100% | -0.3 dB | robust mode with gaps | 0.0% / 100% / -0.3 dB (robust mode) |
| VoLTE EVS 13.2 channel-aware | 75.5% | 100% | 100% | 3.5 dB | robust mode | 69.9% / 100% / 3.4 dB |
| VoLTE EVS 13.2 narrowband | 100% | 100% | 100% | 5.2 dB | fast mode | 100% / 100% / 5.3 dB |
| VoLTE EVS 9.6 narrowband | 97.3% | 100% | 100% | 3.2 dB | robust mode | 98.9% / 100% / 3.2 dB |
| VoLTE AMR-WB 12.65 inside an EVS phone | 100% | 100% | 100% | 4.4 dB | robust mode | 100% / 100% / 4.4 dB |
| VoLTE AMR-WB 8.85 inside an EVS phone | 0.0% | 100% | 100% | 1.2 dB | robust mode | 0.0% / 100% / 1.1 dB |
| landline G.711 | 100% | 100% | 100% | 20.1 dB | fast mode | 100% / 100% / 20.1 dB |
| across networks AMR 12.2 > G.711 > AMR 12.2 | 100% | 100% | 100% | 3.7 dB | robust mode | 100% / 100% / 3.7 dB |
| across networks AMR 12.2 > G.711 > AMR 7.4 | 0.4% | 100% | 100% | 0.2 dB | robust mode | 0.2% / 100% / 0.2 dB |
| across networks AMR 7.4 > G.711 > AMR 7.4 | 0.0% | 99.6% | 100% | -1.2 dB | robust mode | 0.0% / 99.6% / -1.2 dB |
| across networks AMR-WB 12.65 > G.711 > AMR 12.2 | 99.6% | 100% | 100% | 2.8 dB | robust mode | 99.3% / 100% / 2.8 dB |
| across networks AMR-WB 12.65 > G.711 > AMR 7.4 | 0.0% | 100% | 100% | -0.4 dB | robust mode | 0.0% / 100% / -0.4 dB |
| across networks AMR-WB 12.65 > G.711 > AMR-WB 12.65 | 93.5% | 100% | 100% | 2.8 dB | robust mode | 94.6% / 100% / 2.8 dB |
| across networks EVS 13.2 > G.711 > AMR 12.2 | 99.8% | 100% | 100% | 3.6 dB | robust mode | 95.7% / 100% / 2.0 dB |
| across networks EVS 13.2 > G.711 > EVS 13.2 | 94.2% | 100% | 100% | 2.2 dB | robust mode | 69.4% / 100% / 1.6 dB |

- **Fast mode** needs about 4 dB: pooled over 21 codec settings it lost no frame in 2,900
  at 4 dB or more, 10% at 3 to 4 dB and 24% at 2 to 3 dB (`fast_vs_sdr.txt`).
- **Robust mode** works down to about -1 dB.
- **Keep-alive frames** get through everything, including AMR 4.75.
- **EVS 13.2** is the one setting where the two codec builds differ noticeably: 7.3 dB with
  the floating-point codec, 4.5 dB with the fixed-point one. Both carry the fast mode, but
  on the fixed-point codec it sits at the threshold.

## 1.4 Ten-minute runs

One-way, one mode per path, sender and receiver clocks 40 parts per million apart
(`soak.txt`).

| Path | Mode | Frames recovered | Longest loss | Times lock was lost | Clock offset measured |
|---|---|---|---|---|---|
| 2G full rate AMR 12.2, 1% loss | fast | 99.84% | 0.24 s | 0 | 40 ppm |
| 2G half rate AMR 7.4, 1% loss | robust | 99.92% | 0.48 s | 0 | -40 ppm |
| VoLTE AMR-WB 12.65, 0.5% loss | fast | 99.96% | 0.24 s | 0 | 40 ppm |
| VoLTE EVS 13.2, jitter buffer, 1% loss | fast | 99.72% | 0.24 s | 0 | -40 ppm |
| VoLTE EVS 9.6, jitter buffer, 1% loss | robust | 99.60% | 0.48 s | 0 | 39 ppm |
| VoLTE > G.711 > 2G half rate | robust | 100% | none | 0 | -41 ppm |
| 2G lowest rate AMR 4.75 | keep-alive | 100% | none | 0 | 39 ppm |

## 1.5 Receiver benchmark

One-way, fixed mode, 8 runs of 70 superframes per row (`rxbench.txt`). This is the
regression test for the receiver; it runs in 90 s.

| Path | Mode | Frames recovered | Worst run |
|---|---|---|---|
| Clean wire | fast, robust, keep-alive | 100% | 100% |
| AMR 12.2, 3% loss in runs | fast | 99.1% | 98.6% |
| AMR 12.2, 3% loss in runs | robust | 99.8% | 98.6% |
| AMR 7.4, 1% loss | robust | 100% | 100% |
| AMR 4.75 | keep-alive | 100% | 100% |
| AMR-WB 12.65, 0.5% loss | fast, robust | 100% | 100% |
| VoLTE > G.711 > AMR 7.4 | robust | 100% | 100% |
| EVS 13.2, jitter 10 ms, 1% loss | fast | 99.9% | 99.3% |
| EVS 13.2, jitter 10 ms, 1% loss | robust | 100% | 100% |
| EVS 9.6, jitter 10 ms | robust | 100% | 100% |
| AMR-WB in an EVS phone, jitter 10 ms | robust | 100% | 100% |
| EVS 13.2, jitter 25 ms, delay spikes, 2% loss | fast | 98.7% | 97.8% |
| EVS 13.2, jitter 25 ms, delay spikes, 2% loss | robust | 98.7% | 97.1% |

Before the receiver was rewritten on 2026-11-17 the same benchmark gave 98.6% overall, with
one path at 87% (one run in eight never locked) and the heavy-jitter rows at 96.5%.

## 1.6 Experiments behind the design

Earlier on 2026-11-17, before the model was rebuilt:

- `bakeoff_quick.csv`, `bakeoff_round2.csv`, `bakeoff_round3.csv`, `bakeoff_round4.csv`:
  symbol error rates of candidate waveforms through every codec rate, with ideal timing.
  P2's Table 4 waveform fails at AMR 7.95 and below and at EVS below 13.2; the 23-tone,
  10 ms grid was chosen from these.
- Silence-detector probes (`tools/vad_probe.py`, `tools/vad_probe2.py`): a continuous modem
  signal is muted by both AMR detectors and by AMR-WB. An 80 ms pause after at most 640 ms
  of signal kept every detector treating it as speech; 40 ms pauses were not enough.
- `veilspeak_model_matrix.csv`: results of the first model (P2's Table 4 waveform, tested
  through AMR, Opus and Speex with silence detection off). Superseded.

## What these results do not show

- **The phone's own audio processing.** Between a unit and the codec, a real phone may run
  noise suppression, echo control and gain control that differ by chipset and tuning. The
  stand-ins here are textbook versions. If the unit feeds the codec directly inside the
  phone, this does not arise.
- **Network equipment on transcoded calls**: level control, noise reduction, echo
  suppressors. One scenario includes a 12 dB noise reducer; real ones are unknown.
- **Real radio conditions.** Frame loss, jitter and handovers here follow simple statistical
  models, not recorded networks.
- **Other phones' jitter buffers.** The simulated one is the EVS reference. It only ever
  moved our signal by whole 20 ms pieces inside a burst. The receiver also repairs shifts
  of other sizes (tested with synthetic cuts), but other buffers are untested.
- **Real time.** The model processes audio offline. Delay figures are design values.
- **How the speech sounds.** These numbers count frames; Codec2 at 700 and 1200 bit/s is
  intelligible but thin. `python -m veilspeak "<scenario>"` writes what each person hears.

## Regenerate

```
python tools/campaign.py repro_results/campaign.csv                    # 45 min
python tools/campaign.py repro_results/campaign_fixed.csv 2 45 "" --fixed   # about an hour
python tools/coverage.py repro_results/coverage.csv
python tools/coverage.py repro_results/coverage_fixed.csv 4 --fixed
python tools/soak.py > repro_results/soak.txt
python tools/rxbench.py > repro_results/rxbench.txt
```

Results vary a little from run to run: each call draws fresh keys, so the transmitted
signal differs even with the same seed.

---

# Part 2. Reproduction of P2 (Krasnowski, Data-over-Voice), 2026-11-06

Run on 2026-11-06 on a temporary GCE VM (`dov-repro-1`, e2-standard-4, project `mivi-phone-dev`, us-central1-a; now **stopped**).
The runs use the authors' Octave code from OSF [gujfp](https://osf.io/gujfp) (`Data_over_Voice.zip`), whose licence is CC BY-NC-ND 4.0.
The authors' functions were called **unmodified**; only the driver scripts `run_E*.m` are ours.
All channels are **simulated codecs**: AMR-NB `encoder_2018` 12.2k, Speex 1.2, and Opus-SILK via `opus_demo`.
There are no real network calls.

## Verdicts

| # | P2 claim | Result | Verdict |
|---|---|---|---|
| 1 | Codecs at 10–16 kbps leave a multi-tone signal at about 2–6 dB SNR (Fig. 3) | AMR 3.4–4.5 dB (10.2–12.2k); Speex 3.6–6.6 dB; SILK 1.6 dB (7k) to 6.1 dB (15k) | ✅ Reproduced |
| 2 | Per-tone distortion is a fixed phase shift plus roughly Gaussian, uncorrelated noise (Figs. 5–7) | Phase shift 0.55 rad at 400 Hz, falling to ≈0 at 3.2 kHz. Mardia kurtosis 7.7–8.7 (Gaussian = 8). Correlations ≤ 0.11. SILK noise rises with frequency. | ✅ Reproduced |
| 3 | Symbol errors rise in steps with codebook size and bitrate; channel correction helps (Fig. 11) | Same staircase. AMR is best and SILK 12k worst; SILK improves from 12k to 14k to 16k. Correction cuts errors by up to about 8× at high rates. | ✅ Trend reproduced (Fig. 11 is an image, so the comparison is qualitative) |
| 4 | Real-call symbol error rates (Tables 2, 3) | Real 3G in the paper is **5–160× worse** than our AMR simulation. Real VoIP apps in the paper are far *better* than our SILK-12k simulation, i.e. they behave like SILK at 24k or more. | ⚠️ Not reproducible in simulation; the simulation is not a stand-in for real calls |
| 5 | Full system, low mode 1.2 kbps / high mode 2.4 kbps (Tables 4, 5) | The OSF parameter sets **differ from Table 4** and carry only **1.07 / 2.13 kbps**. Results are in the E3 table below. | ⚠️ Partly reproduced: the code works, but not with the published parameters |

## Key numbers

### E1: authors' `DoV_simulation.m`
Setup: 8 harmonics, 400 baud. Symbol error rate (SER) by codebook size:

| Codebook | Bitrate | AMR 12.2k | Speex 12k | SILK 12k |
|---|---|---|---|---|
| 32 | 2.0 kbps | 2e-4 | 1.5e-3 | 1e-4 |
| 64 (paper's low mode) | 2.4 kbps | 4e-4 | 2.6e-3 | 1e-3 |
| 4096 | 4.8 kbps | 0.20 | 0.22 | 0.20 |

### E2: our AMR simulation vs the paper's real 3G calls (Table 2)
Setup: 10 harmonics, 200 baud; SER with correction.

| Bitrate | Our simulation | Paper, real 3G |
|---|---|---|
| 1.6 kbps | 5e-5 | 2.6e-4 |
| 2.0 kbps | 1e-4 | 1.2e-3 |
| 2.2 kbps | 1.5e-4 | 9.4e-3 |
| 2.4 kbps | 1e-4 | 1.6e-2 |

Full tables are in `E2_h10.csv` and `E2_h8.csv`.
The 8-harmonic, 1024-codebook row uses an older file because no `_new` version exists, so that row is worse than its neighbours.

### E3: full frames with RS(GF64) and erasures
Setup: authors' 3G/VoIP parameter sets, 300 frames per case.

| Mode | Channel | SER | FER | BER |
|---|---|---|---|---|
| 3G | AMR 12.2k | 2.4e-4 | 0/300 | 0 |
| 3G | SILK 12k | 0.020 | 0.0033 | 2.4e-4 |
| VoIP | AMR 12.2k | 0.19 | 0.53 | 0.054 |
| VoIP | SILK 12k | 0.18 | 0.56 | 0.069 |
| VoIP | SILK 16k | 0.014 | 0/300 | 0 |
| VoIP | SILK 24k | 0 | 0/300 | 0 |

Notes on E3:
- Erasure decoding only helped in cases that already fail (FER 0.53 → 0.49).
- A result of 0/300 frame errors means FER is below about 1% (95% confidence), not that it is zero.

### Differences between the OSF code and the paper's Table 4

| | Paper low (3G) | OSF `parameters_RS_real_3G` | Paper high (VoIP) | OSF `parameters_RS_real_VoIP` |
|---|---|---|---|---|
| Symbols | 8 harm × 64, 400 baud | 10 harm × 4096, 200 baud | 8 harm × 4096, 400 baud | 8 harm × 4096, 400 baud |
| Frame | 80 ms | 90 ms (16 + 2 training) | 60 ms | 90 ms (32 + 4 training) |
| RS | (28,20) | (28,20) | (40,28) | (56,40) |
| Speech bits / frame | 96 → 1.2 kbps | 96 → **1.07 kbps** | 144 → 2.4 kbps | 192 → **2.13 kbps** |

**The OSF frame timing cannot carry Codec2 1200/2400 in real time:**
- Codec2 1200 needs 108 bits per 90 ms; Codec2 2400 needs 216.
- Only the paper's Table 4 timing fits.
- plan_v2 already uses Table 4, but we have not yet run that exact configuration.

### Frame alignment
The demodulator assumes the signal is already aligned. The channel wrappers trim delays measured by hand (AMR 40 samples, SILK 52).
The code contains no timing recovery.

## What this meant for plan_v2
1. **Gates must be measured on real calls.** AMR simulation understates real 3G errors by 1–2 orders of magnitude. A SILK-12k simulation overstates errors on VoIP apps.
2. **Low mode looks fine on symbol errors alone.**
   - At the paper's real-3G SER of 1.3e-2, RS(28,20) fails only about 3e-5 of frames, if errors are independent.
   - The paper's real FER of 1.9e-2 therefore comes mostly from **loss of sync and dropouts**.
   - That is risk #10 (timing sync), and no reference code exists for it.
3. **High mode needs Opus/SILK at 16 kbps or more.** It fails through AMR and through SILK at 12k.
4. **Write the M1 modem with Table 4 timing**, and use the OSF code only as a reference for each function.

## Files (P2 reproduction)
- `E1_DoV_simulation.log`
- `E2_h8.csv`, `E2_h10.csv`
- `E3_fec.csv`
- `E5_snr.csv`, `E5_distortion.csv`
- `*.log`: raw Octave output
- `run_E2.m`, `run_E3.m`, `run_E5.m`: driver scripts (they run in `~/dov/DoV_technique` on the VM)
