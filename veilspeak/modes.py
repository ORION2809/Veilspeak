"""Signal structure and transmission modes.

Every mode uses one tone grid: 23 harmonics, 100 Hz apart, 400-2600 Hz, in 10 ms symbols.
The signal is sent in superframes of 480 ms:

    80 ms pause | 2 header symbols | 38 data symbols

The pause is what keeps a phone codec's silence detector treating the signal as speech
(measured: an 80 ms pause after at most 640 ms of signal kept every AMR, AMR-WB and EVS
detector active; with no pauses AMR and AMR-WB mute the signal within seconds).

The first header symbol is a fixed pilot. The second is the pilot times a type pattern, so
the frame type can be read from the phase difference between the two without knowing the
channel. The modes differ only in how many bits each data symbol carries:

    FAST    2 x 12 bits per symbol  2.4 kbit/s   Codec2 1200   two FEC frames per superframe
    ROBUST  12 bits per symbol      1.2 kbit/s   Codec2 700C   one FEC frame per superframe
    IDLE    6 bits per symbol       0.6 kbit/s   keep-alive, no speech
    CONTROL coded like ROBUST; carries the key-agreement messages
    TRAIN   known symbols, for timing and channel estimation
"""
import os
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from . import codebook
from .waveform import Waveform

GRID = Waveform(80, 80, tuple(range(4, 27)))
K = GRID.k
SYM = GRID.n
PAUSE_SYMS, HEADER_SYMS, DATA_SYMS = 8, 2, 38
BURST_SYMS = HEADER_SYMS + DATA_SYMS
SF_SYMS = PAUSE_SYMS + BURST_SYMS
SF_SAMPLES = SF_SYMS * SYM                     # 3840 samples = 480 ms
PAUSE_SAMPLES = PAUSE_SYMS * SYM
BURST_SAMPLES = BURST_SYMS * SYM
TICK = SF_SAMPLES // 2                         # one frame-counter step: 240 ms

ALL = tuple(range(K))
EVEN = tuple(range(0, K, 2))
ODD = tuple(range(1, K, 2))
TRAIN, FAST, ROBUST, IDLE, CONTROL = range(5)
_DATA = os.path.join(os.path.dirname(__file__), "data")


@dataclass(frozen=True)
class Mode:
    name: str
    kind: int               # header type
    parts: tuple            # ((harmonic positions, bits), ...): sub-symbols in each 10 ms symbol
    frames: int             # FEC frames per superframe
    rs_k: int               # message symbols per FEC frame
    vocoder: int = 0        # Codec2 bit rate; 0 = no speech
    speech_frames: int = 0  # 40 ms vocoder frames per FEC frame

    @property
    def sym_bits(self):
        return self.parts[0][1]

    @property
    def rs_n(self):
        return DATA_SYMS * len(self.parts) // self.frames

    @property
    def msg_bytes(self):
        return self.rs_k * self.sym_bits // 8

    @property
    def gross_bps(self):
        return sum(bits for _, bits in self.parts) * 100

    def books(self):
        return [codebook.base_code(bits, len(pos)) for pos, bits in self.parts]


MODES = {
    FAST: Mode("fast", FAST, ((EVEN, 12), (ODD, 12)), 2, 26, 1200, 6),
    ROBUST: Mode("robust", ROBUST, ((ALL, 12),), 1, 30, 700, 12),
    IDLE: Mode("idle", IDLE, ((ALL, 6),), 1, 20),
    CONTROL: Mode("control", CONTROL, ((ALL, 12),), 1, 30),
}


def _cached(name, build):
    path = os.path.join(_DATA, name)
    if not os.path.exists(path):
        np.save(path, build())
    return np.load(path)


@lru_cache(None)
def type_patterns():
    """One QPSK pattern across the harmonics per frame type. They are chosen to look as little
    as possible like each other and like no pattern at all (a pilot seen twice), so that a
    receiver looking one symbol early or late does not see a header."""
    def build():
        rng = np.random.default_rng(11)
        best, best_worst = None, 9.0
        for _ in range(20000):
            cand = rng.integers(0, 4, (5, K))
            q = np.exp(0.5j * np.pi * cand)
            cross = np.abs(q @ q.conj().T) / K
            worst = max(float((cross - np.eye(5)).max()), float(np.abs(q.mean(axis=1)).max()))
            if worst < best_worst:
                best, best_worst = cand, worst
        return best
    return _cached("type_patterns_%d_v2.npy" % K, build)


@lru_cache(None)
def _pilots():
    """The first header symbol, one per direction of the call. The two are chosen to look
    unlike each other under every type pattern, so a unit never takes the echo of its own
    signal (a phone's sidetone, or echo from the network) for the far end."""
    def build():
        rng = np.random.default_rng(12)
        types = np.exp(0.5j * np.pi * type_patterns())
        shapes = np.vstack([np.ones((1, K)), types, types.conj()])
        best, best_worst = None, 9.0
        for _ in range(20000):
            cand = rng.integers(0, 4, (2, K))
            q = np.exp(0.5j * np.pi * cand)
            worst = float(np.abs((q[0] * q[1].conj() * shapes).mean(axis=1)).max())
            if worst < best_worst:
                best, best_worst = cand, worst
        return best
    return _cached("pilots_%d.npy" % K, build)


def pilot(role=0):
    """Phases of the first header symbol. role: 0 for what the initiator sends, 1 for the responder."""
    return _pilots()[role]


@lru_cache(None)
def training(role=0):
    """Known phases of the data symbols of a TRAIN superframe (one sequence per direction)."""
    return np.random.default_rng(13 + 100 * role).integers(0, 4, (DATA_SYMS, K))


@lru_cache(None)
def scramble():
    """Fixed phase offset per harmonic added to every data symbol. The codes are linear, so
    without it they contain words with all harmonics in phase: sharp pulses. The offsets are
    picked to give the lowest peak amplitude over a sample of codewords of every mode."""
    def build():
        rng = np.random.default_rng(14)
        sample = []
        for mode in MODES.values():
            phases = np.zeros((256, K), int)
            for (pos, _), book in zip(mode.parts, mode.books()):
                phases[:, list(pos)] = book[rng.integers(0, len(book), 256)]
            sample.append(phases)
        sample = np.vstack(sample)
        cand = rng.integers(0, 4, (3000, K))
        peaks = [np.abs(GRID.synth((sample + c) % 4)).max() for c in cand]
        return cand[int(np.argmin(peaks))]
    return _cached("scramble_%d.npy" % K, build)


def header_phases(kind, role=0):
    """The two header symbols of a superframe of the given type: (2, K) ints."""
    return np.stack([pilot(role), (pilot(role) + type_patterns()[kind]) % 4])
