"""Exact, append-only contract for owner-bound Factory escalations."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "loop-factory-escalation/v1"
OPEN = "open"
RESOLVED = "resolved"
RE_ESCALATED = "re_escalated"


class EscalationContractError(ValueError):
    """An escalation cannot be trusted, delivered, or resolved safely."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EscalationContractError(f"{field} must be a nonempty string")
    return value.strip()


def _timestamp(value: Any, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EscalationContractError(f"{field} must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise EscalationContractError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolution_receipt(value: Any, *, receipt_root: Path | str | None = None) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"schema", "path", "sha256"}:
        raise EscalationContractError(
            "resolution.receipt must contain schema, path, sha256"
        )
    if value.get("schema") != "file-sha256/v1":
        raise EscalationContractError("resolution.receipt.schema must equal file-sha256/v1")
    path = _text(value.get("path"), "resolution.receipt.path")
    digest = _text(value.get("sha256"), "resolution.receipt.sha256")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise EscalationContractError("resolution.receipt.sha256 must be lowercase SHA-256")
    receipt = {"schema": "file-sha256/v1", "path": path, "sha256": digest}
    if receipt_root is None:
        return receipt
    root = Path(receipt_root).resolve()
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise EscalationContractError("resolution.receipt.path escapes receipt root") from exc
    try:
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError as exc:
        raise EscalationContractError("resolution.receipt.path is unreadable") from exc
    if not secrets.compare_digest(actual, digest):
        raise EscalationContractError("resolution.receipt.sha256 does not match receipt")
    return receipt


def verify_resolution_receipt(
    value: Any, *, receipt_root: Path | str
) -> dict[str, str]:
    """Verify a state-local, content-addressed human resolution receipt."""
    return _resolution_receipt(value, receipt_root=receipt_root)


def _identifier(department: str, finding: str, owner: str, raised_at: str) -> str:
    material = json.dumps(
        {"department": department, "finding": finding, "owner": owner, "raised_at": raised_at},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def open_escalation(
    *, department: Any, finding: Any, owner: Any, deadline: Any,
    next_action: Any, raised_at: Any, evidence: Any, fyi_only: Any = False,
) -> dict[str, Any]:
    department = _text(department, "department")
    finding = _text(finding, "finding")
    owner = _text(owner, "owner")
    deadline = _timestamp(deadline, "deadline")
    raised_at = _timestamp(raised_at, "raised_at")
    next_action = _text(next_action, "next_action")
    if not isinstance(evidence, dict) or not evidence:
        raise EscalationContractError("evidence must be a nonempty mapping")
    if not isinstance(fyi_only, bool):
        raise EscalationContractError("fyi_only must be boolean")
    return {
        "schema": SCHEMA,
        "id": _identifier(department, finding, owner, raised_at),
        "department": department,
        "finding": finding,
        "owner": owner,
        "deadline": deadline,
        "next_action": next_action,
        "raised_at": raised_at,
        "status": OPEN,
        "evidence": evidence,
        "fyi_only": fyi_only,
        "re_escalation_count": 0,
    }


def validate(
    row: Any, *, receipt_root: Path | str | None = None
) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise EscalationContractError("escalation must be a mapping")
    status = row.get("status")
    base = {
        "schema", "id", "department", "finding", "owner", "deadline", "next_action",
        "raised_at", "status", "evidence", "fyi_only", "re_escalation_count",
    }
    if status == RESOLVED:
        expected = base | {"resolution"}
    elif status == RE_ESCALATED:
        expected = base | {"last_re_escalated_at"}
    elif status == OPEN:
        expected = base
    else:
        raise EscalationContractError("status must be open, resolved, or re_escalated")
    if set(row) != expected:
        raise EscalationContractError("escalation has missing or unknown fields")
    opened = open_escalation(
        department=row.get("department"),
        finding=row.get("finding"),
        owner=row.get("owner"),
        deadline=row.get("deadline"),
        next_action=row.get("next_action"),
        raised_at=row.get("raised_at"),
        evidence=row.get("evidence"),
        fyi_only=row.get("fyi_only"),
    )
    if row.get("id") != opened["id"]:
        raise EscalationContractError("id does not bind immutable escalation fields")
    count = row.get("re_escalation_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise EscalationContractError("re_escalation_count must be a nonnegative integer")
    if status == RE_ESCALATED:
        _timestamp(row.get("last_re_escalated_at"), "last_re_escalated_at")
        if count < 1:
            raise EscalationContractError("re_escalated status requires a positive count")
    if status == RESOLVED:
        resolution = row.get("resolution")
        if not isinstance(resolution, dict) or set(resolution) != {
            "owner", "decided_at", "action", "receipt"
        }:
            raise EscalationContractError("resolution must contain owner, decided_at, action, receipt")
        _text(resolution.get("owner"), "resolution.owner")
        _timestamp(resolution.get("decided_at"), "resolution.decided_at")
        _text(resolution.get("action"), "resolution.action")
        if receipt_root is None:
            raise EscalationContractError("resolution receipt root is required")
        _resolution_receipt(resolution.get("receipt"), receipt_root=receipt_root)
    return dict(row)


def re_escalate(open_row: dict[str, Any], *, at: str) -> dict[str, Any]:
    current = validate(open_row)
    if current["status"] not in {OPEN, RE_ESCALATED}:
        raise EscalationContractError("only open escalations may re-escalate")
    current["status"] = RE_ESCALATED
    current["re_escalation_count"] += 1
    current["last_re_escalated_at"] = _timestamp(at, "last_re_escalated_at")
    return validate(current)


def resolve(
    open_row: dict[str, Any], *, owner: str, decided_at: str, action: str,
    receipt: dict[str, str], receipt_root: Path | str,
) -> dict[str, Any]:
    current = validate(open_row)
    if current["status"] not in {OPEN, RE_ESCALATED}:
        raise EscalationContractError("only open escalations may resolve")
    current["status"] = RESOLVED
    current.pop("last_re_escalated_at", None)
    current["resolution"] = {
        "owner": _text(owner, "resolution.owner"),
        "decided_at": _timestamp(decided_at, "resolution.decided_at"),
        "action": _text(action, "resolution.action"),
        "receipt": _resolution_receipt(receipt, receipt_root=receipt_root),
    }
    return validate(current, receipt_root=receipt_root)
