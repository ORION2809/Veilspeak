# HOW TO ACTUALLY BUILD THIS

## The Brutal Truth First

The plan.md describes a beautiful theoretical architecture. Building it requires solving
these problems **in this exact order**, because each one gates the next:

```
Can we parameterize speech?              ← SOLVED (well-known DSP)
    │
Can we encrypt params and keep them valid? ← SOLVABLE (the math exists in Paper 1)
    │
Can encrypted params survive a codec?     ← THIS IS THE HARD PART
    │
Can we reconstruct speech from noisy params? ← SOLVED (LPCNet exists)
    │
Can we do all this in real-time?          ← ENGINEERING PROBLEM
    │
Can we generate keys without pre-sharing? ← SOLVED (Paper 5 proved it)
```

**The single hardest problem** is #3: making the encrypted pseudo-speech survive
the phone's codec with low enough error that the descrambled parameters are
still usable. Everything else is glue.

---

## Build Order: 6 Milestones

### Milestone 1: "Can I round-trip speech parameters through encryption?"
### Milestone 2: "Can encrypted pseudo-speech survive a codec?"
### Milestone 3: "Can a human understand the reconstructed speech?"
### Milestone 4: "Can I do this in real-time on a phone?"
### Milestone 5: "Can two phones do this over an actual call?"
### Milestone 6: "Can an observer tell this isn't normal speech?"

Each milestone is independently testable and gives you a go/no-go.

---

## MILESTONE 1: Parameter Round-Trip Through Encryption
**Goal**: Prove that encrypt(params) → decrypt(params) ≈ original params
**Timeline**: 1-2 weeks
**No audio involved yet. Pure math.**

### What to build

```
project/
├── core/
│   ├── speech_params.py      # Data structures for (pitch, energy, spectral)
│   ├── scrambler.py          # The encryption (modular arithmetic + hypersphere rotation)
│   ├── descrambler.py        # The decryption (inverse operations)
│   ├── lattice.py            # Gosset lattice Γ₈, closest vector problem
│   ├── sphere.py             # Hypersphere mappings γ_ξ, Φ_ξ, and inverses
│   └── prng.py               # AES-CTR key stream generator
├── tests/
│   ├── test_roundtrip.py     # encrypt → decrypt, measure error
│   ├── test_lattice.py       # CVP correctness
│   └── test_sphere.py        # mapping invertibility
└── requirements.txt
```

### Dependencies

```
numpy           # array math
scipy           # spatial, linalg
pycryptodome    # AES-CTR for PRNG
pytest          # testing
```

### The actual code you need to write

**1. Spectral envelope representation** — `sphere.py`

The spectral envelope `D` is a unit vector on S⁸ (9 components, norm = 1).
You need two mappings:

```python
def gamma(x: np.ndarray, xi: np.ndarray) -> np.ndarray:
    """Map unit vector on S^n to R^n via spherical coordinates, scaled by xi."""
    # x is on S^8 (9 components), output is in R^8
    # Convert to spherical coordinates phi_1..phi_8
    # Multiply each by xi_i
    ...

def phi(u: np.ndarray, xi: np.ndarray) -> np.ndarray:
    """Torus mapping: R^n -> S^{2n-1}."""
    # u is in R^8, output is on S^15 (16 components)
    result = np.zeros(2 * len(u))
    for i in range(len(u)):
        result[2*i]     = xi[i] * np.cos(u[i] / xi[i])
        result[2*i + 1] = xi[i] * np.sin(u[i] / xi[i])
    return result
```

For our case: `xi = (1/sqrt(8)) * np.ones(8)` — equal weights.

The spherical coordinate conversion (Eq. 1 in Paper 1) is standard:
```
x₁ = cos(φ₁)
x₂ = sin(φ₁)cos(φ₂)
...
x₈ = sin(φ₁)...sin(φ₇)cos(φ₈)
x₉ = sin(φ₁)...sin(φ₇)sin(φ₈)
```

You need the **inverse** too (cartesian → spherical). Use `atan2`.

**2. Gosset lattice and closest vector problem** — `lattice.py`

The Gosset lattice Γ₈ (also called E₈) is one of the densest sphere packings in 8D.
Its basis vectors (from Conway & Sloane) and a fast CVP decoder exist.

The key insight: you don't need to implement a general CVP solver.
The quotient group `Λ^(α)/Λ^(β)` has structure `Z_{2^15} ⊕ Z_{2^16}^6 ⊕ Z_{2^17}`.
Encryption is just **adding integer vectors modulo these values**.

```python
# The 8 basis vectors of Λ^(α) (scaled Gosset lattice)
ALPHA_BASIS = np.array([
    [1,  0,  0,  0,  0,  0,  0,  0],  # α₁, range Z_{2^15}
    [-1, 1,  0,  0,  0,  0,  0,  0],  # α₂, range Z_{2^16}
    [0, -1,  1,  0,  0,  0,  0,  0],  # α₃
    [0,  0, -1,  1,  0,  0,  0,  0],  # α₄
    [0,  0,  0, -1,  1,  0,  0,  0],  # α₅
    [0,  0,  0,  0, -1,  1,  0,  0],  # α₆
    [0,  0,  0,  0,  0, -1,  1,  0],  # α₇
    [1,  1,  1,  1,  1,  1,  1,  1],  # α₈, range Z_{2^17}
]) * (2 * np.pi / (2**16 * np.sqrt(8)))

MODULI = [2**15, 2**16, 2**16, 2**16, 2**16, 2**16, 2**16, 2**17]
```

The "closest vector" step is quantization — snap the input to the nearest
lattice point. For the Gosset lattice, Conway & Sloane give an O(n log n) decoder.
But for a first implementation, you can use a simpler approach:

```python
def quantize_to_lattice(point, lattice_basis):
    """Find closest lattice point by rounding coordinates in the lattice basis."""
    coords = np.linalg.solve(lattice_basis.T @ lattice_basis,
                              lattice_basis.T @ point)
    rounded = np.round(coords).astype(int)
    # Reduce modulo quotient group
    for i, m in enumerate(MODULI):
        rounded[i] = rounded[i] % m
    return rounded
```

This isn't optimal but it works for prototyping. Replace with the real E₈
decoder later.

**3. The actual scrambler** — `scrambler.py`

```python
def scramble_frame(pitch, energy, spectral_env, key_stream_160bits):
    """
    Encrypt one frame's speech parameters.
    
    pitch:         float, samples per cycle [16, 128]
    energy:        float, frame energy [10, 1e8]
    spectral_env:  np.array shape (9,), unit vector on S^8
    key_stream:    160 bits from AES-CTR PRNG
    
    Returns: (pitch_enc, energy_enc, spectral_enc)
             all valid speech parameter values
    """
    # Parse key stream into 10 integers
    nu = parse_key_stream(key_stream_160bits)
    # nu[0] ∈ Z_{2^16}, nu[1] ∈ Z_{2^16}
    # nu[2] ∈ Z_{2^15}, nu[3..8] ∈ Z_{2^16}, nu[9] ∈ Z_{2^17}
    
    # --- Pitch encryption ---
    kappa = linear_scale(pitch, P_MIN, P_MAX, KAPPA_LOW, KAPPA_HIGH)
    kappa_enc = (kappa + nu[0]) % (2**16)
    pitch_enc = linear_scale(kappa_enc, 0, 2**16-1, P_TILDE_MIN, P_TILDE_MAX)
    
    # --- Energy encryption ---
    rho = linear_scale(np.log10(energy), LOG_E_MIN, LOG_E_MAX, RHO_LOW, RHO_HIGH)
    rho_enc = (rho + nu[1]) % (2**16)
    energy_enc = 10 ** linear_scale(rho_enc, 0, 2**16-1,
                                     LOG_E_TILDE_MIN, LOG_E_TILDE_MAX)
    
    # --- Spectral envelope encryption ---
    xi = np.ones(8) / np.sqrt(8)
    angles = cartesian_to_spherical(spectral_env)  # 8 angles from S^8
    scaled = 2 * xi * angles                        # into P_xi
    chi_init = quantize_to_lattice(scaled)           # nearest lattice point
    
    # Random translation on the lattice quotient group
    translation = sum(nu[2+i] * ALPHA_BASIS[i] for i in range(8))
    chi_enc = (chi_init + translation)  # mod Λ^(α)/Λ^(β)
    # (modular reduction happens in the integer coordinate space)
    
    spectral_enc = torus_map(chi_enc, xi)  # Φ_ξ → point on S^15
    
    return pitch_enc, energy_enc, spectral_enc
```

**4. Test it**

```python
def test_perfect_roundtrip():
    """With zero noise, decrypt(encrypt(x)) should exactly recover x."""
    key = os.urandom(32)  # 256-bit AES key
    prng = AES_CTR_PRNG(key)
    
    for _ in range(10000):
        pitch = np.random.uniform(16, 128)
        energy = 10 ** np.random.uniform(1, 8)
        spectral = random_unit_vector(9)
        
        key_bits = prng.next(160)
        p_enc, e_enc, s_enc = scramble_frame(pitch, energy, spectral, key_bits)
        
        # Verify encrypted values are in valid ranges
        assert P_TILDE_MIN <= p_enc <= P_TILDE_MAX
        assert E_TILDE_MIN <= e_enc <= E_TILDE_MAX
        assert abs(np.linalg.norm(s_enc) - 1.0) < 1e-10  # on unit sphere
        
        # Decrypt
        prng_rx = AES_CTR_PRNG(key)  # same key, same state
        key_bits_rx = prng_rx.next(160)
        p_dec, e_dec, s_dec = descramble_frame(p_enc, e_enc, s_enc, key_bits_rx)
        
        # Should be exact (no channel noise)
        assert abs(p_dec - pitch) < 0.01
        assert abs(np.log10(e_dec) - np.log10(energy)) < 0.01
        assert np.linalg.norm(s_dec - spectral) < 0.01
```

### Milestone 1 Success Criteria
- [ ] 10,000 random parameter sets encrypt and decrypt with < 0.01 error
- [ ] All encrypted values fall within valid speech parameter ranges
- [ ] Encrypted spectral envelopes are valid unit vectors on S^15

---

## MILESTONE 2: Codec Survival
**Goal**: Prove that pseudo-speech audio survives AMR/Opus compress+decompress
**Timeline**: 2-3 weeks
**This is where most projects die. Take it seriously.**

### What to build

```
project/
├── core/
│   ├── ... (milestone 1 code)
│   ├── synthesizer.py        # Pseudo-speech waveform generation
│   ├── analyzer.py           # Parameter extraction from received audio
│   └── channel.py            # Codec simulation (AMR, Opus)
├── codecs/
│   ├── amr_nb/               # OpenCORE-AMR wrapper
│   └── opus/                 # Opus codec wrapper
├── tests/
│   ├── test_codec_survival.py
│   └── test_channel_estimation.py
└── evaluate/
    ├── measure_ber.py
    └── measure_param_error.py
```

### Dependencies (add to milestone 1)

```
soundfile       # audio I/O
librosa         # pitch estimation, spectral analysis
pyogg           # Opus codec bindings (or build from source)
```

For AMR-NB, you'll need to compile OpenCORE-AMR:
```bash
git clone https://github.com/nicfit/opencore-amr.git
cd opencore-amr && ./configure && make && make install
```

Or use FFmpeg as a codec wrapper (simpler):
```bash
# Encode to AMR
ffmpeg -i input.wav -ar 8000 -ac 1 -ab 12200 output.amr
# Decode back
ffmpeg -i output.amr output_decoded.wav
```

### The synthesizer — `synthesizer.py`

This is the component that turns encrypted parameters into audio.

```python
def synthesize_frame(pitch_enc, energy_enc, spectral_enc,
                     fs=16000, frame_len=400):
    """
    Generate 25ms pseudo-speech frame from encrypted parameters.
    
    pitch_enc:    float, encrypted pitch period in samples (at 16kHz)
    energy_enc:   float, encrypted frame energy
    spectral_enc: np.array shape (16,), point on S^15 (encrypted spectral envelope)
    
    Returns: np.array shape (400,), audio samples
    """
    omega0 = 2 * np.pi * fs / pitch_enc  # fundamental angular frequency
    K = int(6700 / (omega0 / (2*np.pi)))  # number of harmonics up to 6700 Hz
    
    # 16 square spectral windows, linearly spaced 300-6700 Hz
    H = build_spectral_windows(16, 300, 6700, fs, frame_len=240)
    
    # Build B matrix (DFT of harmonic sinusoids)
    B = build_harmonic_matrix(omega0, K, fs, frame_len=240)
    
    # W16 = roots of unity phase factors
    W16 = np.exp(1j * 2 * np.pi * np.arange(1, 17) / 16)
    
    # Solve for complex harmonic amplitudes via pseudo-inverse
    target = spectral_enc * W16  # element-wise
    HB = H @ B
    A_complex = np.linalg.pinv(HB) @ target
    
    # Extract amplitudes and phases
    amplitudes = np.abs(A_complex)
    phases = np.angle(A_complex)
    
    # Synthesize 240 "data" samples (middle of frame)
    n = np.arange(240)
    signal_middle = np.zeros(240)
    for k in range(K):
        signal_middle += amplitudes[k] * np.cos(
            (k+1) * omega0 / fs * n + phases[k] - (k+1) * omega0 / fs * 80
        )
    
    # Scale to match target energy
    current_energy = np.sum(signal_middle**2)
    if current_energy > 0:
        eta = np.sqrt(energy_enc / current_energy)
        signal_middle *= eta
    
    # Build full 400-sample frame with guard periods
    frame = np.zeros(frame_len)
    frame[80:320] = signal_middle
    
    # Extend guard periods by repeating waveform
    # (maintain phase continuity)
    extend_guard_periods(frame, omega0, amplitudes, phases, fs)
    
    # Apply trapezoidal window
    window = trapezoidal_window(frame_len, guard=80)
    frame *= window
    
    return frame
```

### The analyzer — `analyzer.py`

Extracts parameters from the received (codec-distorted) pseudo-speech.

```python
def analyze_frame(received_frame, fs=16000):
    """
    Extract encrypted parameters from received pseudo-speech frame.
    
    received_frame: np.array shape (400,), received audio samples
    
    Returns: (pitch_rec, energy_rec, spectral_rec)
    """
    # Extract middle 240 samples
    y_middle = received_frame[80:320]
    
    # --- Pitch estimation ---
    # Use ML pitch estimator (no tracking - pitch is random per frame)
    pitch_rec = estimate_pitch_ml(y_middle, fs,
                                   pitch_min=P_TILDE_MIN, pitch_max=P_TILDE_MAX)
    
    # --- Energy ---
    energy_rec = np.sum(y_middle**2)
    energy_rec = np.clip(energy_rec, E_TILDE_MIN, E_TILDE_MAX)
    
    # --- Spectral envelope ---
    Y = np.fft.fft(y_middle, n=240)
    H = build_spectral_windows(16, 300, 6700, fs, frame_len=240)
    W16_conj = np.exp(-1j * 2 * np.pi * np.arange(1, 17) / 16)
    
    raw = np.real(2 * H @ Y[:240] * W16_conj)
    D_rec_normalized = raw / np.linalg.norm(raw)
    
    # Project onto flat torus
    xi = np.ones(8) / np.sqrt(8)
    spectral_rec = project_to_torus(D_rec_normalized, xi)
    
    return pitch_rec, energy_rec, spectral_rec
```

### The codec simulation — `channel.py`

```python
import subprocess
import tempfile
import soundfile as sf

def pass_through_codec(audio, fs, codec='amr-nb', bitrate=12200):
    """
    Simulate voice channel by encoding+decoding through a real codec.
    Uses FFmpeg as the codec engine.
    """
    with tempfile.NamedTemporaryFile(suffix='.wav') as f_in, \
         tempfile.NamedTemporaryFile(suffix='.wav') as f_out:
        
        # Write input
        sf.write(f_in.name, audio, fs, subtype='PCM_16')
        
        if codec == 'amr-nb':
            amr_tmp = tempfile.mktemp(suffix='.amr')
            subprocess.run([
                'ffmpeg', '-y', '-i', f_in.name,
                '-ar', '8000', '-ac', '1', '-ab', str(bitrate),
                amr_tmp
            ], capture_output=True)
            subprocess.run([
                'ffmpeg', '-y', '-i', amr_tmp,
                '-ar', str(fs), '-ac', '1',
                f_out.name
            ], capture_output=True)
            os.unlink(amr_tmp)
            
        elif codec == 'opus':
            opus_tmp = tempfile.mktemp(suffix='.opus')
            subprocess.run([
                'ffmpeg', '-y', '-i', f_in.name,
                '-c:a', 'libopus', '-b:a', str(bitrate),
                '-application', 'voip',
                opus_tmp
            ], capture_output=True)
            subprocess.run([
                'ffmpeg', '-y', '-i', opus_tmp,
                '-ar', str(fs), '-ac', '1',
                f_out.name
            ], capture_output=True)
            os.unlink(opus_tmp)
        
        received, _ = sf.read(f_out.name)
        return received
```

### The critical test

```python
def test_codec_survival():
    """THE make-or-break test. If this fails, the whole system is dead."""
    
    errors_pitch = []
    errors_energy = []
    errors_spectral = []
    
    for trial in range(1000):
        # Generate random encrypted parameters (valid ranges)
        p_enc = np.random.uniform(P_TILDE_MIN, P_TILDE_MAX)
        e_enc = 10 ** np.random.uniform(LOG_E_TILDE_MIN, LOG_E_TILDE_MAX)
        s_enc = random_unit_vector(16)  # random point on S^15
        
        # Synthesize pseudo-speech
        frame = synthesize_frame(p_enc, e_enc, s_enc)
        
        # Pass through codec
        # Build a longer signal (codecs need context)
        signal = np.tile(frame, 20)  # repeat for codec warmup
        received = pass_through_codec(signal, fs=16000, codec='amr-nb')
        
        # Extract parameters from a middle frame (avoid edge effects)
        mid_start = 10 * len(frame)
        received_frame = received[mid_start:mid_start + len(frame)]
        p_rec, e_rec, s_rec = analyze_frame(received_frame)
        
        # Measure errors
        errors_pitch.append(abs(p_enc - p_rec))
        errors_energy.append(abs(np.log10(e_enc) - np.log10(e_rec)))
        errors_spectral.append(np.linalg.norm(s_enc - s_rec))
    
    print(f"Pitch RMSE:    {np.sqrt(np.mean(np.array(errors_pitch)**2)):.2f}")
    print(f"Energy RMSE:   {np.sqrt(np.mean(np.array(errors_energy)**2)):.4f}")
    print(f"Spectral RMSE: {np.sqrt(np.mean(np.array(errors_spectral)**2)):.4f}")
    
    # Paper 1 achieved these over FaceTime:
    # Pitch RMSE:    ~867 (in quantized domain, not raw)
    # Energy RMSE:   ~1644 (in quantized domain)
    # Spectral RMSE: ~0.16
    # Our spectral RMSE must be < 0.3 for intelligible output
```

### Channel estimation (from Paper 2)

The codec introduces a **constant phase shift per harmonic** plus Gaussian noise.
You need to estimate both before real communication starts.

```python
def estimate_channel(codec='amr-nb', bitrate=12200, n_training=400):
    """
    Send known symbols through codec, measure phase shift and noise variance
    per harmonic frequency.
    
    Returns: phase_shifts (K,), noise_variances (K,)
    """
    # Generate training sequence with known phases
    training_phases = np.random.randint(0, 4, size=(n_training, K)) * (np.pi/2)
    
    phase_shifts = np.zeros(K)
    noise_vars = np.zeros(K)
    
    for k in range(K):
        received_phases = []
        for t in range(n_training):
            # ... synthesize, send through codec, analyze ...
            received_phase = extract_harmonic_phase(received_frame, k)
            received_phases.append(received_phase - training_phases[t, k])
        
        # Phase shift = circular mean of differences
        phase_shifts[k] = circular_mean(received_phases)
        # Noise variance = circular variance
        noise_vars[k] = circular_variance(received_phases)
    
    return phase_shifts, noise_vars
```

### Milestone 2 Success Criteria
- [ ] Pseudo-speech survives AMR-NB at 12.2 kbps with spectral RMSE < 0.20
- [ ] Pseudo-speech survives Opus-Silk at 48 kbps with spectral RMSE < 0.10
- [ ] Pitch estimation error < 5% for 95% of frames
- [ ] Full encrypt → synthesize → codec → analyze → decrypt round-trip produces parameter errors within Paper 1's bounds

### What to do if Milestone 2 fails

This is the most likely failure point. If codec distortion is too high:

1. **Increase pseudo-speech frame energy** (`ε̃_min`). Higher energy means the signal
   dominates codec quantization noise. Paper 1 uses `ε̃_min = 10^9`.

2. **Reduce the number of spectral components**. Instead of 16 windows encoding
   S^15, use 8 windows encoding S^7. Halves the data per frame but doubles robustness.

3. **Use wideband codec** (AMR-WB / Opus at 16kHz). More bandwidth = more room
   for encoding = less distortion per component.

4. **Add Reed-Solomon error correction** (from Paper 2). Trade bitrate for reliability.
   With RS(28,20) over GF(2^6), you can correct 4 symbol errors per frame.

5. **Use the DoV approach from Paper 2 entirely** — abandon parameter-domain
   encryption and instead: compress speech with Codec2 → AES encrypt → RS protect →
   modulate into OFDM harmonic symbols. Less elegant but proven to work at 2.4 kbps
   over 3G.

---

## MILESTONE 3: Intelligible Reconstructed Speech
**Goal**: A person can understand what was said after full encrypt-transmit-decrypt
**Timeline**: 2-3 weeks

### What to build

```
project/
├── core/
│   ├── ... (milestones 1-2)
│   ├── speech_encoder.py     # Mic input → speech parameters
│   ├── speech_decoder.py     # Parameters → audible speech (LPCNet)
│   └── stt_tts.py            # Speech-to-text, text-to-speech bridge
├── models/
│   ├── lpcnet/               # Pre-trained narrowband LPCNet
│   └── whisper/              # Whisper small STT model
└── evaluate/
    ├── pesq_test.py          # PESQ objective quality measurement
    └── listening_test.py     # Instructions for subjective testing
```

### Dependencies (add)

```
torch           # for LPCNet / Whisper
whisper         # OpenAI Whisper (or faster-whisper)
pesq            # pip install pesq - ITU P.862 implementation
```

### Option A: Direct parameter vocoding (Paper 1 approach)

Use the **narrowband LPCNet** from Paper 1. This is a neural vocoder that takes
(MFCCs + pitch) and generates speech waveform sample-by-sample.

```python
# The feature vector per frame
def params_to_features(pitch_dec, energy_dec, spectral_dec):
    """Convert decrypted speech parameters to LPCNet feature vector."""
    # Reconstruct band energies
    E = energy_dec * spectral_dec**2  # shape (9,)
    
    # DCT-II of log energies → 9-band MFCCs
    C = scipy.fft.dct(np.log10(E + 1e-10), type=2, n=9)
    
    # Scaled pitch
    rho = (pitch_dec - 100) / 50
    
    return np.concatenate([C, [rho]])  # shape (10,)
```

**Getting LPCNet**:
```bash
git clone https://github.com/mozilla/LPCNet.git
cd LPCNet
# Follow build instructions - needs a trained model
# The pre-trained model works for English
```

Paper 1 modified LPCNet for narrowband (8kHz) and retrained it with noise injection
to handle imperfect parameters. You'll need to do the same retraining:

**Training Stage 1**: Train on clean speech parameters (learn normal speech synthesis)
**Training Stage 2**: Train on speech parameters + simulated channel noise (learn to
handle decryption errors). Inject noise matching your measured codec distortion profile.

### Option B: STT → TTS bridge (simpler, your preferred approach)

Since you said "the voice itself need not be mine", this is actually simpler:

```python
# SENDER SIDE
def encode_speech(audio_input):
    """Convert speaker's voice to transmittable semantic tokens."""
    # 1. Speech to text
    text = whisper_model.transcribe(audio_input)
    
    # 2. Text to compact binary
    encoded = text.encode('utf-8')  # or use a tighter encoding
    
    # 3. Encrypt
    encrypted = aes_ctr_encrypt(encoded, session_key)
    
    # 4. Modulate into pseudo-speech using DoV (Paper 2)
    audio_out = dov_modulate(encrypted)
    
    return audio_out

# RECEIVER SIDE  
def decode_speech(audio_received):
    """Recover semantic content and speak it."""
    # 1. Demodulate DoV signal
    encrypted = dov_demodulate(audio_received)
    
    # 2. Decrypt
    text_bytes = aes_ctr_decrypt(encrypted, session_key)
    text = text_bytes.decode('utf-8')
    
    # 3. Text to speech (mechanical voice, same model both sides)
    audio_out = tts_model.synthesize(text)
    
    return audio_out
```

**This is dramatically simpler** but has tradeoffs:

| | Parameter vocoding (Option A) | STT→TTS bridge (Option B) |
|---|---|---|
| Latency | ~90ms (frame-based) | ~500ms-2s (need full utterance for STT) |
| Voice quality | Degraded but continuous | Clean mechanical voice, but choppy |
| Bandwidth need | 1.2-2.4 kbps of parameters | ~100-500 bps of text |
| STT errors | N/A | Misrecognized words = wrong message |
| Steganography | Encrypted params ARE the speech signal | Encrypted text modulated AS a speech signal |
| Complexity | High (neural vocoder, hypersphere math) | Medium (off-the-shelf STT + TTS + DoV) |

**My recommendation**: Start with **Option B** as your Milestone 3 proof-of-concept.
If latency is unacceptable, switch to Option A later.

### Hybrid approach (best of both)

Use **neural speech tokens** (like EnCodec / SoundStream) instead of text:

```
Speaker's voice → Neural codec encoder → discrete tokens (1.5-6 kbps)
                                              │
                                    [Encrypt tokens]
                                              │
                               [Modulate as pseudo-speech]
                                              │
                                         ══ CHANNEL ══
                                              │
                               [Demodulate pseudo-speech]
                                              │
                                    [Decrypt tokens]
                                              │
                        Neural codec decoder → Reconstructed speech
```

This preserves prosody/emotion (unlike pure text) while being much lower latency
than STT+TTS. The tokens are discrete integers — perfect for encryption.

```bash
pip install encodec  # Meta's neural audio codec
```

```python
from encodec import EncodecModel
from encodec.utils import convert_audio

model = EncodecModel.encodec_model_24khz()
model.set_target_bandwidth(1.5)  # 1.5 kbps

# Encode
codes = model.encode(audio_tensor)  # discrete token indices

# These tokens can be encrypted, modulated, transmitted, demodulated, decrypted

# Decode
reconstructed = model.decode(codes)
```

### Milestone 3 Success Criteria
- [ ] A listener can understand > 80% of words in decoded speech
- [ ] PESQ score > 2.0 (after full encrypt → channel → decrypt → synthesize)
- [ ] End-to-end latency measured and documented
- [ ] At least 2 different codec channels tested (AMR-NB + Opus)

---

## MILESTONE 4: Real-Time On-Device
**Goal**: Process speech in real-time on a smartphone/laptop
**Timeline**: 3-4 weeks

### Performance budgets

Total one-way latency budget: **200ms**

| Component | Budget | Notes |
|-----------|--------|-------|
| Audio capture | 20ms | One frame buffer |
| STT / Parameterization | 30ms | Must be streaming, not batch |
| Encryption | <1ms | AES is fast |
| Pseudo-speech synthesis | 10ms | Pre-computed lookup tables help |
| Audio playback buffer | 20ms | Output buffering |
| **Sender total** | **~80ms** | |
| Audio capture (rx) | 20ms | |
| Analysis / Demodulation | 20ms | FFT + pitch estimation |
| Decryption | <1ms | |
| Speech synthesis (LPCNet) | 50ms | This is the bottleneck |
| Audio playback | 20ms | |
| **Receiver total** | **~110ms** | |
| **TOTAL** | **~190ms** | Within budget |

### Critical path optimization

**LPCNet** at 8kHz requires ~2 GFLOPS. On a modern phone:
- iPhone 15 Neural Engine: ~17 TOPS → trivial
- Snapdragon 8 Gen 3: ~73 TOPS → trivial
- Snapdragon 665 (mid-range): ~5 TOPS → still fine

The real bottleneck is **audio I/O latency**, not compute.

### Platform choices

**For prototyping**: Python on laptop with PyAudio
```bash
pip install pyaudio
```

```python
import pyaudio
import threading
import queue

class RealTimeProcessor:
    def __init__(self, key):
        self.pa = pyaudio.PyAudio()
        self.frame_queue = queue.Queue()
        self.scrambler = Scrambler(key)
        
    def audio_callback(self, in_data, frame_count, time_info, status):
        """Called by PyAudio for each audio frame."""
        audio = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768
        self.frame_queue.put(audio)
        # Return processed output for playback
        if not self.output_queue.empty():
            out = self.output_queue.get()
            return (out.tobytes(), pyaudio.paContinue)
        return (np.zeros(frame_count, dtype=np.int16).tobytes(), pyaudio.paContinue)
    
    def processing_thread(self):
        """Runs in separate thread: parameterize → encrypt → synthesize."""
        while self.running:
            frame = self.frame_queue.get()
            params = self.encoder.encode(frame)
            enc_params = self.scrambler.scramble(*params)
            pseudo_speech = self.synthesizer.synthesize(*enc_params)
            self.output_queue.put(pseudo_speech)
```

**For mobile deployment**: Port critical path to C/C++, call from Android (JNI) or iOS (Swift/ObjC bridge)

Key libraries in C:
- LPCNet: already has C implementation (Mozilla)
- AES: OpenSSL or mbedTLS
- FFT: FFTW or KissFFT
- Audio I/O: AAudio (Android) / AVAudioEngine (iOS)

### Milestone 4 Success Criteria
- [ ] Continuous speech processing at < 200ms one-way latency
- [ ] No audio dropouts during 60-second continuous operation
- [ ] CPU usage < 30% on target device
- [ ] Memory usage < 100MB

---

## MILESTONE 5: Two-Phone Live Call
**Goal**: Alice and Bob have an encrypted conversation over a real phone call
**Timeline**: 2-3 weeks

### Setup

```
┌──────────────┐     Audio cable      ┌──────────────┐
│  Processing  │◄───or Bluetooth────►│   Phone A    │
│  Device A    │                      │  (makes call)│
│  (laptop/    │                      └──────┬───────┘
│   Raspberry  │                             │
│   Pi)        │                        GSM/VoIP
└──────────────┘                             │
                                      ┌──────┴───────┐
┌──────────────┐     Audio cable      │   Phone B    │
│  Processing  │◄───or Bluetooth────►│  (receives   │
│  Device B    │                      │   call)      │
│  (laptop/    │                      └──────────────┘
│   Raspberry  │
│   Pi)        │
└──────────────┘
```

### Synchronization — The Unsolved Hard Problem

This is what Paper 1 calls "the Achilles heel." The receiver must know
EXACTLY where each 25ms frame starts in the received audio stream.
A 0.3ms error causes large spectral errors.

**Solutions from the papers**:

1. **Constant header/preamble** (Paper 2): 10ms known waveform at the start
   of each DoV frame. Receiver correlates against known pattern to find frame boundaries.

2. **Frame counter** (Paper 2): 16-bit counter in each frame allows re-sync
   after signal dropouts. Permits independent frame decoding.

3. **Chirp synchronization**: Send a known linear frequency sweep before
   encrypted speech begins. Receiver detects the chirp and aligns.

4. **Pilot symbols** (Paper 3): Known FBMC/OQAM symbols inserted at
   predefined positions for time and phase synchronization.

```python
def build_sync_preamble(fs=16000, duration_ms=10):
    """Generate a synchronization preamble that's robust to codec distortion."""
    n_samples = int(fs * duration_ms / 1000)
    t = np.arange(n_samples) / fs
    
    # Linear chirp from 500 Hz to 2000 Hz
    chirp = scipy.signal.chirp(t, f0=500, t1=duration_ms/1000, f1=2000)
    
    # Apply raised-cosine window
    window = np.hanning(n_samples)
    return chirp * window

def find_frame_start(received_audio, preamble, fs=16000):
    """Find the start of the next frame by correlating with known preamble."""
    correlation = scipy.signal.correlate(received_audio, preamble, mode='valid')
    peak = np.argmax(np.abs(correlation))
    confidence = np.abs(correlation[peak]) / np.sqrt(
        np.sum(received_audio**2) * np.sum(preamble**2)
    )
    return peak, confidence
```

### Public channel for SKD

The SKD key agreement protocol needs a second communication channel.
Options:

| Channel | Pros | Cons |
|---------|------|------|
| Mobile data (TCP/IP) | Always available on smartphone | Needs internet |
| SMS | Works without internet | Slow, expensive per message |
| Bluetooth (between devices) | No network needed | Short range only |
| Second phone call | Universally available | Doubles resource usage |
| Pre-shared initial key + DH | No second channel needed | Not autonomous per Paper 5 |

**Pragmatic recommendation**: Use mobile data (a simple TCP socket or HTTPS POST)
as the public channel. Both phones have internet. The SKD protocol exchanges
~6000 bits per 8640-bit block — a few KB per second. Trivial bandwidth.

### Milestone 5 Success Criteria
- [ ] Two people can converse and understand each other over GSM 3G call
- [ ] Two people can converse over VoIP (Signal/WhatsApp/FaceTime)
- [ ] Frame synchronization maintains lock for > 60 seconds continuously
- [ ] Key agreement completes within 5 seconds of call start
- [ ] BER < 1% after error correction
- [ ] An observer playing back the intercepted (carrier-decrypted) audio hears plausible speech-like sound

---

## MILESTONE 6: Deniability Testing
**Goal**: Verify that an informed adversary cannot reliably detect the inner encryption
**Timeline**: 2-4 weeks

### Build the adversary

Train an ML classifier to distinguish your pseudo-speech from real speech:

```python
import torch
import torch.nn as nn
import torchaudio

class SpeechVsPseudoSpeechClassifier(nn.Module):
    """Binary classifier: real speech vs. encrypted pseudo-speech."""
    def __init__(self):
        super().__init__()
        # Use mel-spectrogram features
        self.mel = torchaudio.transforms.MelSpectrogram(
            sample_rate=8000, n_mels=40, n_fft=512, hop_length=160
        )
        self.conv = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.ReLU(), nn.AdaptiveAvgPool2d(1),
        )
        self.fc = nn.Sequential(
            nn.Linear(128, 64), nn.ReLU(), nn.Dropout(0.5),
            nn.Linear(64, 1), nn.Sigmoid()
        )
    
    def forward(self, x):
        x = self.mel(x).unsqueeze(1)  # add channel dim
        x = self.conv(x).squeeze(-1).squeeze(-1)
        return self.fc(x)

# Train on:
# - Positive: real speech from LibriSpeech / CommonVoice
# - Negative: your pseudo-speech output
# 
# If classifier accuracy > 60%, your pseudo-speech is distinguishable.
# Iterate on synthesis parameters until accuracy ≈ 50% (chance level).
```

### Statistical tests to run

```python
def deniability_test_suite(real_speech_files, pseudo_speech_files):
    """Comprehensive statistical comparison."""
    results = {}
    
    for name, test_fn in [
        ("pitch_distribution", compare_pitch_distributions),
        ("energy_contour_autocorrelation", compare_energy_autocorr),
        ("spectral_flux", compare_spectral_flux),
        ("zero_crossing_rate", compare_zcr),
        ("formant_transition_patterns", compare_formant_transitions),
        ("silence_pattern", compare_vad_patterns),
        ("long_term_spectral_statistics", compare_ltss),
    ]:
        ks_stat, p_value = test_fn(real_speech_files, pseudo_speech_files)
        results[name] = {"ks_statistic": ks_stat, "p_value": p_value}
        
        if p_value < 0.05:
            print(f"⚠ DISTINGUISHABLE on {name}: p={p_value:.4f}")
        else:
            print(f"✓ Indistinguishable on {name}: p={p_value:.4f}")
    
    return results
```

### Milestone 6 Success Criteria
- [ ] ML classifier accuracy < 55% (near chance) on 5-second audio clips
- [ ] No statistical test distinguishes pseudo-speech from real speech at p<0.05
- [ ] Human listeners (n=10) cannot reliably identify which calls are "normal"
   when given both encrypted and real call recordings

---

## FALLBACK: The Simple Version That Definitely Works

If the parameter-domain steganographic encryption proves too fragile
(Milestone 2 fails repeatedly), here's the **proven fallback** from Papers 2 and 5:

```
Voice → Codec2 (1.2kbps) → AES-256-CTR → Reed-Solomon → DoV OFDM modulation
                                                              │
                                                    pseudo-speech audio
                                                    (harmonic symbols)
                                                              │
                                                         phone codec
                                                              │
                                                      ═══ CHANNEL ═══
                                                              │
                                                    DoV demodulation
                                                              │
                                              RS decode → AES decrypt → Codec2 decode
                                                              │
                                                       reconstructed speech
```

This is **NOT steganographic** — the pseudo-speech doesn't sound like
natural speech, it sounds like modem tones. But it:
- Works over real 3G calls (proven, BER < 0.4%)
- Works over VoIP (proven, BER < 0.01%)
- Uses standard AES-256 encryption
- Has RS error correction for reliability
- Is fully implemented in Paper 2 (code available on GitHub)

You lose plausible deniability but gain a working encrypted voice system.

**Paper 2 GitHub**: https://github.com/PiotrKrasnowski/Data_over_Voice
**Paper 1 GitHub**: https://github.com/PiotrKrasnowski/Speech_Encryption

Start from their code. Don't reinvent what's already proven.

---

## Summary: What To Do Monday Morning

1. `git clone https://github.com/PiotrKrasnowski/Speech_Encryption`
2. `git clone https://github.com/PiotrKrasnowski/Data_over_Voice`
3. Run their examples. Listen to the encrypted and decrypted audio samples.
4. Install FFmpeg + OpenCORE-AMR. Run pseudo-speech through AMR codec. Measure error.
5. Start building Milestone 1: the scrambler math in pure Python+NumPy.
6. By end of week 1, you should have `test_perfect_roundtrip` passing.
7. By end of week 3, you should know if codec survival (Milestone 2) is viable.
8. That result determines everything else.
