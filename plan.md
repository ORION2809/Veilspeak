# OPERATION VEILSPEAK — System Architecture Plan

## Steganographic Voice-Channel Encryption with Plausible Deniability

---

## Table of Contents

1. [Vision Statement](#1-vision-statement)
2. [Threat Model & Design Constraints](#2-threat-model--design-constraints)
3. [Paper-by-Paper Knowledge Extraction](#3-paper-by-paper-knowledge-extraction)
4. [System Architecture Overview](#4-system-architecture-overview)
5. [Phase 0 — Key Agreement (Autonomous, No PKI)](#5-phase-0--key-agreement)
6. [Phase 1 — Speech Semantics Extraction](#6-phase-1--speech-semantics-extraction)
7. [Phase 2 — Distortion-Tolerant Scrambling (Inner Encryption)](#7-phase-2--distortion-tolerant-scrambling)
8. [Phase 3 — Cover Voice Synthesis (Steganographic Encoding)](#8-phase-3--cover-voice-synthesis)
9. [Phase 4 — Codec Transit & Carrier Encryption (Transparent)](#9-phase-4--codec-transit--carrier-encryption)
10. [Phase 5 — Reception & Inner Decryption](#10-phase-5--reception--inner-decryption)
11. [Phase 6 — Semantic Reconstruction & Playback](#11-phase-6--semantic-reconstruction--playback)
12. [Security Analysis — "Weak-But-Enough"](#12-security-analysis--weak-but-enough)
13. [Plausible Deniability Analysis](#13-plausible-deniability-analysis)
14. [Data Flow Diagram](#14-data-flow-diagram)
15. [Parameter Selection & Tradeoffs](#15-parameter-selection--tradeoffs)
16. [Implementation Roadmap](#16-implementation-roadmap)
17. [Open Problems & Future Work](#17-open-problems--future-work)

---

## 1. Vision Statement

Build a voice communication system where:

| Property | Description |
|----------|-------------|
| **Dual-layer encryption** | Carrier's standard encryption wraps an inner steganographic encryption that the carrier cannot detect or strip |
| **Ciphertext = valid speech** | The inner-encrypted data is shaped to look like legitimate voice data — not noise, not static, but actual pronounceable speech parameters |
| **Plausible deniability** | An observer who decrypts the outer (carrier) layer finds well-formed voice audio and has reason to question whether any second layer even exists |
| **Deliberately calibrated strength** | Strong enough that real-time brute-force is impractical; weak enough that the "ciphertext looks like speech" constraint is achievable |
| **Voice-identity independence** | Neither sender's nor receiver's actual voice is transmitted — a shared TTS model speaks the semantic content in a neutral/mechanical voice |
| **Codec-faithful transport** | The encrypted signal must survive the phone's codec processing (AMR, Opus-Silk, etc.) with fidelity high enough that the carrier's own encryption/decryption succeeds and the inner encryption remains decodable |

### The Core Insight

> The phone's codec runs locally and strips "unnecessary" spectral content — that's fine.
> What leaves the phone is encrypted pseudo-speech that any codec, VAD, or classifier
> recognizes as legitimate voice. The carrier wraps it in standard encryption. When
> unwrapped, it reveals a plausible voice signal. Only the intended receiver, holding the
> shared inner key, can decode the true semantic content hidden within that plausible voice.

---

## 2. Threat Model & Design Constraints

### 2.1 Adversary Capabilities

| Adversary | Capability | What They See |
|-----------|-----------|---------------|
| **Passive network observer** | Can capture encrypted packets on the wire | Standard encrypted voice call — indistinguishable from any other call |
| **Carrier / lawful intercept** | Can decrypt the outer transport encryption (SRTP, etc.) | A voice audio stream containing what sounds like a person speaking — plausible cover speech |
| **Advanced signal analyst** | Can run statistical tests, spectral analysis, ML classifiers on the decrypted audio | Speech-like harmonic structure, valid formants, natural energy contour — designed to pass automated speech/non-speech classification |
| **Targeted cryptanalyst** | Suspects inner encryption exists; attempts to recover plaintext | Faces the full combinatorial key space of the inner scrambling. With deliberately reduced key sizes (64-80 bits), theoretically breakable in weeks/months of offline compute — but the call is already over |

### 2.2 What We Are NOT Defending Against

- Physical compromise of either endpoint device (if they own your phone, it's over)
- Rubber-hose cryptanalysis
- Real-time quantum computing attacks (not yet practical)
- Active MITM that can inject/modify packets in real-time while maintaining call quality (extremely difficult on voice channels)

### 2.3 Design Constraints

| Constraint | Source | Implication |
|-----------|--------|-------------|
| Narrowband voice channel: 300–3400 Hz | GSM/PSTN standards | All encoded information must live within this bandwidth |
| Wideband option: 50–7000 Hz | VoIP (Opus, AMR-WB) | More spectral room for encoding, better fidelity |
| Codec compression: 4.75–48 kbps | AMR-NB, Opus-Silk | Signal must survive lossy compression without catastrophic decoding errors |
| Voice Activity Detection (VAD) | All modern voice channels | Signal must not be suppressed as silence/noise — must register as "speech-like" |
| Real-time latency budget: < 200ms one-way | Conversational requirement | All processing (encode → encrypt → synthesize → analyze → decrypt → decode) must complete within this budget |
| Frame-synchronous operation | Real-time streaming | Encrypted signal duration = original speech duration (no bandwidth expansion) |

---

## 3. Paper-by-Paper Knowledge Extraction

### Paper 1 — Krasnowski et al. (2021): "Distortion-tolerant speech encryption"
**File**: `2102.09809v1.pdf`

**Key contributions to this plan:**
- **Distance-preserving encryption on hyperspheres**: Encrypts spectral envelope vectors (timbre) using pseudo-random rotations on S^15 via spherical commutative group codes built from the Gosset lattice Γ₈. The encrypted vector is ALSO a valid spectral envelope — this is the foundational technique for "ciphertext = valid speech."
- **Modular translation for pitch and energy**: Pitch period and log-energy are encrypted by addition modulo 2^16 — encrypted values remain within valid pitch/energy ranges.
- **Pseudo-speech synthesis**: Encodes encrypted parameters into harmonic waveforms using 16 square spectral windows over 300–6700 Hz. The signal has speech-like properties and passes VAD.
- **LPCNet-based neural vocoder for reconstruction**: On the receiver side, a trained neural vocoder (modified LPCNet) reconstructs intelligible speech from imperfectly-decrypted parameters, gracefully degrading with noise.
- **Proven computational indistinguishability**: Proposition 1 proves that with a secure PRNG (e.g., AES-CTR with 256-bit seed), the encrypted parameter sequence is computationally indistinguishable from random — no PPT adversary can distinguish encrypted speech from random pseudo-speech.
- **Tested on FaceTime (iPhone 6 to iPhone 6 over WiFi)**: Real-world proof that encrypted pseudo-speech survives VoIP transmission. MUSHRA quality assessment: 49% ("Fair") for FaceTime-transmitted decrypted speech.

**Parameters we adopt:**
- Frame length: 20ms speech → 25ms pseudo-speech (with 5ms guard periods)
- Spectral encoding: 9 mel-scaled triangular filters → D ∈ S⁸
- Scrambling model: Λ^(α) = (2π / 2^16√8) · Γ₈, yielding Z_{2^15} ⊕ Z_{2^16}⁶ ⊕ Z_{2^17} ≅ 2^128 possible spectral envelope encodings per frame
- Pitch/energy quantization: 2^16 levels each
- Key material per frame: 160 pseudo-random bits (10 integers from PRNG)

---

### Paper 2 — Krasnowski et al. (2022): "Novel Data over Voice technique"
**File**: `2102.10869v4.pdf`

**Key contributions to this plan:**
- **Codebook-based OFDM modulation optimized for LPC voice codecs**: Phase-modulated multi-harmonic symbols where each harmonic carries a 4-PSK symbol. Codebook design via quaternary error-correcting codes maximizing Lee distance.
- **Channel characterization**: Demonstrated that distortion introduced by AMR, Speex, and Opus-Silk is approximately **bivariate Gaussian** in the complex phase-amplitude plane, with a constant phase shift per harmonic. This means we can compensate for it.
- **Demodulation with phase-shift compensation and spectral weighting**: Equation (10) — compensates for codec-induced phase rotation and frequency-dependent distortion variance.
- **Achieved bitrates**: 2.4 kbps over 3G cellular with SER < 1.6×10⁻², up to 6.4 kbps over VoIP (FaceTime) with SER < 6.7×10⁻⁴.
- **Reed-Solomon error correction**: RS codes with 6-bit symbols and erasure decoding to protect encrypted bitstream against channel errors.
- **Silence insertion technique**: Periodically inserting silent frames to prevent VAD from suppressing the signal — validated to postpone or prevent signal attenuation.
- **Secure voice system design**: Complete system: Codec2 (1.2/2.4 kbps speech compression) → AES-256-CTR encryption → RS error correction → DoV modulation. Effective BER < 10⁻⁴ over FaceTime, < 3.7×10⁻³ over 3G.

**Parameters we adopt:**
- DoV symbol: K=8-10 harmonics at 400–3200 Hz (200 Hz spacing), 4-PSK per harmonic
- Symbol duration: 2.5–5 ms
- Codebook sizes: 64 (low mode, 3G) to 4096 (high mode, VoIP)
- Training period: 2–4 seconds for channel estimation (phase shift + distortion variance per harmonic)
- Frame structure: 80ms (low mode) / 60ms (high mode), with 10ms header for synchronization + 16-bit counter for re-sync after dropouts

---

### Paper 3 — Čubrilović et al. (2024): "FBMC/OQAM-Based Secure Voice Communications"
**File**: `FBMC_OQAM-Based_Secure_Voice_Communications_Over_Voice_Channels.pdf`

**Key contributions to this plan:**
- **FBMC/OQAM as superior alternative to plain OFDM**: Better spectral efficiency, reduced ISI/ICI due to well-localized pulse shaping (PHYDYAS half-Nyquist filter, K=4).
- **Speech-like waveform engineering**: Amplitude pulsing (factors 1.0, 0.8, 0.7 cycling across frames) + silent pauses → 10% BER improvement and successful VAD bypass.
- **Real-channel BER results**:
  - GSM 3G: Median BER achievable with FEC (Golay 12,24)
  - VoIP (Telegram, Signal): Higher BER due to symbol dispersion — mitigated by **subcarrier frequency adaptation** (avoiding error-prone frequency bands)
- **Frequency-dependent error patterns**: On VoIP channels, subcarriers 1,2,9,10 carry most errors; on 3G, subcarriers 5-8,13-16. Solution: **adaptively avoid bad subcarriers** per channel type.
- **PESQ quality scores**: MELPe 1200 bps baseline PESQ = 2.96. After channel transit, scores remain acceptable for intelligibility.
- **Parameters**: IFFT/FFT size 128, 16 subcarriers, fs=8kHz, Golay(12,24) FEC protecting 15% most sensitive bits.

**What we adopt:**
- Use FBMC/OQAM pulse-shaping for the pseudo-speech synthesis stage to improve spectral properties and codec survival
- Apply adaptive subcarrier selection based on channel type
- Use amplitude pulsing and silence insertion for VAD evasion

---

### Paper 4 — Pekerti et al. (2024): "Comprehensive Review of Steganography, Modem-Based Cryptography, and Chaotic Cryptography"
**File**: `Secure_End-to-End_Voice_Communication_A_Comprehensive_Review...pdf`

**Key contributions to this plan:**
- **Taxonomy of 33 systems across 39 papers**: Classifies all approaches into Steganography, Modem-based Cryptography (Codebook Optimization, Optimized Modulation, Parameter Mapping, Hardware Codec), and Chaotic Cryptography.
- **Gap analysis**: No single system satisfies ALL of: high throughput capacity (≥16 kbps), low BER, good speech quality (PESQ/MOS), AND standard security algorithm (AES/3DES). Our system targets this gap.
- **Steganography is most popular 2018-2022**: Highest throughput capacity achievable. But existing steganographic approaches have **low imperceptibility** (hiding voice in voice degrades cover) and **poor real-time compatibility**.
- **Codebook Optimization is most promising for standard crypto**: All solutions implement AES-256. All tested on real channels. But low throughput (≤6.4 kbps).
- **Parameter Mapping supports standard crypto**: 3DES-168, AES-128, AES-256 demonstrated. LPC and MBE models are the workhorses.
- **AI-based methods are underexplored**: Only 4 papers use AI. Major opportunity for neural vocoder / speech enhancement / channel prediction.
- **Key research gaps identified**:
  1. No system fulfills all evaluation parameters simultaneously
  2. Speech quality evaluation (subjective + objective) is rarely done properly
  3. Standard security algorithm integration remains challenging
  4. Signal synchronization is a critical unsolved problem
  5. AI-based methods have vast unexplored potential

**What we adopt:**
- Our system explicitly targets the identified gap: combining steganographic plausibility (Category 1) with standard crypto compatibility (Category 2) and AI-based vocoding
- Use the Parameter Mapping approach (LPC/MBE model) as our encryption-to-speech interface
- Implement proper quality assessment (PESQ + MUSHRA) in evaluation

---

### Paper 5 — Radomirović et al. (2025): "Perfectly Secret Autonomous Low-Bit-Rate Voice Communication"
**File**: `symmetry-17-00365-v2.pdf`

**Key contributions to this plan:**
- **Autonomous key generation — NO pre-shared keys, NO PKI, NO trusted third party**: Uses Sequential Key Distillation (SKD) protocol over a public authenticated channel. Common randomness extracted from cross-correlation between locally synthesized speech signals on both sides.
- **Source of common randomness**: `CR(Ŝ_lA, Ŝ_lB)` — locally synthesized signals using identical LSP parameters but independent local noise sources. Cross-correlation is ~10× higher than between original speech and synthesized speech.
- **Perfect secrecy via Vernam cipher**: One-time pad encryption of MELPe vocoder output (1.2 kb/s). Secret key rate demonstrated at **8.8 kb/s** — 7.3× the required rate.
- **LSP-based LP synthesizer for key generation**: Random LSP parameters (from FIFO buffer of previously distilled keys) drive synthesis filters. Innovation entropy comes from local true random noise (vTRNG). Total entropy: H(Ŝ_lA) = H(LSP) + H(ξ), where H(ξ) is dominant.
- **Privacy amplification via Huffman-Rényi difference**: New strategy for determining PA compression degree. Three strategies: "mean", "3σ", "3σ+s" — security margin s up to 460 bits ensures negligible leakage.
- **Real-world validation on vPCP-V platform**:
  - GSM 3G: BER = 1.20×10⁻³ (with FEC: 1.02×10⁻³)
  - GSM VoLTE: BER = 1.50×10⁻³ (with FEC: 1.20×10⁻³)
  - WhatsApp: BER = 5.50×10⁻³ (with FEC: 4.70×10⁻³)
  - Google Meet: BER = 7.90×10⁻³ (with FEC: 3.40×10⁻³)
- **SKD protocol phases**: Advantage Distillation (Bit Parity algorithm) → Information Reconciliation (8-bit Winnow algorithm) → Huffman encoding → Privacy Amplification (universal hash functions with Toeplitz structure)
- **FIFO buffer architecture**: Continuously replenished with freshly distilled keys. Reading rate must satisfy: R_K ≥ R_C + R_E (key rate ≥ cipher consumption + synthesizer consumption).
- **Open-phase bootstrapping**: ~1 second of open speech generates ~12.88 kb of key material — far exceeding the 160-bit minimum for initial FIFO fill.

**What we adopt:**
- The entire SKD-based autonomous key agreement system as our Phase 0
- The FIFO buffer architecture for continuous key supply
- The LSP-based synthesizer as our common randomness source during protected communication
- The MELPe vocoder as our low-bit-rate speech codec option
- The Vernam cipher structure (though we deliberately weaken it per our design goals — see Section 12)

---

## 4. System Architecture Overview

```
┌─────────────────────────── SENDER'S PHONE ───────────────────────────┐
│                                                                       │
│  ┌──────────┐    ┌──────────────┐    ┌────────────────────┐           │
│  │  Micro-  │───▶│  STT / Sem-  │───▶│  TTS / Neural     │           │
│  │  phone   │    │  antic Token │    │  Vocoder (shared   │           │
│  │          │    │  Extraction  │    │  model, mechani-   │           │
│  └──────────┘    └──────────────┘    │  cal voice)        │           │
│                                      └────────┬───────────┘           │
│                                               │                       │
│                                    Speech Parameters:                 │
│                                    (ε, p, D) per 20ms frame           │
│                                               │                       │
│                                               ▼                       │
│                         ┌─────────────────────────────────┐           │
│                         │  DISTORTION-TOLERANT SCRAMBLER  │           │
│                         │  (Inner Encryption)             │           │
│    ┌──────────┐        │                                 │           │
│    │ SKD Key  │───────▶│  • Pitch: mod-2^16 translation  │           │
│    │ FIFO     │  key   │  • Energy: mod-2^16 translation │           │
│    │ Buffer   │ stream │  • Timbre: Hypersphere rotation  │           │
│    └──────────┘        │    via Gosset lattice group code │           │
│         ▲              └────────────┬────────────────────┘           │
│         │                           │                                 │
│    ┌────┴─────┐          Encrypted but VALID speech params:           │
│    │ SKD      │          (ε̃, p̃, D̃) — each is a legitimate           │
│    │ Protocol │          vocal parameter value                        │
│    │ (public  │                     │                                 │
│    │ channel) │                     ▼                                 │
│    └──────────┘     ┌──────────────────────────────┐                 │
│                     │  PSEUDO-SPEECH SYNTHESIZER   │                 │
│                     │  • Harmonic waveform synth   │                 │
│                     │  • 16 spectral windows FDM   │                 │
│                     │  • FBMC/OQAM pulse shaping   │                 │
│                     │  • Amplitude pulsing + VAD   │                 │
│                     │    evasion                    │                 │
│                     └────────────┬─────────────────┘                 │
│                                  │                                    │
│                        PSEUDO-SPEECH AUDIO                            │
│                     (sounds like someone speaking)                    │
│                                  │                                    │
│                                  ▼                                    │
│                     ┌─────────────────────────┐                      │
│                     │  PHONE'S NATIVE CODEC   │  ◀── Happens on     │
│                     │  (AMR-NB / Opus-Silk)   │      device, this   │
│                     │  + VAD, AGC, NS         │      is fine         │
│                     └────────────┬────────────┘                      │
│                                  │                                    │
│                        COMPRESSED BITSTREAM                           │
│                                  │                                    │
│                                  ▼                                    │
│                     ┌─────────────────────────┐                      │
│                     │  CARRIER ENCRYPTION     │  ◀── Standard        │
│                     │  (SRTP / A5/3 / etc.)   │      outer layer     │
│                     └────────────┬────────────┘                      │
│                                  │                                    │
└──────────────────────────────────┼────────────────────────────────────┘
                                   │
                          ═══ NETWORK ═══
                                   │
┌──────────────────────────────────┼────────────────────────────────────┐
│                                  │       RECEIVER'S PHONE             │
│                                  ▼                                    │
│                     ┌─────────────────────────┐                      │
│                     │  CARRIER DECRYPTION     │  ◀── Outer layer     │
│                     │  (Standard, automatic)  │      removed         │
│                     └────────────┬────────────┘                      │
│                                  │                                    │
│                     DECOMPRESSED PSEUDO-SPEECH                        │
│                     (observer sees: valid voice audio)                │
│                                  │                                    │
│                                  ▼                                    │
│                     ┌─────────────────────────────┐                  │
│                     │  PSEUDO-SPEECH ANALYZER     │                  │
│                     │  • Pitch estimation (ML)    │                  │
│                     │  • Energy measurement       │                  │
│                     │  • DFT spectral windowing   │                  │
│                     │  • Torus projection          │                  │
│                     └────────────┬────────────────┘                  │
│                                  │                                    │
│                       Received params: (ε̃_rec, p̃_rec, D̃_rec)       │
│                       (distorted copies of encrypted params)          │
│                                  │                                    │
│                                  ▼                                    │
│                     ┌──────────────────────────────┐                 │
│    ┌──────────┐     │  DISTORTION-TOLERANT         │                 │
│    │ SKD Key  │────▶│  DESCRAMBLER                 │                 │
│    │ FIFO     │ key │  (Reverse inner encryption)  │                 │
│    │ Buffer   │     │  • Mod-2^16 subtraction      │                 │
│    └──────────┘     │  • Inverse torus translation  │                 │
│                     │  • Spherical coordinate       │                 │
│                     │    recovery                   │                 │
│                     └────────────┬─────────────────┘                 │
│                                  │                                    │
│                       Decrypted params: (ε_dec, p_dec, D_dec)        │
│                       (approximate — distortion-tolerant)             │
│                                  │                                    │
│                                  ▼                                    │
│                     ┌──────────────────────────────┐                 │
│                     │  NEURAL VOCODER (LPCNet)     │                 │
│                     │  Reconstructs intelligible   │                 │
│                     │  speech from parameters      │                 │
│                     │  (same TTS model as sender)  │                 │
│                     └────────────┬─────────────────┘                 │
│                                  │                                    │
│                                  ▼                                    │
│                            ┌──────────┐                              │
│                            │ Speaker  │                              │
│                            └──────────┘                              │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 5. Phase 0 — Key Agreement

### 5.1 Bootstrapping (Open Communication Phase)

**Duration**: 2–10 seconds of normal open speech at the start of a call.

**Process** (from Paper 5):

1. Both devices run their built-in **MELPe analyzers** on the open speech
2. Both devices simultaneously run **local MELPe synthesizers** using the same analyzed parameters
3. The locally synthesized signal `Ŝ_l` (transmitter) and reconstructed signal `Ŝ` (receiver) share **common randomness** from the LP synthesis filter while differing due to independent local noise sources
4. Execute **SKD protocol** over a public authenticated channel:
   - **Advantage Distillation**: Bit Parity (BP) algorithm, 2 iterations
   - **Information Reconciliation**: 8-bit Winnow algorithm
   - **Compression**: Optimal Huffman encoder
   - **Privacy Amplification**: Universal hash functions (Toeplitz matrix), strategy "3σ+s"
5. Average distilled key rate: **12.88 kb/s** — just 1 second of open speech fills the FIFO with ~12,880 bits (only 160 bits minimum needed)

**Security during bootstrap**: Since communication is open anyway, the ~6000 parity bits exchanged over the public channel leak information about the open speech — but that speech is already public. No security impact.

### 5.2 Continuous Key Generation (Protected Phase)

**Process** (from Paper 5):

1. Both devices maintain **FIFO buffers** of identical secret content
2. FIFO content is interpreted as randomly-selected **LSP parameters** (Algorithm 1 from Paper 5)
3. Both devices feed these LSP parameters into **local LP synthesizers** to generate signals `Ŝ_lA` and `Ŝ_lB`
4. These signals share common randomness (same synthesis filter) but differ (independent local noise `ξ` from true random number generator)
5. SKD protocol runs continuously over the public channel on this source
6. Distilled keys replenish the FIFO buffers

**Key property**: `I(S, Ŝ_lA) = 0` and `I(S, Ŝ_lB) = 0` — the synthesized signals are **statistically independent** of the actual input speech. Eve learns NOTHING about the real conversation from the public channel during protected mode.

**Key rate in protected mode** (from Paper 5, Table 3):
| SNR (dB) | Key Rate (kb/s) | Key Acceptance Rate | Leakage Rate (b/b) |
|----------|----------------|--------------------|--------------------|
| 29.8     | 3.04 ± 1.06    | 100%               | 0.0042             |
| 39.9     | 6.44 ± 1.35    | 100%               | 0.0015             |
| 50.0     | 9.96 ± 1.54    | 100%               | 0.0011             |

**FIFO consumption constraint**: `R_K ≥ R_C + R_E` where R_C = 1.2 kb/s (Vernam cipher) and R_E is synthesizer LSP consumption. Easily satisfied at all SNR values above 29.8 dB.

### 5.3 Deliberate Weakening for Our Design

Paper 5 achieves **perfect secrecy** via Vernam cipher. For our "weak-but-enough" design:

- **Option A — Reduced key reuse cycle**: Instead of one-time pad, reuse key material across N frames (e.g., N=50 → same key for 1 second). This dramatically reduces key consumption while maintaining practical unbreakability.
- **Option B — Truncated PRNG seed**: Use AES-CTR with 64-bit or 80-bit seeds instead of 256-bit. The scrambling is identical in structure but the key space shrinks from 2^256 to 2^64–2^80.
- **Option C — Reduced scrambling resolution**: Quantize pitch/energy to 2^10 levels instead of 2^16 — fewer possible translations means smaller effective key space.

The choice among these determines the precise security-vs-steganographic-quality tradeoff.

---

## 6. Phase 1 — Speech Semantics Extraction

### 6.1 Why Separate Semantics from Voice

Your voice is **not transmitted**. Instead:

1. Your voice carries **meaning** (semantic content)
2. Your voice also carries **identity** (speaker characteristics, emotional prosody)
3. We extract (1) and discard (2)
4. A shared TTS model on both sides re-speaks the meaning in a **neutral mechanical voice**

This serves three purposes:
- **Privacy**: Your voiceprint is never on the wire
- **Steganographic plausibility**: The cover voice parameters are generated by the encryption system, not constrained by your actual voice
- **Codec compatibility**: The TTS model can be tuned to produce signals that survive codec processing optimally

### 6.2 Implementation

**On-device pipeline**:

```
Microphone → [VAD] → [STT Model] → text/tokens → [TTS Model] → speech parameters
                                                         │
                                              (ε, p, D) per 20ms frame
```

**STT options** (all run locally on-device):
- Whisper (OpenAI) — small/medium models, ~1-5 GFLOPS
- Vosk — lightweight offline STT
- Custom phoneme recognizer (lower latency, simpler)

**TTS options** (deterministic, both sides have identical model):
- LPCNet-based synthesizer (from Paper 1) — directly outputs harmonic parameters
- Piper TTS — lightweight, configurable voice
- Custom harmonic parameter generator from phoneme sequence

**Output**: Per 20ms frame:
- `p_init` — pitch period (samples/cycle), bounded to [16, 128] at fs=8kHz
- `ε_init` — frame energy, bounded to [10, 10^8]
- `D_init ∈ S⁸` — normalized 9-component spectral envelope vector

---

## 7. Phase 2 — Distortion-Tolerant Scrambling

### 7.1 Pitch Encryption

```
p_init ──[linear scale]──▶ κ_init ∈ [κ_low, κ_high] ⊂ Z_{2^16}
                                       │
                              κ_enc = (κ_init + ν₁) mod 2^16
                                       │
                           ──[linear scale]──▶ p̃_enc ∈ [p̃_min, p̃_max]
```

- `ν₁ ∈ Z_{2^16}` from PRNG key stream
- Guard bounds: `κ_low = 2^13`, `κ_high = 2^16 - 2^13 - 1`
- Pseudo-speech pitch range: `[p̃_min, p̃_max] = [80, 160]` samples (at 16kHz)
- **Result**: Encrypted pitch is a valid fundamental frequency (100–200 Hz range)

### 7.2 Energy Encryption

```
log₁₀(ε_init) ──[linear scale]──▶ ρ_init ∈ [ρ_low, ρ_high] ⊂ Z_{2^16}
                                              │
                                     ρ_enc = (ρ_init + ν₂) mod 2^16
                                              │
                              ──[linear scale]──▶ ε̃_enc ∈ [ε̃_min, ε̃_max]
```

- `ν₂ ∈ Z_{2^16}` from PRNG key stream
- Pseudo-speech energy range: `[ε̃_min, ε̃_max] = [10^9, 10^10]`
- **Result**: Encrypted energy is a valid frame energy — the signal is always "loud enough" to avoid VAD suppression

### 7.3 Spectral Envelope (Timbre) Encryption

This is the most mathematically sophisticated part.

**Step 1 — Map to intermediate domain**:
```
D_init ∈ S⁸ ──[γ_ξ]──▶ 2·γ_ξ(D_init) ∈ P_ξ = Π[0, 2π/√8)⁸
```
where `ξ = (1/√8)[1,...,1]ᵀ ∈ R⁸`

**Step 2 — Quantize to lattice**:
```
χ_init = FindClosestVector(2·γ_ξ(D_init); Λ^(α)/Λ^(β))
```
where:
- `Λ^(β) = (2πZ⁸/√8)` — orthogonal lattice
- `Λ^(α) = (2π / 2^16√8) · Γ₈` — scaled Gosset lattice
- Quotient group: `Z_{2^15} ⊕ Z_{2^16}⁶ ⊕ Z_{2^17}`
- Order: `M = 2^128` possible encodings

**Step 3 — Encrypt by random lattice translation**:
```
χ_enc = χ_init + ν₃α₁ + ν₄α₂ + ... + ν₁₀α₈  mod Λ^(α)/Λ^(β)
```
where `ν₃ ∈ Z_{2^15}`, `ν₄,...,ν₉ ∈ Z_{2^16}`, `ν₁₀ ∈ Z_{2^17}`

**Step 4 — Map to codeword on hypersphere**:
```
D̃_enc = Φ_ξ(χ_enc) ∈ S¹⁵
```

**Result**: `D̃_enc` is a valid point on the unit hypersphere S^15 — it represents a legitimate spectral envelope that could correspond to a real speech sound. The encrypted timbre might sound like a different vowel, a different speaker, or a different phoneme — but it IS a valid formant structure.

### 7.4 Distortion Tolerance

The critical property: if the received `D̃_rec` is close to `D̃_enc` (small transmission distortion), then the decrypted `D_dec` will be close to `D_init`:

```
‖D_init - D_dec‖ ≤ (π√2) · ‖D̃_enc - D̃_rec‖
```

Expansion factor `τ = π/2ξ_min ≈ 4.44` — transmission errors are amplified by ~4.4× but remain bounded. This is what makes the system survive codec compression.

### 7.5 PRNG Key Stream Generation

Per frame (20ms), the scrambler consumes:
```
ν = [ν₁, ν₂, ν₃, ..., ν₁₀]
     16   16   15   16  16  16  16  16  16  17  = 160 bits
```

At 50 frames/second: **8000 bits/second** key stream consumption.

**PRNG**: AES-256-CTR mode (NIST SP 800-90A). One 256-bit seed securely produces up to 2^48 bitstrings of length 2^19. That's >1 minute of one-way communication per seed.

For **deliberately weakened** version: Use AES-CTR with 64-80 bit effective key (pad remaining seed bits with known value).

---

## 8. Phase 3 — Cover Voice Synthesis

### 8.1 Harmonic Waveform Generation

Each 25ms pseudo-speech frame encodes `(p̃_enc, ε̃_enc, D̃_enc)` into:

```
y[n] = Σ_{k=1}^{K(ω₀)} η·Aₖ·cos(k·ω₀/fs·n + φₖ - k·ω₀/fs·80)
```

where:
- `n = 0, 1, ..., 399` (400 samples at fs=16kHz)
- `ω₀ = 2π·fs / p̃_enc` — fundamental frequency from encrypted pitch
- `Aₖ, φₖ` — amplitudes and phases encoding `D̃_enc`
- `η` — scaling factor matching `ε̃_enc`
- Guard periods: first and last 5ms (80 samples each)

### 8.2 Spectral Shaping

16 square-shaped spectral windows uniformly distributed over 300–6700 Hz:

```
D̃_enc ⊙ W₁₆ = H·B(ω₀)·Ǎ
```

Solve for complex amplitudes `Ǎ` via Moore-Penrose pseudo-inverse:

```
Ǎ = (H·B(ω₀))† · (D̃_enc ⊙ W₁₆)
```

**Properties**:
- Pseudo-inverse minimizes amplitude magnitudes → reduces clipping risk
- Pre-computable matrices → real-time performance
- Encoding error RMSE ≈ 0.011 — negligible compared to channel distortion

### 8.3 FBMC/OQAM Enhancement (from Paper 3)

For improved spectral efficiency and codec survival:
- Apply **PHYDYAS half-Nyquist prototype filter** (K=4) for pulse shaping
- Use **PPN-FBMC/OQAM** implementation (2 M-point IFFTs + 2 PPNs)
- OQAM preprocessing: split real/imaginary, apply phase rotation `θₖ(n) = j^k`
- **Amplitude pulsing**: Cycle amplification factors [1.0, 0.8, 0.7] across consecutive frames
- **Silence insertion**: Every 16th frame replaced with silence to defeat VAD

### 8.4 What the Output Sounds Like

To a human listener, the pseudo-speech sounds like:
- A person speaking in an **unknown language** — harmonic, voiced, with changing pitch and formant structure
- Or **mumbling/babbling** — recognizable as speech but not intelligible
- The energy contour rises and falls naturally (because encrypted energy values remain in valid ranges)
- The pitch varies frame-to-frame (because encrypted pitch values remain in valid ranges)
- The spectral shape changes (because encrypted spectral envelopes remain valid formant structures)

To an **automated classifier** (VAD, speech detector):
- Harmonic structure ✓
- Formant-like spectral peaks ✓
- Speech-range fundamental frequency ✓
- Natural energy dynamics ✓
- **Verdict: Speech** ✓

---

## 9. Phase 4 — Codec Transit & Carrier Encryption

### 9.1 What Happens Automatically

```
Pseudo-speech audio
        │
        ▼
┌────────────────────────────┐
│ Phone's audio processing   │
│ • High-pass filter         │
│ • Noise suppression        │
│ • Adaptive gain control    │
│ • Voice Activity Detection │  ◀── All of these PASS because
│ • LPC analysis             │      the signal has speech properties
│ • Codec compression        │
│   (AMR-NB / Opus-Silk)     │
└────────────┬───────────────┘
             │
             ▼
    Compressed bitstream
             │
             ▼
┌────────────────────────────┐
│ Carrier transport          │
│ • SRTP encryption          │  ◀── This is the "outer layer"
│ • Network transmission     │      that happens normally
│ • SRTP decryption          │
│ • Codec decompression      │
└────────────┬───────────────┘
             │
             ▼
    Received pseudo-speech audio
    (distorted but structurally intact)
```

### 9.2 Codec Survival — Why It Works

From Paper 1 (Section 4.2) and Paper 2 (Section 3.1):

**LPC codecs preserve speech-like parameters by design.** They are optimized to:
- Accurately encode the spectral envelope (formants) → Our D̃_enc survives
- Preserve fundamental frequency → Our p̃_enc survives  
- Maintain energy contour → Our ε̃_enc survives

**What gets destroyed**: Fine time-structure, exact phase relationships, non-speech spectral content.

**What survives**: Exactly the parameters we encoded our data into.

**Measured distortion** (Paper 1, Table 3 — FaceTime):
| Parameter | RMSE (received vs. encrypted) |
|-----------|------------------------------|
| Energy    | 1644.73                      |
| Pitch     | 867.30                       |
| Spectral  | 0.16                         |

These errors are within the distortion-tolerant bounds of the descrambling algorithm.

---

## 10. Phase 5 — Reception & Inner Decryption

### 10.1 Signal Analysis

From each received 25ms pseudo-speech frame `ŷ_t`:

1. **Pitch estimation**: Maximum-likelihood estimator (no tracking — pitch is uncorrelated across frames in encrypted signal)
   ```
   p̃_rec = 2π·fs / ω̂₀
   ```

2. **Energy measurement**:
   ```
   ε̃_rec = Σ ŷ²[n]  for n=80..319
   ```

3. **Spectral envelope recovery**:
   ```
   D̂_rec = ℜ(2H·Ŷ ⊙ W̄₁₆) / ‖ℜ(2H·Ŷ ⊙ W̄₁₆)‖
   ```
   Then project to flat torus: `D̃_rec = Project(D̂_rec; ξ)`

### 10.2 Descrambling

**Pitch**: `κ_dec = (κ_rec - ν₁) mod 2^16` → linear scale back to `p_dec`

**Energy**: `ρ_dec = (ρ_rec - ν₂) mod 2^16` → linear scale back to `ε_dec`

**Spectral envelope**:
```
χ_dec = Φ⁻¹_ξ(D̃_rec) - ν₃α₁ - ... - ν₁₀α₈  mod Λ^(α)/Λ^(β)
```
Correct coordinates exceeding `πξᵢ`, then:
```
D_dec = γ⁻¹_ξ(χ_dec / 2)
```

### 10.3 Error Handling

**Large deciphering errors** (pitch/energy wrapping around modular bounds) are detectable and can be:
- **Pitch**: Smoothed by a simple tracker at the output (since natural speech has correlated pitch)
- **Energy**: Silent frames misinterpreted as loud → perceptually more damaging; guard bounds mitigate
- **Spectral**: Wrong torus projection causes unpredictable formant change in single frame → audible as "flutter"

---

## 11. Phase 6 — Semantic Reconstruction & Playback

### 11.1 Neural Vocoder Speech Synthesis

The decrypted parameters `(p_dec, ε_dec, D_dec)` are fed to a **narrowband LPCNet** vocoder (from Paper 1):

1. Compute feature vector: `[C₀, C₁, ..., C₈, ρ]` where `Cᵢ` are 9-band MFCCs from DCT of `log₁₀(E)`, and `ρ = (p_dec - 100)/50`
2. Frame-rate network (2 conv layers + 2 FC layers) → conditioning vector `f` (128-dim)
3. Sample-rate network (GRU-A 384 + GRU-B 16 + dual FC) → predicts excitation samples autoregressively
4. Output: `s[n] = s̃[n] + e[n]` where `s̃[n]` is linear prediction from LPC coefficients derived from energy bands

**Result**: Intelligible speech in a **mechanical/neutral voice** speaking the sender's intended words.

### 11.2 Quality Expectations

From Paper 1 (Section 5.3):
- **Intelligibility**: ~86% word recognition (vs. 97.5% reference) — sufficient for conversation
- **Quality** (MUSHRA):
  - No distortion: 64% ("Good/Fair")
  - After FaceTime: 49% ("Fair")
  - After AWGN @15dB: 46% ("Fair")
  - After AWGN @10dB: 19% ("Poor/Bad") — this is the lower operational limit

---

## 12. Security Analysis — "Weak-But-Enough"

### 12.1 Security Spectrum

| Security Level | Key Size | Time to Brute Force | Suitability |
|---------------|----------|-------------------|-------------|
| **Full (Paper 5)** | 256-bit AES + OTP | Computationally impossible | Overkill for our purpose; makes steganography harder |
| **Strong** | 128-bit effective | ~10^24 years on modern hardware | Still overkill |
| **Calibrated target** | 80-bit effective | ~10^9 years on modern hardware | Unbreakable in any practical timeframe |
| **Minimum viable** | 64-bit effective | ~500 years on modern hardware; ~days with nation-state resources | The sweet spot: practically secure for ephemeral calls, theoretically breakable with extraordinary effort |
| **Too weak** | <48-bit | Hours to days | Below threshold |

### 12.2 Recommended Configuration

**64-80 bit effective key per call session**, implemented as:
- AES-128-CTR with 80-bit secret seed (48 bits zero-padded)
- Generates 160-bit key stream per frame × 50 frames/sec = 8000 bits/sec
- Session key rotated every call (derived from SKD protocol)

**Why this is "enough":**
- A phone call is ephemeral — by the time a 64-bit key is brute-forced, the conversation is long over
- The adversary doesn't even know there IS an inner encryption (plausible deniability)
- They'd have to first suspect, then capture, then identify the encoding scheme, then brute-force — total effort makes it not worthwhile

**Why this is "weak enough" to enable steganography:**
- Lower key sizes mean less randomness injected per frame
- This means encrypted parameters can be constrained to stay closer to "natural" speech parameter distributions
- With 2^128 possible spectral envelope translations but only 2^64 keys explored, the mapping can be biased toward perceptually natural-sounding envelopes

### 12.3 Formal Security Properties

From Paper 1, Proposition 1:
> For any PPT adversary A, |Pr(PrivK^eav_{A,Π}(λ) = 1) - 0.5| ≤ negl(λ)

This holds as long as the PRNG is secure. With 64-bit seeds, λ=64, and negl(64) is effectively zero for any real-world adversary without nation-state quantum computing.

### 12.4 What an Attacker Faces

1. **Step 1**: Detect that inner encryption exists (defeat plausible deniability — see Section 13)
2. **Step 2**: Identify the encoding scheme (which spectral windows? which lattice? which modular arithmetic?)
3. **Step 3**: Capture sufficient ciphertext (at least several seconds of call)
4. **Step 4**: Brute-force the 64-80 bit key space
5. **Step 5**: For each candidate key, attempt descrambling and check if output "sounds like speech"
6. **Step 6**: Determine which of potentially many "plausible" decryptions is the true one

Steps 1-2 require reverse-engineering the system. Step 4 requires 2^64–2^80 AES operations. Steps 5-6 require automated speech quality assessment for each candidate — a massive computational multiplier.

---

## 13. Plausible Deniability Analysis

### 13.1 What the Observer Sees (After Carrier Decryption)

| Observable | What It Looks Like | What It Actually Is |
|-----------|-------------------|-------------------|
| Time-domain waveform | Harmonic speech signal with natural amplitude envelope | Pseudo-speech encoding encrypted parameters |
| Spectrum | Formant structure with fundamental frequency and harmonics | Scrambled spectral envelope mapped to valid formant positions |
| Pitch contour | Varying fundamental frequency in human speech range (100-200 Hz) | Encrypted pitch values, each independently valid |
| Energy contour | Rising and falling loudness, with quiet periods | Encrypted energy values, constrained to valid range |
| Spectrogram | Speech-like patterns with formant transitions | Each frame's encrypted spectral envelope appears as legitimate formant structure |

### 13.2 Statistical Tests an Adversary Might Apply

| Test | Result on Our Signal | Result on Real Speech | Distinguishable? |
|------|---------------------|---------------------|-----------------|
| VAD classification | "Speech" | "Speech" | No |
| Fundamental frequency range | 100–200 Hz | 80–300 Hz | Marginal (our range is subset) |
| Formant structure | Valid formants (encrypted spectral envelopes are valid points on S^15) | Valid formants | No |
| Frame-to-frame pitch correlation | Low (each frame encrypted independently) | High (natural speech has pitch continuity) | **Potentially yes** — this is a weakness |
| Frame-to-frame spectral correlation | Low | Medium-high | **Potentially yes** |
| Phonetic analysis | No recognizable phonemes/words | Recognizable language | **Yes** — an attentive human listener would notice |

### 13.3 Mitigations for Distinguishability

**Problem**: Independent frame-by-frame encryption destroys temporal correlations that real speech has.

**Mitigation 1 — Correlated encryption offsets**:
Instead of independent `ν₁, ν₂` per frame, use a **slowly varying** key stream:
```
ν₁[t] = ν₁[t-1] + Δν₁[t]
```
where `Δν₁[t]` is small. This preserves some temporal continuity in the encrypted pitch/energy.

**Mitigation 2 — Cover speech injection**:
Generate a **baseline cover speech signal** (e.g., from a text-to-speech engine reading generic text) and ADD the encrypted perturbations on top:
```
p̃_enc = p_cover + encrypt(p_init - p_cover)
```
The output follows the temporal dynamics of the cover speech while carrying encrypted deviations.

**Mitigation 3 — Post-processing smoothing**:
Apply a light **temporal smoothing filter** to the encrypted parameters before synthesis. This sacrifices some encryption entropy but makes the signal statistically closer to real speech.

### 13.4 Deniability Levels

| Level | Description | Implementation |
|-------|------------|----------------|
| **Level 1: Casual observation** | "Sounds like a phone call in a foreign language" | Base system achieves this |
| **Level 2: Automated classification** | ML classifier says "speech" not "encrypted data" | Achieved via harmonic synthesis + VAD-compatible parameters |
| **Level 3: Statistical analysis** | No anomalies in spectral/temporal statistics | Requires mitigations from 13.3 |
| **Level 4: Expert phonetic analysis** | "This sounds like a valid but unfamiliar language" | Requires cover speech injection (Mitigation 2) |
| **Level 5: Full forensic analysis** | "We cannot determine if this contains hidden data" | Requires all mitigations + careful parameter tuning + evaluation against state-of-the-art steganalysis |

---

## 14. Data Flow Diagram

### 14.1 Bit Budget Per Frame (20ms)

```
SENDER SIDE:
─────────────────────────────────────────────────────
Speech input (8kHz, 16-bit)    = 2,560 bits / 20ms frame
                                        │
                    [STT → TTS → Parameterize]
                                        │
                                        ▼
Speech parameters:
  Pitch (p):              16 bits
  Energy (ε):             16 bits  
  Spectral (D ∈ S⁸):    128 bits  (8 × 16-bit quantized angles)
  ─────────────────────────────────
  TOTAL:                 160 bits / frame
                                        │
                    [Encrypt: 160 PRNG bits consumed]
                                        │
                                        ▼
Encrypted parameters:
  Pitch (p̃):             16 bits  ← valid pitch value
  Energy (ε̃):            16 bits  ← valid energy value
  Spectral (D̃ ∈ S¹⁵):  128 bits  ← valid spectral envelope
  ─────────────────────────────────
  TOTAL:                 160 bits / frame
                                        │
                    [Pseudo-speech synthesis → 25ms frame]
                                        │
                                        ▼
Pseudo-speech audio (16kHz, 16-bit) = 6,400 bits / 25ms frame
                                        │
                    [Phone codec → compress → carrier encrypt]
                                        │
                                        ▼
Transmitted:  AMR-NB @ 12.2kbps = 244 bits / 20ms
              or Opus @ 48kbps  = 960 bits / 20ms
```

### 14.2 Key Material Budget

```
KEY CONSUMPTION:
  Inner encryption:   160 bits/frame × 50 frames/sec = 8,000 bits/sec
  LP synthesizer:     80 bits / 67.5ms frame = ~1,185 bits/sec
  ──────────────────────────────────────────────────────
  TOTAL CONSUMPTION:  ~9,185 bits/sec

KEY GENERATION (SKD at SNR=39.9dB):
  Average rate:       6,440 bits/sec

  ⚠ DEFICIT at 39.9dB! Solutions:
  a) Increase SNR to 50dB → 9,960 bits/sec (sufficient)
  b) Use Option A weakening (key reuse across N frames)
  c) Reduce scrambling resolution (fewer bits per parameter)
  d) Run SKD on wider bandwidth (16kHz samples instead of 8kHz)
```

---

## 15. Parameter Selection & Tradeoffs

### 15.1 Master Tradeoff Table

| Parameter | Low Setting | High Setting | Effect of Increasing |
|-----------|------------|-------------|---------------------|
| Key size (bits) | 64 | 256 | ↑ Security, ↓ Deniability (more random = less speech-like) |
| Spectral windows | 9 | 18 | ↑ Encoding precision, ↑ Bandwidth requirement |
| Frame duration | 15ms | 25ms | ↑ Robustness, ↑ Latency |
| Guard period | 3ms | 5ms | ↑ Robustness, ↓ Data rate |
| Codebook size (DoV) | 64 | 4096 | ↑ Data rate, ↓ Error robustness |
| LP filter order | 8 | 12 | ↑ Spectral accuracy, ↑ Computation |
| Synthesizer SNR | 30dB | 50dB | ↑ Key generation rate, ↓ Innovation entropy |
| PRNG bits per frame | 80 | 160 | ↑ Scrambling resolution, ↑ Key consumption |
| Temporal smoothing | None | Heavy | ↑ Deniability, ↓ Security (correlates ciphertext) |

### 15.2 Recommended Configuration Profiles

**Profile: STEALTH (Maximum Deniability)**
```
Key size:           64 bits (AES-CTR)
Temporal smoothing: Heavy (correlated encryption offsets)
Cover speech:       Yes (TTS reads generic conversational text)
Spectral windows:   9 (narrowband)
Frame duration:     25ms
Synthesizer SNR:    40dB
Target channel:     VoIP (FaceTime, Signal)
```

**Profile: FORTRESS (Maximum Security)**
```
Key size:           256 bits (full AES-CTR) or OTP (Vernam)
Temporal smoothing: None
Cover speech:       No (pure pseudo-speech)
Spectral windows:   16 (wideband)
Frame duration:     20ms
Synthesizer SNR:    50dB
Target channel:     Dedicated VoIP / direct connection
```

**Profile: BALANCED (Recommended Default)**
```
Key size:           80 bits (AES-CTR)
Temporal smoothing: Light (small correlated perturbations)
Cover speech:       Optional
Spectral windows:   12
Frame duration:     25ms with 5ms guards
Synthesizer SNR:    42dB
Target channel:     3G/VoLTE cellular or VoIP
```

---

## 16. Implementation Roadmap

### Stage 1: Proof of Concept (Offline, Simulated Channel)

**Duration**: 4-6 weeks

- [ ] Implement harmonic speech encoder (20ms frames → (ε, p, D))
- [ ] Implement distortion-tolerant scrambler (modular translations + hypersphere rotations)
- [ ] Implement pseudo-speech synthesizer (harmonic waveform from encrypted params)
- [ ] Implement pseudo-speech analyzer (parameter extraction from received waveform)
- [ ] Implement descrambler (inverse operations)
- [ ] Implement narrowband LPCNet vocoder (parameter → speech reconstruction)
- [ ] Test end-to-end with simulated AWGN channel at SNR = 15, 20, 25 dB
- [ ] Measure: BER, RMSE of parameters, PESQ of reconstructed speech

**Languages/Tools**: Python (numpy, scipy), PyTorch (LPCNet training), MATLAB (signal analysis)

### Stage 2: Codec Integration (Offline, Real Codec)

**Duration**: 3-4 weeks

- [ ] Integrate AMR-NB codec (OpenCORE-AMR or reference implementation)
- [ ] Integrate Opus-Silk codec
- [ ] Test pseudo-speech survival through codec compress/decompress cycle
- [ ] Implement channel estimation (phase shift + distortion variance per harmonic)
- [ ] Implement compensated demodulation (Equation 10 from Paper 2)
- [ ] Implement FBMC/OQAM pulse shaping (optional, for improved performance)
- [ ] Implement amplitude pulsing + silence insertion for VAD evasion
- [ ] Measure: BER after codec, PESQ after full encode-codec-decode chain

### Stage 3: Key Agreement System

**Duration**: 3-4 weeks

- [ ] Implement MELPe vocoder (or integrate open-source implementation)
- [ ] Implement local LP synthesis for common randomness generation
- [ ] Implement SKD protocol:
  - [ ] Advantage Distillation (Bit Parity algorithm, 2 iterations)
  - [ ] Information Reconciliation (8-bit Winnow algorithm)
  - [ ] Huffman encoder
  - [ ] Privacy Amplification (universal hash with Toeplitz matrix)
- [ ] Implement FIFO buffer management
- [ ] Implement LSP-based synthesizer for protected-phase key generation
- [ ] Test key generation rate and leakage rate
- [ ] Implement deliberate weakening options (A, B, C from Section 5.3)

### Stage 4: Real-Time On-Device Prototype

**Duration**: 4-6 weeks

- [ ] Port critical-path code to C/C++ for real-time performance
- [ ] Integrate STT model (Whisper small / Vosk)
- [ ] Integrate TTS model (LPCNet / Piper)
- [ ] Implement audio I/O pipeline (capture → process → playback)
- [ ] Target latency: < 200ms end-to-end
- [ ] Test on Android device (via audio cable to second phone)
- [ ] Test on iOS device (if applicable)

### Stage 5: Real-Channel Testing

**Duration**: 2-4 weeks

- [ ] Test over 3G cellular call (phone-to-phone)
- [ ] Test over VoLTE call
- [ ] Test over VoIP: FaceTime, Signal, WhatsApp, Telegram
- [ ] Measure BER, PESQ, MUSHRA (subjective listening test with ~20 participants)
- [ ] Test plausible deniability: run ML speech classifiers on intercepted audio
- [ ] Stress test: varying network conditions, packet loss, handovers

### Stage 6: Hardening & Deniability Enhancement

**Duration**: 2-4 weeks

- [ ] Implement cover speech injection (TTS-based baseline + encrypted deviations)
- [ ] Implement correlated encryption offsets for temporal naturalness
- [ ] Run steganalysis: train adversarial classifier to distinguish pseudo-speech from real speech
- [ ] Iteratively tune parameters until classifier accuracy drops to ~50% (chance level)
- [ ] Security audit: verify key generation, PRNG usage, no side-channel leaks
- [ ] Documentation and threat model finalization

---

## 17. Open Problems & Future Work

### 17.1 Identified Challenges

| Problem | Severity | Potential Solution |
|---------|----------|-------------------|
| **Temporal correlation mismatch**: Frame-independent encryption destroys pitch/spectral continuity | High (for deniability) | Correlated encryption offsets; cover speech injection |
| **Synchronization sensitivity**: 0.3ms sync error causes significant spectral decoding errors | High (for reliability) | Robust sync preambles; pilot symbols per Paper 2 |
| **Pitch estimation in encrypted signal**: Standard pitch trackers assume temporal continuity | Medium | ML-based pitch estimator trained on pseudo-speech; or frame-independent ML estimator (Paper 1) |
| **LPCNet training for encrypted parameters**: Vocoder must handle imperfect parameter recovery | Medium | Two-stage training: clean parameters → noisy parameters (Paper 1, Section 4.3) |
| **Public channel availability**: SKD requires authenticated side-channel | Medium | Use data channel over same cellular connection; or internet connection; or pre-shared initial key |
| **Codec diversity**: Different phones use different codecs with different distortion profiles | Medium | Channel estimation + adaptive codebook selection (Paper 2); or train system on multiple codecs |
| **Computational budget on mobile**: Real-time LPCNet synthesis ~2 GFLOPS | Medium | Optimized C implementation; GPU/NPU acceleration; lighter vocoder architecture |

### 17.2 Future Research Directions

1. **Adversarial training for deniability**: Train a GAN where the generator produces pseudo-speech and the discriminator tries to distinguish it from real speech. The generator learns to produce maximally speech-like encrypted signals.

2. **Neural codec integration**: Modern neural audio codecs (EnCodec, SoundStream) might provide a more natural encoding medium than hand-crafted harmonic synthesis.

3. **Semantic-level encryption**: Instead of encrypting acoustic parameters, encrypt at the semantic token level (e.g., encrypt the token sequence of a neural speech codec). The decoder naturally produces speech-like audio from any token sequence.

4. **Multi-speaker deniability**: Maintain a library of speaker voice profiles. The cover voice can be dynamically selected to match plausible communication patterns (e.g., if Alice normally calls Bob, use a voice profile that sounds like Alice).

5. **Wideband extension**: Paper 1 suggests extending from 4kHz to 8kHz bandwidth using 18 mel-scaled windows + PCA/autoencoder dimensionality reduction. This would improve both speech quality and encoding capacity.

6. **Quantum-resistant key agreement**: While the current SKD protocol is information-theoretically secure, the authentication of the public channel may use classical PKI. Future work should ensure quantum-resistant authentication.

---

## References

1. Krasnowski, P., Lebrun, J., & Martin, B. (2021). *Introducing an experimental distortion-tolerant speech encryption scheme for secure voice communication*. arXiv:2102.09809v1.

2. Krasnowski, P., Lebrun, J., & Martin, B. (2022). *Introducing a novel Data over Voice technique for secure voice communication*. arXiv:2102.10869v4.

3. Čubrilović, S., Kuzmanović, Z., Punt, M., Vučić, D., & Kovačević, B. (2024). *FBMC/OQAM-Based Secure Voice Communications Over Voice Channels*. IEEE Access, 12, 94452-94460.

4. Pekerti, A. A., Sasongko, A., & Indrayanto, A. (2024). *Secure End-to-End Voice Communication: A Comprehensive Review of Steganography, Modem-Based Cryptography, and Chaotic Cryptography Techniques*. IEEE Access, 12, 75146-75168.

5. Radomirović, J., Milosavljević, M., Čubrilović, S., Kuzmanović, Z., Perić, M., Banjac, Z., & Perić, D. (2025). *A Class of Perfectly Secret Autonomous Low-Bit-Rate Voice Communication Systems*. Symmetry, 17(3), 365.

---

*Document version: 1.0*
*Classification: RESEARCH — NOT FOR OPERATIONAL USE*
*Generated from analysis of 5 research papers, synthesized into unified architecture*
