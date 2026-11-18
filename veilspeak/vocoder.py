"""Codec2 speech coding (1200 bit/s or 700C) in blocks of 40 ms frames, with loss concealment."""
import numpy as np
import pycodec2

FRAME_SAMPLES = 320             # 40 ms at 8 kHz, for both rates used here


class Vocoder:
    def __init__(self, bitrate):
        self.c2 = pycodec2.Codec2(bitrate)
        assert self.c2.samples_per_frame() == FRAME_SAMPLES
        self.bits = self.c2.bits_per_frame()
        self._bytes = (self.bits + 7) // 8
        self._last, self._lost = None, 0

    def encode(self, pcm):
        """int16 samples, a whole number of 40 ms frames -> packed bits (bytes)."""
        pcm = np.ascontiguousarray(pcm, dtype=np.int16).reshape(-1, FRAME_SAMPLES)
        bits = [np.unpackbits(np.frombuffer(self.c2.encode(f), dtype=np.uint8))[:self.bits] for f in pcm]
        return np.packbits(np.concatenate(bits)).tobytes()

    def split(self, data, frames):
        """Packed bits of `frames` frames -> list of per-frame byte strings."""
        bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))[:frames * self.bits].reshape(frames, self.bits)
        pad = np.zeros((frames, self._bytes * 8 - self.bits), dtype=np.uint8)
        return [np.packbits(row).tobytes() for row in np.hstack([bits, pad])]

    def decode(self, frame):
        """One frame's bytes -> 320 int16 samples. None conceals a lost frame: the last
        frame's parameters are repeated at falling level twice, then silence."""
        if frame is not None:
            self._last, self._lost = frame, 0
            return self.c2.decode(frame)
        self._lost += 1
        if self._last is None or self._lost > 2:
            return np.zeros(FRAME_SAMPLES, dtype=np.int16)
        return (self.c2.decode(self._last) * 0.5 ** self._lost).astype(np.int16)
