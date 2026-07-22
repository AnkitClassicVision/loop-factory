import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import receipts

import hashlib
import json


class GatewayDenied(RuntimeError):
    pass


def content_hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def send_binding(to, subject, body, slot=None) -> dict:
    """The canonical binding a send receipt commits to. Built identically at
    issuance and at use so any tamper (recipient, subject, body, or slot)
    changes the hash and fails closed. GLM P1 #7."""
    return {
        "to": to,
        "subject": subject,
        "content_hash": content_hash(body),
        "slot": slot,
    }


def dispatch(
    to,
    subject,
    body,
    receipt,
    *,
    signer,
    now,
    seen_nonces,
    revoked=frozenset(),
    slot=None,
    live=False,
    sink=None,
) -> dict:
    if receipt is None:
        raise GatewayDenied("no receipt")

    binding = send_binding(to, subject, body, slot)
    check = receipts.verify_receipt(
        receipt,
        "external_send",
        binding,
        signer=signer,
        now=now,
        seen_nonces=seen_nonces,
        revoked=revoked,
    )
    if not check.ok:
        raise GatewayDenied("receipt: " + check.reason)

    if live:
        raise GatewayDenied("no wired real adapter in kernel v1")

    if sink is not None:
        sink = pathlib.Path(sink)
        sink.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "to": to,
            "subject": subject,
            "body": body,
            "mode": "shadow",
            "now": now,
        }
        with sink.open("a") as fake_sink:
            fake_sink.write(json.dumps(row) + chr(10))

    return {
        "mode": "shadow",
        "delivered": False,
        "sink": str(sink) if sink else None,
    }
