"""Per-frame encryption: AES-256-CTR, then a 16-bit truncated HMAC-SHA256 tag (encrypt-then-MAC).

Message layout: ciphertext | counter low 6 bits + 2 status bits | 16-bit tag.

The frame counter advances once per 240 ms of the sender's stream, whether or not a frame is
sent in that slot, so the receiver can predict it from elapsed time. Only its low 6 bits
travel; the receiver accepts the single counter value that fits them near the predicted one,
and each counter value only once (a sliding window, as in IPsec and SRTP, so a frame recovered
after its successor still counts). That rejects replays and keeps a chance tag match (2^-16
per try) from throwing the counter far ahead. The status bits (the receiver's link report)
and the frame type are covered by the tag.

In-call re-keying: RekeyingSealer and RekeyingOpener wrap the base classes and derive fresh
keys every REKEY_INTERVAL frames (~8 minutes) via HKDF. Both sides rekey at the same counter
value so they stay in sync; old keys are overwritten with zeros.
"""
import hashlib
import hmac

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

TAG_BYTES = 2
OVERHEAD = 1 + TAG_BYTES


def _keystream(key, direction, counter, length):
    nonce = bytes([direction]) + counter.to_bytes(4, "big") + bytes(11)
    return Cipher(algorithms.AES(key), modes.CTR(nonce)).encryptor().update(bytes(length))


def _tag(key, direction, counter, kind, status, ciphertext):
    data = bytes([direction, kind, status]) + counter.to_bytes(4, "big") + ciphertext
    return hmac.new(key, data, hashlib.sha256).digest()[:TAG_BYTES]


class FrameSealer:
    def __init__(self, enc_key, mac_key, direction):
        assert len(enc_key) == 32 and len(mac_key) == 32
        self.enc, self.mac, self.dir = enc_key, mac_key, direction
        self.last = -1

    def seal(self, counter, kind, status, plaintext):
        if counter <= self.last:                        # never reuse a (key, counter) pair
            raise ValueError("frame counter must increase")
        if counter >= 1 << 32:
            raise OverflowError("frame counter exhausted; rekey")
        self.last = counter
        ks = _keystream(self.enc, self.dir, counter, len(plaintext))
        ct = bytes(a ^ b for a, b in zip(plaintext, ks))
        return ct + bytes([(counter & 63) << 2 | (status & 3)]) + _tag(self.mac, self.dir, counter, kind, status & 3, ct)


class FrameOpener:
    WINDOW = 64                         # counters behind the newest that may still arrive

    def __init__(self, enc_key, mac_key, direction, slack=6, first=8):
        self.enc, self.mac, self.dir = enc_key, mac_key, direction
        self.last = -1                  # newest counter accepted
        self._seen = 0                  # bit i set: counter last - i was accepted
        self.slack = slack              # counter steps of tolerance around the predicted value
        self.first = first              # 64-step windows searched before any frame was accepted

    def _unused(self, counter):
        if counter > self.last:
            return True
        return self.last - counter < self.WINDOW and not (self._seen >> (self.last - counter)) & 1

    def _candidates(self, lsb, expected):
        if expected is None:
            return [c for c in (lsb + 64 * i for i in range(self.first)) if self._unused(c)]
        counter = expected + ((lsb - expected + 32) & 63) - 32
        return [counter] if counter >= 0 and self._unused(counter) and abs(counter - expected) <= self.slack else []

    def find(self, message, kind, expected=None):
        """Counter under which the message authenticates, or None."""
        ct, head, tag = message[:-OVERHEAD], message[-OVERHEAD], message[-TAG_BYTES:]
        for counter in self._candidates(head >> 2, expected):
            if hmac.compare_digest(tag, _tag(self.mac, self.dir, counter, kind, head & 3, ct)):
                return counter
        return None

    def open(self, message, kind, expected=None):
        """Returns (counter, status, plaintext) or None. A counter is accepted once."""
        counter = self.find(message, kind, expected)
        if counter is None:
            return None
        if counter > self.last:
            self._seen = ((self._seen << (counter - self.last)) | 1) & ((1 << self.WINDOW) - 1)
            self.last = counter
        else:
            self._seen |= 1 << (self.last - counter)
        ct = message[:-OVERHEAD]
        ks = _keystream(self.enc, self.dir, counter, len(ct))
        return counter, message[-OVERHEAD] & 3, bytes(a ^ b for a, b in zip(ct, ks))


# ---------------------------------------------------------------------------
# In-call re-keying wrappers
# ---------------------------------------------------------------------------

REKEY_INTERVAL = 2048

def _rekey_derive(old_key, counter, label):
    salt = counter.to_bytes(4, "big")
    return HKDF(hashes.SHA256(), 32, salt, label).derive(old_key)


def _zero(ba):
    """Overwrite a bytes object's contents. Works for bytearray; for immutable bytes the
    variable is simply replaced (Python does not guarantee in-place zeroing of bytes)."""
    if isinstance(ba, bytearray):
        for i in range(len(ba)):
            ba[i] = 0


class RekeyingSealer:
    """FrameSealer that derives fresh keys every REKEY_INTERVAL frames."""

    def __init__(self, enc_key, mac_key, direction, interval=REKEY_INTERVAL):
        self._enc = bytearray(enc_key)
        self._mac = bytearray(mac_key)
        self._dir = direction
        self._interval = interval
        self._inner = FrameSealer(bytes(self._enc), bytes(self._mac), direction)
        self._next_rekey = interval
        self.rekey_count = 0

    def seal(self, counter, kind, status, plaintext):
        if counter >= self._next_rekey:
            self._rotate(counter)
        return self._inner.seal(counter, kind, status, plaintext)

    def _rotate(self, counter):
        boundary = (counter // self._interval) * self._interval
        new_enc = _rekey_derive(bytes(self._enc), boundary, b"rekey-enc")
        new_mac = _rekey_derive(bytes(self._mac), boundary, b"rekey-mac")
        _zero(self._enc)
        _zero(self._mac)
        self._enc = bytearray(new_enc)
        self._mac = bytearray(new_mac)
        self._inner = FrameSealer(bytes(self._enc), bytes(self._mac), self._dir)
        self._inner.last = self._next_rekey - 1
        self._next_rekey = boundary + self._interval
        self.rekey_count += 1


class RekeyingOpener:
    """FrameOpener that derives fresh keys every REKEY_INTERVAL frames."""

    def __init__(self, enc_key, mac_key, direction, interval=REKEY_INTERVAL, slack=6, first=8):
        self._enc = bytearray(enc_key)
        self._mac = bytearray(mac_key)
        self._dir = direction
        self._interval = interval
        self._inner = FrameOpener(bytes(self._enc), bytes(self._mac), direction, slack, first)
        self._next_rekey = interval
        self.rekey_count = 0

    def _maybe_rotate(self, counter):
        if counter < self._next_rekey:
            return
        boundary = (counter // self._interval) * self._interval
        new_enc = _rekey_derive(bytes(self._enc), boundary, b"rekey-enc")
        new_mac = _rekey_derive(bytes(self._mac), boundary, b"rekey-mac")
        _zero(self._enc)
        _zero(self._mac)
        self._enc = bytearray(new_enc)
        self._mac = bytearray(new_mac)
        old_last, old_seen = self._inner.last, self._inner._seen
        self._inner = FrameOpener(bytes(self._enc), bytes(self._mac), self._dir,
                                  self._inner.slack, self._inner.first)
        self._inner.last, self._inner._seen = old_last, old_seen
        self._next_rekey = boundary + self._interval
        self.rekey_count += 1

    def find(self, message, kind, expected=None):
        ct, head, tag = message[:-OVERHEAD], message[-OVERHEAD], message[-TAG_BYTES:]
        for counter in self._inner._candidates(head >> 2, expected):
            if counter >= self._next_rekey:
                boundary = (counter // self._interval) * self._interval
                trial_mac = _rekey_derive(bytes(self._mac), boundary, b"rekey-mac")
                if hmac.compare_digest(tag, _tag(trial_mac, self._dir, counter, kind, head & 3, ct)):
                    self._maybe_rotate(counter)
                    return counter
            else:
                if hmac.compare_digest(tag, _tag(self._inner.mac, self._dir, counter, kind, head & 3, ct)):
                    return counter
        return None

    def open(self, message, kind, expected=None):
        counter = self.find(message, kind, expected)
        if counter is None:
            return None
        self._maybe_rotate(counter)
        inner = self._inner
        if counter > inner.last:
            inner._seen = ((inner._seen << (counter - inner.last)) | 1) & ((1 << inner.WINDOW) - 1)
            inner.last = counter
        else:
            inner._seen |= 1 << (inner.last - counter)
        ct = message[:-OVERHEAD]
        ks = _keystream(inner.enc, inner.dir, counter, len(ct))
        return counter, message[-OVERHEAD] & 3, bytes(a ^ b for a, b in zip(ct, ks))

    @property
    def last(self):
        return self._inner.last

    @last.setter
    def last(self, value):
        self._inner.last = value

    @property
    def slack(self):
        return self._inner.slack

    @slack.setter
    def slack(self, value):
        self._inner.slack = value
