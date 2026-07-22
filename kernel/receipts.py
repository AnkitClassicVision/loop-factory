import base64
import hashlib
import hmac
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


class Signer(ABC):
    @abstractmethod
    def sign(self, payload: bytes) -> str:
        raise NotImplementedError

    @abstractmethod
    def verify(self, payload: bytes, sig: str) -> bool:
        raise NotImplementedError


class LocalSigner(Signer):
    def __init__(self, key=None):
        if key is None:
            key = os.environ.get("OE_KERNEL_SIGNING_KEY")
        if key is None:
            raise ValueError("missing OE_KERNEL_SIGNING_KEY")
        if isinstance(key, str):
            key = key.encode()
        self._key = key

    def sign(self, payload: bytes) -> str:
        digest = hmac.new(self._key, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def verify(self, payload: bytes, sig: str) -> bool:
        return hmac.compare_digest(self.sign(payload), sig)


class KMSSigner(Signer):
    def __init__(self, key_id):
        self.key_id = key_id

    def sign(self, payload: bytes) -> str:
        raise NotImplementedError("wire AWS KMS at OPS-B")

    def verify(self, payload: bytes, sig: str) -> bool:
        raise NotImplementedError("wire AWS KMS at OPS-B")


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _binding_hash(binding) -> str:
    return hashlib.sha256(_canon(binding)).hexdigest()


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def issue_receipt(action_class, binding, ttl_s, signer, now, nonce) -> str:
    payload = {
        "action_class": action_class,
        "binding_hash": _binding_hash(binding),
        "exp": now + ttl_s,
        "nonce": nonce,
    }
    payload_bytes = _canon(payload)
    sig = signer.sign(payload_bytes)
    return f"{_base64url_encode(payload_bytes)}.{sig}"


@dataclass
class ReceiptCheck:
    ok: bool
    reason: str


def verify_receipt(
    token,
    action_class,
    binding,
    signer,
    now,
    seen_nonces,
    revoked=frozenset(),
) -> ReceiptCheck:
    try:
        encoded_payload, sig = token.rsplit(".", 1)
        payload_bytes = _base64url_decode(encoded_payload)
        payload = json.loads(payload_bytes)
        if not isinstance(payload, dict):
            return ReceiptCheck(False, "malformed")
    except (AttributeError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return ReceiptCheck(False, "malformed")

    try:
        signature_valid = signer.verify(payload_bytes, sig)
    except Exception:
        return ReceiptCheck(False, "signature")
    if not signature_valid:
        return ReceiptCheck(False, "signature")

    try:
        if payload["action_class"] != action_class:
            return ReceiptCheck(False, "action_class mismatch")
        if payload["binding_hash"] != _binding_hash(binding):
            return ReceiptCheck(False, "binding mismatch")
        if now > payload["exp"]:
            return ReceiptCheck(False, "expired")
        nonce = payload["nonce"]
        if nonce in revoked:
            return ReceiptCheck(False, "revoked")
        if nonce in seen_nonces:
            return ReceiptCheck(False, "replay/nonce")
        seen_nonces.add(nonce)
    except (KeyError, TypeError, ValueError, OverflowError):
        return ReceiptCheck(False, "malformed")

    return ReceiptCheck(True, "ok")


def revoke(nonce, revoked):
    revoked.add(nonce)
