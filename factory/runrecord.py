"""Build, validate, and durably append factory run records.

The v2 record deliberately excludes prompts and message bodies. Records are
local JSON lines, serialized under an advisory file lock so concurrent writers
cannot interleave their output.
"""
from __future__ import annotations

import copy
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "run-record/v2"

_REQUIRED_FIELDS = frozenset(
    {
        "schema",
        "rev",
        "run_id",
        "department",
        "node",
        "epoch",
        "ts",
        "attempt",
        "round",
        "release",
        "trigger",
        "engine",
        "model",
        "auth_class",
        "usage",
        "cost",
        "duration_ms",
        "status",
        "errors",
        "artifacts",
        "receipts",
        "evaluator",
        "approval",
        "external_actions_taken",
    }
)
_DERIVED_FIELDS = frozenset({"metered_violation"})
_AUTH_CLASSES = frozenset(
    {"oauth_cli", "service_oauth", "local_model", "blocked", None}
)
_STATUSES = frozenset(
    {"ok", "blocked", "error", "halted", "killed", "escalated", "skipped"}
)
_TRIGGER_KINDS = frozenset({"time", "event", "goal", "manual", "escalation"})
_COST_LANES = frozenset({"flat_subscription", "metered_forbidden"})


def new_run_id() -> str:
    """Return a time-sortable UTC run identifier with random uniqueness."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{os.urandom(4).hex()}"


def _fail(field: str, detail: str) -> None:
    raise ValueError(f"invalid field {field}: {detail}")


def _require_type(
    field: str,
    value: Any,
    expected: type,
    *,
    allow_none: bool = False,
) -> None:
    if allow_none and value is None:
        return
    if expected is int and isinstance(value, bool):
        _fail(field, "must be int, not bool")
    if not isinstance(value, expected):
        _fail(field, f"must be {expected.__name__}")


def _require_shape(field: str, value: dict[str, Any], keys: set[str]) -> None:
    actual = set(value)
    missing = keys - actual
    if missing:
        _fail(f"{field}.{sorted(missing)[0]}", "is required")
    unknown = actual - keys
    if unknown:
        _fail(f"{field}.{sorted(unknown)[0]}", "is not allowed")


def _validate_release(value: Any) -> None:
    if value is None:
        return
    _require_type("release", value, dict)
    _require_shape("release", value, {"hash", "source_ref"})
    _require_type("release.hash", value["hash"], str)
    _require_type("release.source_ref", value["source_ref"], str)


def _validate_trigger(value: Any) -> None:
    if value is None:
        return
    _require_type("trigger", value, dict)
    _require_shape("trigger", value, {"kind", "id", "dedupe_key"})
    _require_type("trigger.kind", value["kind"], str)
    if value["kind"] not in _TRIGGER_KINDS:
        _fail("trigger.kind", f"must be one of {sorted(_TRIGGER_KINDS)}")
    _require_type("trigger.id", value["id"], str)
    _require_type("trigger.dedupe_key", value["dedupe_key"], str)


def _validate_usage(value: Any) -> None:
    if value is None:
        return
    _require_type("usage", value, dict)
    keys = {"input_tokens", "output_tokens", "cache_read", "cache_creation"}
    _require_shape("usage", value, keys)
    for key in sorted(keys):
        _require_type(f"usage.{key}", value[key], int)


def _validate_cost(value: Any) -> bool:
    if value is None:
        return False
    _require_type("cost", value, dict)
    _require_shape("cost", value, {"lane", "model_calls"})
    _require_type("cost.lane", value["lane"], str)
    if value["lane"] not in _COST_LANES:
        _fail("cost.lane", f"must be one of {sorted(_COST_LANES)}")
    _require_type("cost.model_calls", value["model_calls"], int)
    return value["lane"] == "metered_forbidden"


def build_record(**fields: Any) -> dict[str, Any]:
    """Validate fields against the locked v2 contract and return a copy."""
    actual = set(fields)
    missing = _REQUIRED_FIELDS - actual
    if missing:
        _fail(sorted(missing)[0], "is required")
    unknown = actual - _REQUIRED_FIELDS
    if unknown:
        _fail(sorted(unknown)[0], "is not allowed")

    if fields["schema"] != SCHEMA:
        _fail("schema", f"must equal {SCHEMA!r}")
    _require_type("rev", fields["rev"], int)
    for field in ("run_id", "department", "node", "ts"):
        _require_type(field, fields[field], str)
    _require_type("epoch", fields["epoch"], int)
    _require_type("attempt", fields["attempt"], int)
    _require_type("round", fields["round"], int, allow_none=True)

    _validate_release(fields["release"])
    _validate_trigger(fields["trigger"])

    for field in ("engine", "model"):
        _require_type(field, fields[field], str, allow_none=True)
    _require_type("auth_class", fields["auth_class"], str, allow_none=True)
    if fields["auth_class"] not in _AUTH_CLASSES:
        _fail("auth_class", "must be oauth_cli, service_oauth, local_model, blocked, or None")

    _validate_usage(fields["usage"])
    metered_violation = _validate_cost(fields["cost"])
    _require_type("duration_ms", fields["duration_ms"], int, allow_none=True)

    _require_type("status", fields["status"], str)
    if fields["status"] not in _STATUSES:
        _fail("status", f"must be one of {sorted(_STATUSES)}")
    for field in ("errors", "artifacts", "receipts"):
        _require_type(field, fields[field], list)
    for field in ("evaluator", "approval"):
        _require_type(field, fields[field], dict, allow_none=True)
    _require_type(
        "external_actions_taken", fields["external_actions_taken"], int
    )

    record = copy.deepcopy(fields)
    if metered_violation:
        record["metered_violation"] = True
    try:
        json.dumps(record)
    except (TypeError, ValueError) as exc:
        _fail("record", f"must be JSON serializable ({exc})")
    return record


def validate_record(record: dict[str, Any]) -> dict[str, Any]:
    """Validate a complete record, including its derived violation marker."""
    if not isinstance(record, dict):
        _fail("record", "must be dict")
    unknown = set(record) - _REQUIRED_FIELDS - _DERIVED_FIELDS
    if unknown:
        _fail(sorted(unknown)[0], "is not allowed")

    supplied_violation = record.get("metered_violation")
    base_fields = {key: record[key] for key in record if key in _REQUIRED_FIELDS}
    validated = build_record(**base_fields)
    expected_violation = validated.get("metered_violation")
    if "metered_violation" in record:
        if supplied_violation is not True:
            _fail("metered_violation", "must be True")
        if expected_violation is not True:
            _fail("metered_violation", "is only allowed for cost.lane metered_forbidden")
    elif expected_violation is True:
        _fail("metered_violation", "is required for cost.lane metered_forbidden")
    return validated


def append_record(state_dir: Path, record: dict[str, Any]) -> Path:
    """Validate and append one fsynced JSON line under an exclusive lock."""
    validated = validate_record(record)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "runs-v2.jsonl"
    line = json.dumps(validated, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return path
