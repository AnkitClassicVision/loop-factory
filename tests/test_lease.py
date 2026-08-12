from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from kernel.lease import LeaseHeld, acquire, refusal_receipt, release


NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def test_acquire_happy_path_writes_frozen_shape(tmp_path):
    lease = acquire(tmp_path, holder="alpha", ttl_s=60, now=NOW)
    row = json.loads((tmp_path / "driver.lease").read_text())
    assert set(row) == {"holder", "acquired_at", "expires_at", "nonce"}
    assert row["holder"] == lease.holder == "alpha"
    assert row["nonce"] == lease.nonce
    assert lease.path == tmp_path / "driver.lease"


def test_second_acquire_unexpired_refuses_and_receipt_is_durable(tmp_path):
    acquire(tmp_path, holder="alpha", ttl_s=60, now=NOW)
    with pytest.raises(LeaseHeld) as caught:
        acquire(tmp_path, holder="beta", ttl_s=60, now=NOW)
    assert caught.value.holder == "alpha"
    refusal_receipt(tmp_path, loser="beta", holder=caught.value.holder, now=NOW)
    row = json.loads((tmp_path / "lease-refusals.jsonl").read_text().strip())
    assert row == {"ts": NOW.isoformat(), "loser": "beta", "holder": "alpha"}


def test_expired_takeover_is_atomic_and_records_predecessor(tmp_path):
    first = acquire(tmp_path, holder="alpha", ttl_s=1, now=NOW)
    second = acquire(tmp_path, holder="beta", ttl_s=60, now=NOW + timedelta(seconds=2))
    row = json.loads(second.path.read_text())
    assert row["takeover_from"] == "alpha"
    assert row["nonce"] != first.nonce


def test_release_wrong_nonce_refuses_and_preserves_successor(tmp_path):
    first = acquire(tmp_path, holder="alpha", ttl_s=1, now=NOW)
    second = acquire(tmp_path, holder="beta", ttl_s=60, now=NOW + timedelta(seconds=2))
    with pytest.raises(LeaseHeld) as caught:
        release(first)
    assert caught.value.holder == "beta"
    assert second.path.exists()


def test_two_acquirer_arbitration_has_one_holder_and_one_refusal(tmp_path):
    winners = []
    for holder in ("alpha", "beta"):
        try:
            winners.append(acquire(tmp_path, holder=holder, ttl_s=60, now=NOW))
        except LeaseHeld as exc:
            refusal_receipt(tmp_path, loser=holder, holder=exc.holder, now=NOW)
    assert [lease.holder for lease in winners] == ["alpha"]
    rows = (tmp_path / "lease-refusals.jsonl").read_text().splitlines()
    assert len(rows) == 1

