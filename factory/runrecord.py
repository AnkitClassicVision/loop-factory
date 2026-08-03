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
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = "run-record/v2"
# Runner-injected spool location (canonical name: kernel/capabilities.py).
# When present, this process is a graph-runner node: appends land in the
# spool and the canonical stream is unreachable — the runner validates,
# stamps identity from its own execution state, signs, and promotes.
RECORD_SPOOL_ENV = "OE_RECORD_SPOOL"

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
# "promotion" is stamped by the runner at promotion time (identity +
# signature) — never by an emitter.
_OPTIONAL_FIELDS = frozenset({"promotion"})
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
    unknown = actual - _REQUIRED_FIELDS - _OPTIONAL_FIELDS
    if unknown:
        _fail(sorted(unknown)[0], "is not allowed")
    if "promotion" in fields:
        _require_type("promotion", fields["promotion"], dict)

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
    unknown = set(record) - _REQUIRED_FIELDS - _DERIVED_FIELDS - _OPTIONAL_FIELDS
    if unknown:
        _fail(sorted(unknown)[0], "is not allowed")

    supplied_violation = record.get("metered_violation")
    base_fields = {
        key: record[key]
        for key in record
        if key in _REQUIRED_FIELDS or key in _OPTIONAL_FIELDS
    }
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
    """Validate and append one fsynced JSON line under an exclusive lock.

    Runner-mediated appends (review B1, Option C): inside a graph-runner
    node process (RECORD_SPOOL_ENV present) the row lands in the per-attempt
    spool — the canonical stream is unreachable from node code through this
    API. The runner later validates, stamps identity from its OWN execution
    state, signs, and promotes; any identity the node wrote is overwritten.
    """
    validated = validate_record(record)
    spool = os.environ.get(RECORD_SPOOL_ENV)
    if spool:
        target_dir = Path(spool)
    else:
        target_dir = Path(state_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "runs-v2.jsonl"
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


def read_release(department_dir: Path) -> dict[str, str] | None:
    """Return the pinned release identity, or ``None`` when it is unavailable."""
    try:
        department_dir = Path(department_dir)
        release_name = (
            (department_dir / "releases" / "current")
            .read_text(encoding="utf-8")
            .strip()
        )
        if not release_name or Path(release_name).name != release_name:
            return None
        manifest_path = (
            department_dir / "releases" / release_name / "manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        release_hash = manifest["hash"]
        source_ref = manifest["source_ref"]
        if not isinstance(release_hash, str) or not isinstance(source_ref, str):
            return None
        return {"hash": release_hash, "source_ref": source_ref}
    except Exception:
        return None


def emit_record(
    state_dir: Path,
    *,
    department: str,
    node: str,
    status: str,
    epoch: int = 0,
    attempt: int = 1,
    round: int | None = None,
    release: dict[str, Any] | None = None,
    trigger: dict[str, Any] | None = None,
    engine: str | None = None,
    model: str | None = None,
    auth_class: str | None = None,
    usage: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    errors: Any = (),
    artifacts: Any = (),
    receipts: Any = (),
    evaluator: dict[str, Any] | None = None,
    approval: dict[str, Any] | None = None,
    external_actions_taken: int = 0,
) -> Path:
    """Build and append one v2 record with generated identity and timestamp.

    Under the graph runner, promotion replaces this locally minted identity
    with the runner's canonical ``new_run_id`` value. Outside the runner this
    emitter remains the identity owner.
    """
    record = build_record(
        schema=SCHEMA,
        rev=2,
        run_id=new_run_id(),
        department=department,
        node=node,
        epoch=epoch,
        ts=datetime.now(timezone.utc).isoformat(),
        attempt=attempt,
        round=round,
        release=release,
        trigger=trigger,
        engine=engine,
        model=model,
        auth_class=auth_class,
        usage=usage,
        cost=cost,
        duration_ms=duration_ms,
        status=status,
        errors=list(errors),
        artifacts=list(artifacts),
        receipts=list(receipts),
        evaluator=evaluator,
        approval=approval,
        external_actions_taken=external_actions_taken,
    )
    return append_record(state_dir, record)


@contextmanager
def timed_emit(
    state_dir: Path,
    department: str,
    node: str,
    **kwargs: Any,
) -> Iterator[None]:
    """Time a block and emit exactly one success or exception record."""
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:
        fields = dict(kwargs)
        errors = list(fields.pop("errors", ()))
        errors.append(type(exc).__name__)
        fields["status"] = "error"
        fields["errors"] = errors
        fields["duration_ms"] = int((time.perf_counter() - started) * 1000)
        emit_record(
            state_dir,
            department=department,
            node=node,
            **fields,
        )
        raise
    else:
        fields = dict(kwargs)
        fields.setdefault("status", "ok")
        fields["duration_ms"] = int((time.perf_counter() - started) * 1000)
        emit_record(
            state_dir,
            department=department,
            node=node,
            **fields,
        )
