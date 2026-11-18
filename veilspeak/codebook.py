"""DoV codebooks: additive quaternary codes chosen for large minimum Lee distance.

P2 picks the symbols of a codebook as the words of a quaternary code of length K (one 4-PSK
phase per harmonic) with the largest minimum Lee distance it can find, because Lee distance
between phase words is proportional to squared Euclidean distance between the waveforms.
"""
import os

import numpy as np


def _lee_to(words, c):
    d = np.abs(words - c)
    return np.minimum(d, 4 - d).sum(axis=1)


def search_additive(bits, k, tries=3000, seed=1):
    """Additive quaternary code of length k with 2**bits words: bits // 2 generators of order
    4 (the first is the all-ones word, so the code is closed under negating the waveform)
    plus one generator of order 2 if bits is odd. Random search keeps the largest minimum
    Lee distance (the code is a group, so that is its minimum Lee weight)."""
    import itertools
    rng = np.random.default_rng(seed)
    k1, k2 = bits // 2, bits % 2
    coef = np.array(list(itertools.product(*([range(4)] * k1 + [range(2)] * k2))))
    best_lee, best = -1, None
    for _ in range(tries):
        gen = np.vstack([np.ones((1, k), int), rng.integers(0, 4, (k1 - 1, k)),
                         2 * rng.integers(0, 2, (k2, k))])
        words = (coef @ gen) % 4
        lee = int(np.minimum(words, 4 - words).sum(axis=1)[1:].min())
        if lee > best_lee and len(np.unique(words, axis=0)) == len(words):
            best_lee, best = lee, words
    return best.astype(np.int8)


def lower_peak(book, synth_matrix, shifts=4096, seed=2):
    """Adds one fixed word to every codeword to lower the peak amplitude of the waveforms.
    Every Lee distance is unchanged. Without this, a linear code contains the words with all
    harmonics in phase: sharp pulses, which are fragile under timing error and clipping.
    synth_matrix: K x samples complex exponentials of the waveform."""
    rng = np.random.default_rng(seed)
    cand = rng.integers(0, 4, (shifts, book.shape[1]))
    step = max(1, len(book) // 256)                      # a sample of the words is enough to rank shifts
    sample = book[::step].astype(int)
    peaks = [np.abs((np.exp(0.5j * np.pi * (sample + c)) @ synth_matrix).real).max() for c in cand]
    return ((book + cand[int(np.argmin(peaks))]) % 4).astype(np.int8)


def min_lee_distance(book):
    best = 99
    for i in range(len(book) - 1):
        best = min(best, int(_lee_to(book[i + 1:], book[i]).min()))
    return best


def base_code(bits, k):
    """Additive code before peak reduction, cached on disk."""
    path = os.path.join(os.path.dirname(__file__), "data", "code_%d_%d.npy" % (k, bits))
    if not os.path.exists(path):
        np.save(path, search_additive(bits, k))
    return np.load(path)
