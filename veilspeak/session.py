"""Call-setup key agreement, modelled on ZRTP (RFC 6189): X25519 with a hash commitment and a
short authentication string (SAS) that the two people compare by voice.

Classic (pq_hybrid=False):

    step 0  I -> R   hello_I (version, flags, 16-byte nonce) | commit = H(pk_I | hello_I)[:16]
    step 1  R -> I   hello_R | pk_R
    step 2  I -> R   pk_I | confirm_I
    step 3  R -> I   confirm_R

Post-quantum hybrid (pq_hybrid=True):

    step 0  I -> R   hello_I | commit = H(pk_I | hello_I)[:16]      (same)
    step 1  R -> I   hello_R | pk_R_x25519 | pq_pk_R
    step 2  I -> R   pk_I_x25519 | pq_ct (encapsulated to pq_pk_R) | confirm_I
    step 3  R -> I   confirm_R                                       (same)

    The shared secret is HKDF(x25519_shared || pq_shared || retained, transcript, label).
    If the PQ primitive is broken, X25519 alone still protects the call; if X25519 is broken
    (e.g. by a future quantum computer), the PQ primitive alone does.

The commitment fixes the initiator's key before it sees the responder's, so a
man-in-the-middle gets one guess at matching the 20-bit SAS (success chance 2^-20 per call).
Keys come from HKDF-SHA256 over the shared secrets and an optional retained secret from an
earlier verified call, salted with the hash of every handshake message.

Each side keeps resending its current step until the other side's next step arrives, so lost
messages need no acknowledgements.
"""
import hashlib
import hmac
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .crypto import FrameOpener, FrameSealer, RekeyingOpener, RekeyingSealer

VERSION = 2
VERSION_PQ = 3
STEP_BYTES = (34, 50, 40, 8)            # payload size of steps 0-3 (classic)
_ZBASE32 = "ybndrfg8ejkmcpqxot1uwisza345h769"

# -- post-quantum KEM abstraction --------------------------------------------------------

_PQ_BACKEND = None

try:
    import oqs
    _kem_probe = oqs.KeyEncapsulation("ML-KEM-768")
    _PQ_PK_BYTES = _kem_probe.details["length_public_key"]
    _PQ_CT_BYTES = _kem_probe.details["length_ciphertext"]
    _PQ_SS_BYTES = _kem_probe.details["length_shared_secret"]
    del _kem_probe

    class _PQKeyPair:
        def __init__(self):
            self._kem = oqs.KeyEncapsulation("ML-KEM-768")
            self.public_key = self._kem.generate_keypair()

        def decapsulate(self, ct):
            return self._kem.decap_secret(ct)

    def _pq_encapsulate(pk):
        kem = oqs.KeyEncapsulation("ML-KEM-768")
        ct, ss = kem.encap_secret(pk)
        return ct, ss

    _PQ_BACKEND = "ML-KEM-768 (liboqs)"
except Exception:
    pass

if _PQ_BACKEND is None:
    # Placeholder: a second X25519 exchange standing in for ML-KEM-768.
    # Structurally correct hybrid protocol but NOT post-quantum secure.
    _PQ_PK_BYTES = 32
    _PQ_CT_BYTES = 32
    _PQ_SS_BYTES = 32

    class _PQKeyPair:
        def __init__(self):
            self._sk = X25519PrivateKey.generate()
            self.public_key = self._sk.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw)

        def decapsulate(self, ct):
            return self._sk.exchange(X25519PublicKey.from_public_bytes(ct))

    def _pq_encapsulate(pk):
        eph = X25519PrivateKey.generate()
        ct = eph.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        ss = eph.exchange(X25519PublicKey.from_public_bytes(pk))
        return ct, ss

    _PQ_BACKEND = "X25519 placeholder (not post-quantum)"

STEP_BYTES_PQ = (34, 50 + _PQ_PK_BYTES, 40 + _PQ_CT_BYTES, 8)
PQ_BACKEND = _PQ_BACKEND


class HandshakeError(Exception):
    pass


def _hkdf(ikm, salt, label, length=32):
    return HKDF(hashes.SHA256(), length, salt, label).derive(ikm)


class Handshake:
    """One side of the key agreement."""

    def __init__(self, initiator, retained=None, entropy=os.urandom, pq_hybrid=False):
        self.initiator = initiator
        self.pq_hybrid = pq_hybrid
        self.retained = retained or bytes(32)
        self._sk = X25519PrivateKey.generate()
        self._pk = self._sk.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self._pq = _PQKeyPair() if pq_hybrid else None
        version = VERSION_PQ if pq_hybrid else VERSION
        self._hello = bytes([version, 1 if pq_hybrid else 0]) + entropy(16)
        self._commit = hashlib.sha256(b"commit" + self._pk + self._hello).digest()[:16]
        self._out = (0, self._hello + self._commit) if initiator else None
        self._seen = {}
        self._step_bytes = STEP_BYTES_PQ if pq_hybrid else STEP_BYTES
        self.keys_ready = False
        self.complete = False
        self.failed = None
        self.sas = None
        self.peer_id = None
        self.retained_match = None

    def outgoing(self):
        """(step, payload) to send now and keep resending, or None."""
        return self._out

    def receive(self, step, payload):
        """Feed a step received from the peer. Repeats are ignored."""
        if self.failed or step in self._seen or len(payload) != self._step_bytes[step]:
            return
        try:
            self._advance(step, payload)
        except HandshakeError as exc:
            self.failed, self._out = str(exc), None
            raise

    def _advance(self, step, payload):
        if self.initiator:
            if step == 1:
                hello_r = payload[:18]
                self._check_hello(hello_r)
                pk_r = payload[18:50]
                pq_ss = None
                if self.pq_hybrid:
                    pq_pk_r = payload[50:]
                    pq_ct, pq_ss = _pq_encapsulate(pq_pk_r)
                    self._pq_ct = pq_ct
                self._derive(self._hello, self._commit, hello_r, pk_r, self._pk, pk_r, pq_ss)
                self._seen[1] = payload
                step2 = self._pk
                if self.pq_hybrid:
                    step2 += self._pq_ct
                step2 += self._confirm(True)
                self._out = (2, step2)
            elif step == 3 and self.keys_ready:
                self._check_confirm(payload, False)
                self._seen[3] = payload
                self.complete, self._out = True, None
        else:
            if step == 0:
                self._check_hello(payload[:18])
                self._seen[0] = payload
                step1 = self._hello + self._pk
                if self.pq_hybrid:
                    step1 += self._pq.public_key
                self._out = (1, step1)
            elif step == 2 and 0 in self._seen:
                hello_i, commit = self._seen[0][:18], self._seen[0][18:]
                pk_i = payload[:32]
                pq_ss = None
                if self.pq_hybrid:
                    pq_ct = payload[32:32 + _PQ_CT_BYTES]
                    confirm = payload[32 + _PQ_CT_BYTES:]
                    pq_ss = self._pq.decapsulate(pq_ct)
                else:
                    confirm = payload[32:]
                expected = hashlib.sha256(b"commit" + pk_i + hello_i).digest()[:16]
                if not hmac.compare_digest(expected, commit):
                    raise HandshakeError("initiator's key does not match its commitment")
                self._derive(hello_i, commit, self._hello, self._pk, pk_i, pk_i, pq_ss)
                self._check_confirm(confirm, True)
                self._seen[2] = payload
                self.complete, self._out = True, (3, self._confirm(False))

    def _check_hello(self, hello):
        if hello[0] not in (VERSION, VERSION_PQ):
            raise HandshakeError("unsupported protocol version")

    def _derive(self, hello_i, commit, hello_r, pk_r, pk_i, peer_pk, pq_ss=None):
        try:
            shared = self._sk.exchange(X25519PublicKey.from_public_bytes(peer_pk))
        except ValueError as exc:
            raise HandshakeError("invalid peer key") from exc
        transcript = hashlib.sha256(hello_i + commit + hello_r + pk_r + pk_i).digest()
        ikm = shared
        if pq_ss is not None:
            ikm += pq_ss
        ikm += self.retained
        self.retained_match = (self.retained != bytes(32))

        def key(label, length=32):
            return _hkdf(ikm, transcript, label, length)

        self._transcript = transcript
        self._keys = {
            "enc": {True: key(b"enc I->R"), False: key(b"enc R->I")},
            "mac": {True: key(b"mac I->R"), False: key(b"mac R->I")},
            "confirm": {True: key(b"confirm I"), False: key(b"confirm R")},
        }
        bits = int.from_bytes(key(b"sas", 4), "big") >> 12          # 20 bits
        self.sas = "".join(_ZBASE32[(bits >> s) & 31] for s in (15, 10, 5, 0))
        self.next_retained = key(b"retained")
        self.peer_id = hashlib.sha256(b"peer-id" + peer_pk).digest()
        self.keys_ready = True

    def _confirm(self, from_initiator):
        return hmac.new(self._keys["confirm"][from_initiator], self._transcript,
                        hashlib.sha256).digest()[:8]

    def _check_confirm(self, value, from_initiator):
        if not hmac.compare_digest(value, self._confirm(from_initiator)):
            raise HandshakeError("key confirmation failed")

    def sealer(self, rekeying=False):
        """Encrypts this side's outgoing frames."""
        assert self.keys_ready
        enc = self._keys["enc"][self.initiator]
        mac = self._keys["mac"][self.initiator]
        d = 0 if self.initiator else 1
        if rekeying:
            return RekeyingSealer(enc, mac, d)
        return FrameSealer(enc, mac, d)

    def opener(self, rekeying=False):
        """Verifies and decrypts the peer's frames."""
        assert self.keys_ready
        peer = not self.initiator
        enc = self._keys["enc"][peer]
        mac = self._keys["mac"][peer]
        d = 0 if peer else 1
        if rekeying:
            return RekeyingOpener(enc, mac, d)
        return FrameOpener(enc, mac, d)
