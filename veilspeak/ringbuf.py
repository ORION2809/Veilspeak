"""Thread-safe ring buffer for real-time audio I/O.

Designed for single-producer single-consumer use between a sounddevice callback thread
(which must never block) and a processing thread. The write side overwrites the oldest
data when the buffer is full rather than blocking.
"""
import threading

import numpy as np


class AudioRingBuffer:
    """Fixed-capacity ring buffer of float64 audio samples.

    Thread-safe for one writer and one reader running concurrently: the writer appends
    with write(), the reader consumes with read(). Both touch only their own index and
    update the shared count under a lightweight lock.
    """

    def __init__(self, capacity):
        self._buf = np.zeros(capacity, dtype=np.float64)
        self._cap = capacity
        self._wpos = 0
        self._rpos = 0
        self._count = 0
        self._lock = threading.Lock()

    @property
    def capacity(self):
        return self._cap

    @property
    def available(self):
        with self._lock:
            return self._count

    def write(self, data):
        """Append samples. If len(data) > remaining space, the oldest samples are
        silently overwritten (the reader gets the most recent audio)."""
        data = np.asarray(data, dtype=np.float64).ravel()
        n = len(data)
        if n == 0:
            return
        if n >= self._cap:
            data = data[-self._cap:]
            n = self._cap
        with self._lock:
            space = self._cap - self._wpos
            if n <= space:
                self._buf[self._wpos:self._wpos + n] = data
            else:
                self._buf[self._wpos:] = data[:space]
                self._buf[:n - space] = data[space:]
            self._wpos = (self._wpos + n) % self._cap
            self._count = min(self._count + n, self._cap)
            if self._count == self._cap:
                self._rpos = self._wpos

    def read(self, n):
        """Read up to n samples. Returns exactly n samples; pads with zeros if fewer
        are available."""
        out = np.zeros(n, dtype=np.float64)
        with self._lock:
            avail = min(n, self._count)
            if avail == 0:
                return out
            space = self._cap - self._rpos
            if avail <= space:
                out[:avail] = self._buf[self._rpos:self._rpos + avail]
            else:
                out[:space] = self._buf[self._rpos:]
                out[space:avail] = self._buf[:avail - space]
            self._rpos = (self._rpos + avail) % self._cap
            self._count -= avail
        return out

    def peek(self, n):
        """Read up to n samples without consuming them."""
        out = np.zeros(n, dtype=np.float64)
        with self._lock:
            avail = min(n, self._count)
            if avail == 0:
                return out
            space = self._cap - self._rpos
            if avail <= space:
                out[:avail] = self._buf[self._rpos:self._rpos + avail]
            else:
                out[:space] = self._buf[self._rpos:]
                out[space:avail] = self._buf[:avail - space]
        return out

    def clear(self):
        with self._lock:
            self._wpos = self._rpos = self._count = 0
