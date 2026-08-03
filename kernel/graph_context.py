"""Signed graph execution context: the runner-minted identity token.

Cross-review B1: identity carried as plain env strings (OE_GRAPH_RUN_ID) was
spoofable — a node could rewrite its own os.environ and emit records under
another run's identity. Identity now travels ONLY as a signed token
(OE_GRAPH_CONTEXT), minted by the runner with the kernel signer and bound to
(department, run_id, node, attempt) with a TTL, using the same hardened
primitives as effect and step receipts (kernel/receipts.py signing style).
Raw env strings are never trusted for identity anywhere.

Verification model (honest about the trust boundary):
  * Appenders (runrecord / scores / model telemetry) take identity FROM THE
    TOKEN PAYLOAD only, enforce schema + expiry, and — wherever the kernel
    signing key is resolvable in-process (OE_KERNEL_SIGNING_KEY, i.e. any
    trusted context) — REQUIRE a valid signature. Tampered, malformed, or
    expired tokens refuse fail-closed.
  * Inside a confined node process the kernel key is deliberately scrubbed
    (kernel/capabilities.py), so an HMAC signature cannot be checked there —
    with a symmetric key, verify capability IS mint capability, and handing
    it to the node would recreate the forgery it prevents. The node still
    cannot MINT a token (no key); the runner is the trusted verifier: after
    every node execution it re-verifies the identity claims of each row the
    node appended and FAILS the node on a forged claim before any transition
    fires (factory/runner.py). This split — parse in the confined plane,
    signature enforcement in the trusted plane — is a documented limit of
    the same-key model, like the trusted-sanitizer note in lock_service.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import sys

_KERNEL_DIR = pathlib.Path(__file__).resolve().parent

SCHEMA = "graph-context/v1"
GRAPH_CONTEXT_ENV = "OE_GRAPH_CONTEXT"
DEFAULT_TTL_S = 3600  # one node-execution window, same order as step receipts
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}\Z")
_IDENTITY_FIELDS = ("department", "run_id", "node")


def _receipts():
    name = "graph_context_receipts_base"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _KERNEL_DIR / "receipts.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ContextInvalid(ValueError):
    """A graph context token that must not be trusted. Fail-closed."""


def issue_context(*, signer, now, department, run_id, node, attempt,
                  ttl_s=DEFAULT_TTL_S) -> str:
    """Mint one signed context token for one node attempt (runner-only)."""
    receipts = _receipts()
    payload = {
        "schema": SCHEMA,
        "department": department,
        "run_id": run_id,
        "node": node,
        "attempt": int(attempt),
        "exp": now + ttl_s,
    }
    _check_payload(payload, now=now)
    payload_bytes = receipts._canon(payload)
    return f"{receipts._base64url_encode(payload_bytes)}.{signer.sign(payload_bytes)}"


def _check_payload(payload, *, now) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ContextInvalid(f"graph context schema must be {SCHEMA!r}")
    for field in _IDENTITY_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
            raise ContextInvalid(f"graph context {field} must be a safe identifier")
    attempt = payload.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        raise ContextInvalid("graph context attempt must be a non-negative int")
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise ContextInvalid("graph context exp must be numeric")
    if now > exp:
        raise ContextInvalid("graph context expired")


def parse_context(token, *, now) -> dict:
    """Structural + expiry validation WITHOUT a signature check.

    For the confined plane only (no key material by design); every trusted
    plane must use verify_context. Raises ContextInvalid on anything off."""
    receipts = _receipts()
    try:
        encoded_payload, _sig = token.rsplit(".", 1)
        payload_bytes = receipts._base64url_decode(encoded_payload)
        payload = json.loads(payload_bytes)
    except (AttributeError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ContextInvalid(f"malformed graph context token: {exc}") from exc
    _check_payload(payload, now=now)
    return payload


def verify_context(token, *, signer, now) -> dict:
    """Full verification: structure + expiry + kernel signature."""
    receipts = _receipts()
    payload = parse_context(token, now=now)
    encoded_payload, sig = token.rsplit(".", 1)
    try:
        signature_valid = signer.verify(
            receipts._base64url_decode(encoded_payload), sig)
    except Exception as exc:
        raise ContextInvalid(f"graph context signature unverifiable: {exc}") from exc
    if not signature_valid:
        raise ContextInvalid("graph context signature invalid")
    return payload


def resolve_verifier():
    """Return the kernel signer when its key is present in-process, else None.

    None means the confined plane: parse-only there, with the runner as the
    trusted verifier of every appended identity claim."""
    if not os.environ.get("OE_KERNEL_SIGNING_KEY"):
        return None
    return _receipts().LocalSigner()


def load_context(*, now, token=None) -> dict | None:
    """Load the ambient graph context for an appender, fail-closed.

    Returns None when no context is present (emitter runs outside the graph
    runner). Otherwise returns the validated payload; the signature is
    REQUIRED to verify whenever the kernel signer is resolvable in-process.
    Any malformed, expired, or (where checkable) forged token raises
    ContextInvalid — identity is never silently dropped or defaulted."""
    if token is None:
        token = os.environ.get(GRAPH_CONTEXT_ENV) or None
    if token is None:
        return None
    verifier = resolve_verifier()
    if verifier is not None:
        return verify_context(token, signer=verifier, now=now)
    return parse_context(token, now=now)
