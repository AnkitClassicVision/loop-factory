"""PII-free, append-only funnel transition ledger."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PHONE_LIKE_RE = re.compile(r"\d{7,}")
ROW_KEYS = frozenset({"subject_id", "from_stage", "to_stage", "ts", "meta"})
META_KEYS = frozenset({"source", "cohort", "note"})


class LedgerError(RuntimeError):
    """An event would violate the ledger's fail-closed contract."""


@dataclass(frozen=True)
class TransitionWindow:
    rows: list[dict]
    malformed: int


def _utc(value: datetime | str | None) -> datetime:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LedgerError("ts must be an ISO datetime") from exc
    else:
        raise LedgerError("ts must be a datetime or ISO string")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _contains_pii(value: Any) -> bool:
    if isinstance(value, str):
        return bool(EMAIL_RE.search(value) or PHONE_LIKE_RE.search(value))
    if isinstance(value, dict):
        return any(_contains_pii(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_pii(item) for item in value)
    return False


def _validated_row(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != ROW_KEYS:
        raise LedgerError(f"event keys must be exactly {sorted(ROW_KEYS)}")
    for key in ("subject_id", "from_stage", "to_stage"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise LedgerError(f"{key} must be a non-empty string")
    meta = value["meta"]
    if not isinstance(meta, dict) or not set(meta).issubset(META_KEYS):
        raise LedgerError(f"meta keys must be within {sorted(META_KEYS)}")
    if _contains_pii(value):
        raise LedgerError("event contains an email or phone-like value")
    parsed = _utc(value["ts"])
    row = dict(value)
    row["ts"] = parsed.isoformat()
    return row


def append_event(
    state_dir,
    *,
    subject_id,
    from_stage,
    to_stage,
    ts=None,
    meta=None,
) -> Path:
    state = Path(state_dir)
    event_ts = _utc(ts)
    row = _validated_row({
        "subject_id": subject_id,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "ts": event_ts.isoformat(),
        "meta": {} if meta is None else meta,
    })
    path = state / "events.jsonl"
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise LedgerError(f"cannot read existing ledger: {exc}") from exc
        for line in lines:
            try:
                existing = _validated_row(json.loads(line))
            except (ValueError, TypeError, LedgerError):
                continue
            if (
                existing["subject_id"], existing["from_stage"], existing["to_stage"]
            ) == (subject_id, from_stage, to_stage) and _utc(existing["ts"]).date() == event_ts.date():
                raise LedgerError("duplicate transition within the same UTC day")
    state.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def read_transitions(state_dir, *, from_stage, to_stage, since, until) -> TransitionWindow:
    path = Path(state_dir) / "events.jsonl"
    start, end = _utc(since), _utc(until)
    rows: list[dict] = []
    malformed = 0
    if not path.exists():
        return TransitionWindow(rows, malformed)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return TransitionWindow(rows, 1)
    for line in lines:
        try:
            row = _validated_row(json.loads(line))
            timestamp = _utc(row["ts"])
        except (ValueError, TypeError, LedgerError):
            malformed += 1
            continue
        if row["from_stage"] == from_stage and row["to_stage"] == to_stage and start <= timestamp <= end:
            rows.append(row)
    return TransitionWindow(rows, malformed)
