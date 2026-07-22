import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "receipts", ROOT / "kernel/receipts.py"
)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

BIND = {"to": "a@b.co", "content_hash": "deadbeef"}


def _issue(*, key="k", nonce="n1"):
    return R.issue_receipt(
        "external_send",
        BIND,
        ttl_s=60,
        signer=R.LocalSigner(key=key),
        now=1000.0,
        nonce=nonce,
    )


def test_valid_receipt_verifies():
    tok = _issue()
    result = R.verify_receipt(
        tok,
        "external_send",
        BIND,
        signer=R.LocalSigner(key="k"),
        now=1010.0,
        seen_nonces=set(),
    )
    assert result.ok is True


def test_stale_receipt_rejected():
    result = R.verify_receipt(
        _issue(),
        "external_send",
        BIND,
        signer=R.LocalSigner(key="k"),
        now=2000.0,
        seen_nonces=set(),
    )
    assert result.ok is False
    assert "expired" in result.reason


def test_forged_signature_rejected():
    result = R.verify_receipt(
        _issue(key="attacker"),
        "external_send",
        BIND,
        signer=R.LocalSigner(key="k"),
        now=1010.0,
        seen_nonces=set(),
    )
    assert result.ok is False
    assert "signature" in result.reason


def test_replayed_nonce_rejected():
    tok = _issue()
    seen = set()
    first = R.verify_receipt(
        tok,
        "external_send",
        BIND,
        signer=R.LocalSigner(key="k"),
        now=1010.0,
        seen_nonces=seen,
    )
    second = R.verify_receipt(
        tok,
        "external_send",
        BIND,
        signer=R.LocalSigner(key="k"),
        now=1010.0,
        seen_nonces=seen,
    )
    assert first.ok is True
    assert second.ok is False
    assert "replay" in second.reason or "nonce" in second.reason


def test_mistagged_action_class_rejected():
    result = R.verify_receipt(
        _issue(),
        "crm_write",
        BIND,
        signer=R.LocalSigner(key="k"),
        now=1010.0,
        seen_nonces=set(),
    )
    assert result.ok is False
    assert "action_class" in result.reason


def test_binding_mismatch_rejected():
    result = R.verify_receipt(
        _issue(),
        "external_send",
        {"to": "evil@x.co", "content_hash": "deadbeef"},
        signer=R.LocalSigner(key="k"),
        now=1010.0,
        seen_nonces=set(),
    )
    assert result.ok is False
    assert "binding" in result.reason


def test_revoked_nonce_rejected():
    revoked = set()
    R.revoke("n7", revoked)
    result = R.verify_receipt(
        _issue(nonce="n7"),
        "external_send",
        BIND,
        signer=R.LocalSigner(key="k"),
        now=1010.0,
        seen_nonces=set(),
        revoked=revoked,
    )
    assert result.ok is False
    assert "revoked" in result.reason


def test_kms_signer_is_a_clean_seam():
    signer = R.KMSSigner(key_id="alias/oe-kernel")
    with pytest.raises(NotImplementedError):
        signer.sign(b"x")
