"""Step receipts: signed transition tokens for the deterministic runner.

Closes the audit gap 'step receipts are forgeable plain JSON': a transition
token is HMAC-signed over a binding of (department, graph identity + hash,
release hash, run, node, attempt, output hash) using the SAME hardened
issue/verify path as effect receipts (kernel/receipts.py) — which this module
extends and must never weaken. A token is single-use PERIOD: one consumption,
any successor (fan-out mints one token per transition), and consumption is
DURABLE — a runner restart must not reopen replay.
"""
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = _load("step_receipts_kernel_receipts", "kernel/receipts.py")
SR = _load("step_receipts", "kernel/step_receipts.py")


IDENTITY = dict(
    department="demo",
    graph_id="SG-RUN",
    graph_hash="a" * 64,
    release_hash="feedfeedfeedfeed",
    run_id="SG-RUN-0001",
    node_id="N1",
    attempt=1,
)
OUTPUT = {"status": "ok", "delivered_count": 0}


def _signer(key="k"):
    return R.LocalSigner(key=key)


def _issue(now=1000.0, key="k", **overrides):
    identity = {**IDENTITY, **overrides}
    return SR.issue_step_receipt(
        signer=_signer(key), now=now, output=OUTPUT, ttl_s=600, **identity)


def _verify(token, *, now=1010.0, key="k", consumed=None, output=OUTPUT,
            **overrides):
    identity = {**IDENTITY, **overrides}
    return SR.verify_step_receipt(
        token, signer=_signer(key), now=now, output=output,
        consumed=consumed if consumed is not None else set(), **identity)


def test_valid_step_receipt_verifies():
    assert _verify(_issue()).ok is True


def test_forged_plain_json_rejected():
    forged = '{"node_id": "N1", "status": "done"}'
    assert _verify(forged).ok is False


def test_wrong_key_rejected():
    result = _verify(_issue(key="attacker"))
    assert result.ok is False
    assert "signature" in result.reason


def test_expired_rejected():
    result = _verify(_issue(now=100.0), now=100.0 + 601)
    assert result.ok is False
    assert "expired" in result.reason


def test_tampered_output_rejected():
    result = _verify(_issue(), output={"status": "ok", "delivered_count": 7})
    assert result.ok is False
    assert "binding" in result.reason


def test_cross_run_replay_rejected():
    result = _verify(_issue(), run_id="SG-RUN-0002")
    assert result.ok is False
    assert "binding" in result.reason


def test_release_disagreement_rejected():
    result = _verify(_issue(), release_hash="0123012301230123")
    assert result.ok is False
    assert "binding" in result.reason


def test_single_use_period_any_successor(tmp_path):
    # One consumption, ANY successor: the second verify dies even though a
    # different successor would consume it — fan-out mints one token per edge.
    token = _issue()
    consumed = SR.DurableNonceStore(tmp_path / "consumed.jsonl")
    assert _verify(token, consumed=consumed).ok is True
    replay = _verify(token, consumed=consumed)
    assert replay.ok is False
    assert "replay" in replay.reason


def test_consumption_survives_restart(tmp_path):
    # Simulated restart: a FRESH store over the same file (new process) must
    # still refuse the consumed token — durability, not process memory.
    token = _issue()
    path = tmp_path / "consumed.jsonl"
    assert _verify(token, consumed=SR.DurableNonceStore(path)).ok is True
    after_restart = _verify(token, consumed=SR.DurableNonceStore(path))
    assert after_restart.ok is False
    assert "replay" in after_restart.reason


def test_distinct_tokens_for_same_step_consume_independently(tmp_path):
    consumed = SR.DurableNonceStore(tmp_path / "consumed.jsonl")
    first, second = _issue(), _issue()
    assert first != second  # fresh nonce per token
    assert _verify(first, consumed=consumed).ok is True
    assert _verify(second, consumed=consumed).ok is True


def test_torn_trailing_line_is_tolerated(tmp_path):
    path = tmp_path / "consumed.jsonl"
    token = _issue()
    assert _verify(token, consumed=SR.DurableNonceStore(path)).ok is True
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"nonce": "tor')  # crashed mid-write
    reloaded = SR.DurableNonceStore(path)
    assert _verify(token, consumed=reloaded).ok is False  # prior consumption kept


def test_non_finite_output_has_no_canonical_hash():
    import pytest
    with pytest.raises(ValueError):
        SR.output_hash({"n": float("inf")})
