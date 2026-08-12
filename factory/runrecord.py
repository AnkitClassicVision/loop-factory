"""Build, validate, and durably append factory run records.

The v2 record deliberately excludes prompts and message bodies. Records are
local JSON lines, serialized under an advisory file lock so concurrent writers
cannot interleave their output.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import stat
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, NoReturn

from kernel import receipts
from factory import node_contract


SCHEMA = "run-record/v2"
# Runner-injected spool location (canonical name: kernel/capabilities.py).
# When present, this process is a graph-runner node: appends land in the
# spool and the canonical stream is unreachable — the runner validates,
# stamps identity from its own execution state, signs, and promotes.
RECORD_SPOOL_ENV = "OE_RECORD_SPOOL"
SPOOL_SCHEMA = "factory-record-spool/v1"
SPOOL_MARKER = "factory-spool.json"
_SPOOL_MARKER_FIELDS = frozenset(
    {"schema", "run_id", "department", "release", "trigger", "state_dir", "signature"}
)

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
_OPTIONAL_FIELDS = frozenset({
    "promotion", "block", "node_contract", "contract_sha256",
    "work_object_ref", "qa_receipt_ref",
})
_AUTH_CLASSES = frozenset(
    {"oauth_cli", "service_oauth", "local_model", "blocked", None}
)
_STATUSES = frozenset(
    {"ok", "blocked", "unknown", "hold", "error", "halted", "killed", "escalated", "skipped"}
)
_TRIGGER_KINDS = frozenset({"time", "event", "goal", "manual", "escalation"})
_COST_LANES = frozenset({"flat_subscription", "metered_forbidden"})


class RecordCustodyRefused(RuntimeError):
    """A node tried to write a record without Factory custody."""


def new_run_id() -> str:
    """Return a time-sortable UTC run identifier with random uniqueness."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{os.urandom(4).hex()}"


def _fail(field: str, detail: str) -> NoReturn:
    raise ValueError(f"invalid field {field}: {detail}")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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


def _validate_block(value: Any) -> None:
    if not isinstance(value, dict):
        _fail("block", "must be a mapping")
    _require_shape("block", value, {"owner", "deadline", "next_action"})
    for field in ("owner", "deadline", "next_action"):
        _require_type(f"block.{field}", value[field], str)
        if not value[field].strip():
            _fail(f"block.{field}", "must be nonempty")
    try:
        parsed = datetime.fromisoformat(value["deadline"].replace("Z", "+00:00"))
    except ValueError:
        _fail("block.deadline", "must be an ISO 8601 timestamp")
        return
    if parsed.tzinfo is None:
        _fail("block.deadline", "must include a timezone")


def _validate_node_binding(fields: dict[str, Any]) -> None:
    binding_fields = {"node_contract", "contract_sha256", "work_object_ref", "qa_receipt_ref"}
    present = binding_fields & set(fields)
    if not present:
        return
    if present != binding_fields:
        _fail("node_contract", "identity, digest, work object, and QA evidence are all required")
    identity = fields["node_contract"]
    if not isinstance(identity, dict) or set(identity) != {"department", "subgraph", "node_id", "impl"}:
        _fail("node_contract", "must be the exact composite identity")
    for key in identity:
        _require_type(f"node_contract.{key}", identity[key], str)
        if not identity[key].strip():
            _fail(f"node_contract.{key}", "must be nonempty")
    digest = fields["contract_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        _fail("contract_sha256", "must be a lowercase SHA-256 digest")
    work = fields["work_object_ref"]
    if not isinstance(work, dict) or set(work) != {"name", "consumer", "artifact_or_field"}:
        _fail("work_object_ref", "must match the declared work object")
    if not all(_nonempty(work[key]) for key in work):
        _fail("work_object_ref", "must be nonempty")
    qa = fields["qa_receipt_ref"]
    if not isinstance(qa, dict) or set(qa) != {"verifier", "evidence"}:
        _fail("qa_receipt_ref", "must name the declared QA evidence")
    if not all(_nonempty(qa[key]) for key in qa):
        _fail("qa_receipt_ref", "must be nonempty")


def _seal_receipts(state_dir: Path, receipts: Any) -> list[dict[str, Any]]:
    root = Path(state_dir).resolve()
    sealed: list[dict[str, Any]] = []
    for index, raw in enumerate(receipts):
        if isinstance(raw, str):
            raw = {"path": raw}
        if not isinstance(raw, dict):
            _fail(f"receipts[{index}]", "must be a mapping")
        raw_path = raw.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            _fail(f"receipts[{index}].path", "must be a nonempty string")
        try:
            candidate = Path(str(raw_path))
            candidate = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
            relative = candidate.relative_to(root)
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except (OSError, ValueError):
            _fail(f"receipts[{index}].path", "must be a readable state-local path")
        receipt = dict(raw)
        receipt["schema"] = "file-sha256/v1"
        receipt["path"] = relative.as_posix()
        receipt["sha256"] = digest
        sealed.append(receipt)
    return sealed


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
    _validate_node_binding(fields)

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
    if "block" in fields:
        _validate_block(fields["block"])
    for field in ("errors", "artifacts", "receipts"):
        _require_type(field, fields[field], list)
    for field in ("evaluator", "approval"):
        _require_type(field, fields[field], dict, allow_none=True)
    _require_type(
        "external_actions_taken", fields["external_actions_taken"], int
    )
    if fields["external_actions_taken"] < 0:
        _fail("external_actions_taken", "must be nonnegative")

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


def _spool_binding(spool: Path) -> dict[str, Any]:
    spool = Path(spool)
    try:
        info = spool.lstat()
        marker_info = (spool / SPOOL_MARKER).lstat()
        if not stat.S_ISDIR(info.st_mode) or not stat.S_ISREG(marker_info.st_mode):
            raise RecordCustodyRefused("factory_record_spool_invalid")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise RecordCustodyRefused("factory_record_spool_not_private")
        marker = json.loads((spool / SPOOL_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RecordCustodyRefused("factory_record_spool_unreadable") from exc
    if not isinstance(marker, dict) or set(marker) != _SPOOL_MARKER_FIELDS:
        raise RecordCustodyRefused("factory_record_spool_marker_invalid")
    if marker["schema"] != SPOOL_SCHEMA or not all(
        isinstance(marker[field], str) and marker[field].strip()
        for field in ("run_id", "department", "trigger", "state_dir")
    ):
        raise RecordCustodyRefused("factory_record_spool_marker_invalid")
    if marker["release"] is not None and not isinstance(marker["release"], dict):
        raise RecordCustodyRefused("factory_record_spool_marker_invalid")
    if not isinstance(marker["signature"], str) or not marker["signature"].strip():
        raise RecordCustodyRefused("factory_record_spool_marker_invalid")
    return marker


def _marker_payload(marker: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in marker.items() if key != "signature"}
    return receipts.canonical_payload(unsigned)


def _factory_signer() -> receipts.LocalSigner:
    try:
        return receipts.LocalSigner()
    except Exception as exc:
        raise RecordCustodyRefused("factory_record_signing_unavailable") from exc


def write_spool_marker(
    spool: Path,
    *,
    run_id: str,
    department: str,
    release: dict[str, Any] | None,
    trigger: str,
    state_dir: Path,
) -> Path:
    """Create a private, Factory-signed binding for one run spool."""
    spool = Path(spool)
    try:
        spool.mkdir(parents=True, exist_ok=False, mode=0o700)
    except FileExistsError:
        if not spool.is_dir() or any(spool.iterdir()):
            raise RecordCustodyRefused("factory_record_spool_not_empty")
    os.chmod(spool, 0o700)
    marker = {
        "schema": SPOOL_SCHEMA,
        "run_id": run_id,
        "department": department,
        "release": copy.deepcopy(release),
        "trigger": trigger,
        "state_dir": str(Path(state_dir).resolve()),
    }
    marker["signature"] = _factory_signer().sign(_marker_payload(marker))
    path = spool / SPOOL_MARKER
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(marker, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if fd >= 0:
            os.close(fd)
    return path


def assert_factory_spool(
    spool: Path,
    *,
    run_id: str,
    department: str,
    state_dir: Path,
    trigger: str = "daily",
) -> dict[str, Any]:
    """Check the context handed to a daily entrypoint before it runs."""
    marker = _spool_binding(Path(spool))
    if (
        marker["run_id"] != run_id
        or marker["department"] != department
        or marker["trigger"] != trigger
        or marker["state_dir"] != str(Path(state_dir).resolve())
    ):
        raise RecordCustodyRefused("factory_record_spool_binding_mismatch")
    return marker


def verify_factory_spool(
    spool: Path,
    *,
    run_id: str,
    department: str,
    state_dir: Path,
    trigger: str = "daily",
) -> dict[str, Any]:
    """Verify a Factory-issued marker. Only Factory promotion calls this."""
    marker = assert_factory_spool(
        spool,
        run_id=run_id,
        department=department,
        state_dir=state_dir,
        trigger=trigger,
    )
    try:
        valid = _factory_signer().verify(_marker_payload(marker), marker["signature"])
    except Exception as exc:
        raise RecordCustodyRefused("factory_record_spool_signature_invalid") from exc
    if not valid:
        raise RecordCustodyRefused("factory_record_spool_signature_invalid")
    return marker


def append_record(state_dir: Path, record: dict[str, Any]) -> Path:
    """Validate and append one fsynced JSON line under an exclusive lock.

    Runner-mediated appends (review B1, Option C): inside a graph-runner
    node process (RECORD_SPOOL_ENV present) the row lands in the per-attempt
    spool — the canonical stream is unreachable from node code through this
    API. The runner later validates, stamps identity from its OWN execution
    state, signs, and promotes; any identity the node wrote is overwritten.
    """
    spool_value = os.environ.get(RECORD_SPOOL_ENV)
    if not spool_value:
        raise RecordCustodyRefused("factory_record_spool_required")
    target_dir = Path(spool_value)
    binding = _spool_binding(target_dir)
    if target_dir.resolve() == Path(state_dir).resolve():
        raise RecordCustodyRefused("factory_record_spool_is_canonical_state")
    validated = validate_record(record)
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


def _append_canonical_records(state_dir: Path, records: list[dict[str, Any]]) -> Path:
    """Append already-promoted records; only Factory promotion calls this."""
    target_dir = Path(state_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "runs-v2.jsonl"
    lines = []
    for record in records:
        lines.append(
            json.dumps(validate_record(record), sort_keys=True, separators=(",", ":"))
            + "\n"
        )
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.writelines(lines)
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
    run_id: str | None = None,
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
    block: dict[str, Any] | None = None,
    external_actions_taken: int = 0,
    contract_subgraph: str | None = None,
    contract_node_id: str | None = None,
    contract_impl: str | None = None,
) -> Path:
    """Build and append one v2 record with generated identity and timestamp.

    ``LOOP_FACTORY_RUN_ID`` is how the daily chain binds all node records to
    one minted run. Under the graph runner, promotion replaces this identity
    with the runner's canonical ``new_run_id`` value. Outside the runner this
    emitter remains the identity owner.
    """
    binding: dict[str, Any] | None = None
    contract_root = Path(state_dir).parent
    if (contract_root / node_contract.CONTRACT_FILE).is_file():
        if contract_subgraph is not None or contract_node_id is not None or contract_impl is not None:
            if not all(isinstance(value, str) and value.strip() for value in (contract_subgraph, contract_node_id, contract_impl)):
                raise ValueError("node_contract_identity_incomplete")
            declared = node_contract.lookup(
                contract_root,
                subgraph=contract_subgraph,
                node_id=contract_node_id,
                impl=contract_impl,
            )
        else:
            declared = node_contract.lookup_by_node(contract_root, node)
        binding = {
            "node_contract": {
                key: declared[key] for key in ("department", "subgraph", "node_id", "impl")
            },
            "contract_sha256": node_contract.load(contract_root)["contract_sha256"],
            "work_object_ref": dict(declared["work_object"]),
            "qa_receipt_ref": dict(declared["qa"]),
        }
    fields: dict[str, Any] = {
        "schema": SCHEMA,
        "rev": 2,
        "run_id": run_id or os.environ.get("LOOP_FACTORY_RUN_ID") or new_run_id(),
        "department": department,
        "node": node,
        "epoch": epoch,
        "ts": datetime.now(timezone.utc).isoformat(),
        "attempt": attempt,
        "round": round,
        "release": release,
        "trigger": trigger,
        "engine": engine,
        "model": model,
        "auth_class": auth_class,
        "usage": usage,
        "cost": cost,
        "duration_ms": duration_ms,
        "status": status,
        "errors": list(errors),
        "artifacts": list(artifacts),
        "receipts": _seal_receipts(Path(state_dir), receipts),
        "evaluator": evaluator,
        "approval": approval,
        "external_actions_taken": external_actions_taken,
    }
    if binding is not None:
        fields.update(binding)
    if block is not None:
        fields["block"] = block
    record = build_record(**fields)
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
