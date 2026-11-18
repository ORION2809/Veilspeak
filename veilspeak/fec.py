"""Frame coding: bytes <-> Reed-Solomon symbols, shortened RS codes with erasure decoding.

Each sub-symbol of the modem (6 or 12 bits) is one RS symbol, so a wrongly demodulated
sub-symbol costs exactly one RS symbol.
"""
from math import comb

import numpy as np
import reedsolo

_PRIM = {6: 0x43, 12: 0x1069}
_CODECS = {}


def _codec(nsym, bits):
    key = (nsym, bits)
    if key not in _CODECS:
        # reedsolo keeps one module-wide choice of array type, set by the last codec built.
        # Build a wide-field codec last so that choice always holds values above 255.
        _CODECS[key] = reedsolo.RSCodec(nsym, nsize=(1 << bits) - 1, c_exp=bits, prim=_PRIM[bits])
        reedsolo.RSCodec(2, nsize=4095, c_exp=12, prim=_PRIM[12])
    return _CODECS[key]


def bytes_to_symbols(data, bits):
    stream = np.unpackbits(np.frombuffer(bytes(data), dtype=np.uint8)).reshape(-1, bits)
    return stream @ (1 << np.arange(bits - 1, -1, -1))


def symbols_to_bytes(symbols, bits):
    sym = np.asarray(symbols, dtype=np.int64)
    stream = (sym[:, None] >> np.arange(bits - 1, -1, -1)) & 1
    return np.packbits(stream.astype(np.uint8).reshape(-1)).tobytes()


class FrameCodec:
    """One FEC frame of a mode: message bytes <-> rs_n symbols."""

    def __init__(self, mode):
        self.n, self.k, self.bits = mode.rs_n, mode.rs_k, mode.sym_bits
        self.rs = _codec(self.n - self.k, self.bits)

    def encode(self, message):
        assert len(message) * 8 == self.k * self.bits
        return np.array(self.rs.encode(bytes_to_symbols(message, self.bits).tolist()), dtype=np.int64)

    def decode(self, symbols, confidence, check):
        """Returns (message, corrected symbols, symbols corrected) for the first decoding that
        satisfies check(message), else None. Tries errors-only first, then erases the least
        confident symbols in growing numbers (P2 section 5.1).

        With many symbols erased little of the code is left to tell a codeword from noise, so
        a decoding is only passed on to the message check if noise would have produced one
        like it (that many erasures and errors) less than once in about 128 tries."""
        received = np.array([int(s) for s in symbols])
        weakest = np.argsort(confidence)
        spare = self.n - self.k
        for erased in list(range(0, spare - 1, 2)) + [spare - 1]:
            erase = [int(i) for i in weakest[:erased]]
            try:
                msg, full, _ = self.rs.decode(received.tolist(), erase_pos=erase)
            except reedsolo.ReedSolomonError:
                continue
            full = np.array(list(full), dtype=np.int64)
            wrong = full != received
            errors = int(wrong.sum() - wrong[erase].sum())
            if np.log2(comb(self.n - erased, errors)) + (errors - spare + erased) * self.bits > -7:
                continue
            message = symbols_to_bytes(list(msg), self.bits)
            if check(message):
                return message, full, int(wrong.sum())
        return None
