"""A DoV waveform: which harmonics a symbol carries, how long it lasts, and which part of it
the receiver analyses.

P2's symbols are exactly one analysis window long (2.5 ms, 400 Hz harmonic spacing). A symbol
may also be longer than its analysis window; the extra samples at the start are a guard
interval (a cyclic prefix), which keeps the smearing between symbols out of the window.
"""
import os
from dataclasses import dataclass
from functools import cached_property

import numpy as np

FS = 8000
AMPLITUDE_RMS = 0.126          # about -18 dBFS


@dataclass(frozen=True)
class Waveform:
    n: int                      # samples per symbol at 8 kHz
    window: int = 20            # analysis window, samples; harmonics are orthogonal over it
    bins: tuple = tuple(range(1, 9))   # harmonic k is at bins[k] * FS / window Hz

    @property
    def k(self):
        return len(self.bins)

    @property
    def guard(self):
        return self.n - self.window

    @property
    def baud(self):
        return FS / self.n

    @property
    def amplitude(self):        # per-harmonic amplitude giving the target RMS
        return AMPLITUDE_RMS * np.sqrt(2.0 / self.k)

    @cached_property
    def hz(self):
        return np.array(self.bins) * FS / self.window

    @cached_property
    def omega(self):            # phase advance of each harmonic per sample of delay
        return 2 * np.pi * np.array(self.bins) / self.window

    @cached_property
    def _synth(self):           # K x n; phase reference at the start of the analysis window
        t = np.arange(self.n) - self.guard
        return np.exp(1j * np.outer(self.omega, t))

    @cached_property
    def analysis(self):         # window x K; a clean symbol gives unit phasors
        t = np.arange(self.window)
        return np.exp(-1j * np.outer(t, self.omega)) / (self.amplitude * self.window / 2)

    def book(self, bits):
        """Codebook of 2**bits quaternary words (words x K) for this waveform."""
        from . import codebook
        name = "book_%d_%d_%d_%d_%d.npy" % (self.k, bits, self.n, self.window, self.bins[0])
        path = os.path.join(os.path.dirname(__file__), "data", name)
        if not os.path.exists(path):
            one_period = self._synth[:, self.guard:]
            np.save(path, codebook.lower_peak(codebook.base_code(bits, self.k), one_period))
        return np.load(path)

    def synth(self, phases):
        """phases: (symbols, K) ints 0..3 -> float audio."""
        q = np.exp(0.5j * np.pi * np.asarray(phases))
        return (self.amplitude * (q @ self._synth).real).reshape(-1)

    def demux(self, samples):
        """samples: (symbols * n,) aligned to symbol starts -> (symbols, K) complex bins."""
        frames = np.asarray(samples).reshape(-1, self.n)[:, self.guard:]
        return frames @ self.analysis


P2_WAVEFORM = Waveform(20, 20, tuple(range(1, 9)))      # 8 harmonics, 400-3200 Hz, 400 baud
