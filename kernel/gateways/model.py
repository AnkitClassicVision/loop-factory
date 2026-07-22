import sys, pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import receipts

import hashlib


class GatewayDenied(RuntimeError):
    pass


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def model_binding(prompt, sanitized=True) -> dict:
    return {"prompt_hash": prompt_hash(prompt), "sanitized": bool(sanitized)}


def call_model(prompt, receipt, *, signer, now, seen_nonces, revoked=frozenset(), runner) -> str:
    if receipt is None:
        raise GatewayDenied("no sanitation receipt")
    chk = receipts.verify_receipt(
        receipt,
        "model_call",
        model_binding(prompt),
        signer=signer,
        now=now,
        seen_nonces=seen_nonces,
        revoked=revoked,
    )
    if not chk.ok:
        raise GatewayDenied("receipt: " + chk.reason)
    return runner(prompt)
