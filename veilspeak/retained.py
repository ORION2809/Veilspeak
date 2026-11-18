"""Retained-secret storage for key continuity between calls.

After a verified call (the users compared the SAS and confirmed it matched), the session
produces a retained secret. If the same two devices call again, supplying that secret to the
handshake strengthens the SAS: a man-in-the-middle that was not present in the earlier call
cannot produce the right secret and will cause the confirmation check to fail outright, rather
than relying on the 2^-20 SAS probability alone.

Secrets are stored in a JSON file keyed by peer identity (a hash of the peer's public key).
When a device_key is provided, secrets are encrypted at rest with AES-256-GCM.
"""
import hashlib
import json
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class RetainedStore:
    def __init__(self, path=None, device_key=None):
        self._path = os.path.expanduser(path or "~/.veilspeak/retained.json")
        self._device_key = device_key
        self._data = {}
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            return
        with open(self._path, "r") as f:
            self._data = json.load(f)

    def _save(self):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def _encrypt(self, secret):
        if self._device_key is None:
            return secret.hex()
        nonce = os.urandom(12)
        ct = AESGCM(self._device_key).encrypt(nonce, secret, None)
        return (nonce + ct).hex()

    def _decrypt(self, stored_hex):
        raw = bytes.fromhex(stored_hex)
        if self._device_key is None:
            return raw
        nonce, ct = raw[:12], raw[12:]
        return AESGCM(self._device_key).decrypt(nonce, ct, None)

    @staticmethod
    def peer_id(handshake):
        """A stable identifier for the peer, derived from their public key."""
        if not handshake.keys_ready:
            raise ValueError("handshake not complete")
        pk = handshake._seen.get(1, handshake._seen.get(2, b""))
        if handshake.initiator:
            pk_bytes = handshake._seen[1][18:]
        else:
            pk_bytes = handshake._seen[2][:32]
        return hashlib.sha256(b"peer-id" + pk_bytes).digest()

    def get(self, peer_id):
        """Returns the retained secret for this peer, or 32 zero bytes if none."""
        key = peer_id.hex()
        entry = self._data.get(key)
        if entry is None:
            return bytes(32)
        try:
            return self._decrypt(entry["secret"])
        except Exception:
            return bytes(32)

    def put(self, peer_id, secret):
        """Stores a retained secret after a verified call."""
        key = peer_id.hex()
        entry = self._data.get(key, {})
        entry["secret"] = self._encrypt(secret)
        entry["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry["call_count"] = entry.get("call_count", 0) + 1
        self._data[key] = entry
        self._save()

    def remove(self, peer_id):
        key = peer_id.hex()
        if key in self._data:
            del self._data[key]
            self._save()

    def peers(self):
        """Returns a list of (peer_id_bytes, entry_dict) for all stored peers."""
        return [(bytes.fromhex(k), v) for k, v in self._data.items()]
