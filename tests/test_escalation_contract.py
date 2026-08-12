from __future__ import annotations

import copy
import hashlib

import pytest

from factory.escalation_contract import (
    EscalationContractError,
    OPEN,
    RE_ESCALATED,
    RESOLVED,
    open_escalation,
    re_escalate,
    resolve,
    validate,
)


OPENED = {
    "department": "sales",
    "finding": "runmanifest_red",
    "owner": "human-owner",
    "deadline": "2026-08-10T12:00:00Z",
    "next_action": "review the failed run and choose a repair path",
    "raised_at": "2026-08-09T12:00:00Z",
    "evidence": {"run_id": "synthetic-run", "verdict": "red"},
}


def test_open_escalation_is_owner_deadline_evidence_and_id_bound():
    row = open_escalation(**OPENED)

    assert row["status"] == OPEN
    assert validate(row) == row
    assert row["id"] == open_escalation(**OPENED)["id"]


def test_resolution_requires_an_independently_verifiable_receipt(tmp_path):
    opened = open_escalation(**OPENED)
    reissued = re_escalate(opened, at="2026-08-09T18:00:00Z")
    assert reissued["status"] == RE_ESCALATED
    assert reissued["re_escalation_count"] == 1
    assert reissued["id"] == opened["id"]

    receipt_path = tmp_path / "decision.json"
    receipt_path.write_text('{"decision":"approved"}\n', encoding="utf-8")
    receipt = {
        "schema": "file-sha256/v1",
        "path": "decision.json",
        "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
    }
    resolved = resolve(
        reissued,
        owner="human-owner",
        decided_at="2026-08-09T19:00:00Z",
        action="approve an investigation",
        receipt=receipt,
        receipt_root=tmp_path,
    )
    assert resolved["status"] == RESOLVED
    assert validate(resolved, receipt_root=tmp_path) == resolved


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.__setitem__("owner", ""),
        lambda row: row.__setitem__("deadline", "not-a-date"),
        lambda row: row.__setitem__("evidence", {}),
        lambda row: row.__setitem__("status", "closed"),
        lambda row: row.__setitem__("id", "forged"),
        lambda row: row.__setitem__("unexpected", True),
    ],
)
def test_malformed_or_unowned_escalations_fail_closed(mutate):
    row = copy.deepcopy(open_escalation(**OPENED))
    mutate(row)

    with pytest.raises(EscalationContractError):
        validate(row)


def test_resolution_with_missing_or_tampered_receipt_fails_closed(tmp_path):
    row = open_escalation(**OPENED)
    invalid_receipt = {
        "schema": "file-sha256/v1",
        "path": "missing.json",
        "sha256": "0" * 64,
    }

    with pytest.raises(EscalationContractError, match="resolution.receipt.path"):
        resolve(
            row,
            owner="human-owner",
            decided_at="2026-08-09T19:00:00Z",
            action="approve an investigation",
            receipt=invalid_receipt,
            receipt_root=tmp_path,
        )
