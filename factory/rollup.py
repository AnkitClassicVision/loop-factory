"""Deterministic SQLite rollup and sanitized per-entity NDJSON export."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable

from factory import scores as score_records


LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = "rollup/v2"  # v2: signed promotion + canonical run joins
ENTITIES = (
    "department",
    "run",
    "step_telemetry",
    "receipt",
    "score",
    "incident",
    "approval",
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@?=&%#+-]{0,255}\Z")
_FINISH_REASONS = frozenset(
    {"stop", "length", "tool_calls", "content_filter", "error", "other"}
)
_AUTH_ROUTES = frozenset(
    {"oauth_cli", "service_oauth", "local_model", "vault_api_key", "blocked"}
)

DDL = """
CREATE TABLE department (
  id TEXT PRIMARY KEY, epoch INTEGER, status TEXT, last_cycle_at TEXT,
  ok INTEGER, source_ref TEXT, schema_version TEXT NOT NULL
);
CREATE TABLE run (
  id TEXT PRIMARY KEY, department TEXT NOT NULL, run_id TEXT NOT NULL,
  current_step TEXT, status TEXT, ts TEXT, epoch INTEGER, source_ref TEXT,
  schema_version TEXT NOT NULL
);
CREATE TABLE step_telemetry (
  id TEXT PRIMARY KEY, department TEXT, run_id TEXT, step_id TEXT, node TEXT,
  ts TEXT, operation_name TEXT, provider_name TEXT, request_model TEXT,
  response_model TEXT, input_tokens INTEGER, output_tokens INTEGER,
  finish_reasons_json TEXT, duration_ms INTEGER, error_type TEXT,
  cost_usd REAL, auth_route TEXT, engine TEXT, estimated INTEGER NOT NULL,
  price_schema_version TEXT, price_effective_date TEXT, telemetry_source TEXT,
  source_ref TEXT, schema_version TEXT NOT NULL
);
CREATE TABLE receipt (
  id TEXT PRIMARY KEY, department TEXT, run_id TEXT, step_id TEXT, node TEXT,
  receipt_type TEXT, status TEXT, ts TEXT, verified INTEGER, source_ref TEXT,
  schema_version TEXT NOT NULL
);
CREATE TABLE score (
  id TEXT PRIMARY KEY, department TEXT NOT NULL, run_id TEXT, step_id TEXT,
  node TEXT NOT NULL, name TEXT NOT NULL, value REAL NOT NULL, label TEXT NOT NULL,
  explanation TEXT NOT NULL, source TEXT NOT NULL, judge_model TEXT,
  config_version TEXT NOT NULL, ts TEXT NOT NULL, source_ref TEXT,
  schema_version TEXT NOT NULL
);
CREATE TABLE incident (
  id TEXT PRIMARY KEY, department TEXT, code TEXT NOT NULL, severity TEXT,
  status TEXT NOT NULL, ts TEXT, source_ref TEXT, schema_version TEXT NOT NULL
);
CREATE TABLE approval (
  id TEXT PRIMARY KEY, department TEXT, decision_id TEXT, status TEXT NOT NULL,
  queued_at TEXT, card_ref TEXT, source_ref TEXT, schema_version TEXT NOT NULL
);
CREATE INDEX run_by_status_ts ON run(status, ts);
CREATE INDEX run_by_run_id ON run(run_id);
CREATE INDEX telemetry_by_department_ts ON step_telemetry(department, ts);
CREATE INDEX telemetry_by_run_id ON step_telemetry(run_id);
CREATE INDEX score_by_department_label ON score(department, label);
CREATE INDEX score_by_run_id ON score(run_id);
CREATE INDEX incident_by_status ON incident(status);
CREATE INDEX approval_by_status_age ON approval(status, queued_at);
"""


def _stable_id(*parts: Any) -> str:
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _resolve_verifier():
    """The kernel signer when its key is present in-process, else None."""
    if not os.environ.get("OE_KERNEL_SIGNING_KEY"):
        return None
    from kernel import receipts

    return receipts.LocalSigner()


def _promotion_verdict(row: dict[str, Any], signer) -> str:
    """Read-time defense (review B1, Option C): only the runner's promotion
    step may put graph identity into a canonical stream, and it signs every
    promoted row. A graph-claiming row without a valid signature is a
    same-uid direct file write (or tampering) and is quarantined."""
    promotion = row.get("promotion")
    if not isinstance(promotion, dict) or not isinstance(
            promotion.get("sig"), str):
        return "unsigned"
    if signer is None:
        return "unverifiable"
    unsigned = {**row, "promotion": {k: v for k, v in promotion.items()
                                     if k != "sig"}}
    try:
        payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
        verified = signer.verify(payload, promotion["sig"])
    except Exception:
        return "invalid"
    return "ok" if verified else "invalid"


def _promotion_id(row: dict[str, Any]) -> str | None:
    promotion = row.get("promotion")
    if isinstance(promotion, dict) and isinstance(promotion.get("id"), str):
        return promotion["id"]
    return None


def _body_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":"),
                   default=str).encode("utf-8")
    ).hexdigest()


def _collapse_promotions(rows, incidents):
    """Exactly-once for promotion-backed rows (review F3).

    Rows sharing a promotion id must be byte-identical — that is what a
    crash-recovered promotion re-appending the SAME spool produces. Two
    different bodies under one id is a conflict, never last-writer-wins:
    the whole group is quarantined as an incident.
    """
    grouped: dict[str, list] = {}
    passthrough = []
    for row in rows:
        marker = row.pop("_promotion_body", None)
        if marker is None:
            passthrough.append(row)
        else:
            grouped.setdefault(row["id"], []).append((marker, row))
    for row_id, group in grouped.items():
        bodies = {marker for marker, _row in group}
        if len(bodies) > 1:
            first = group[0][1]
            incidents.append(
                _incident(
                    department=first.get("department"),
                    code="graph_identity_conflict",
                    source_ref=str(first.get("source_ref")),
                )
            )
            continue
        passthrough.append(group[0][1])
    return passthrough


def _projection_runs(root, state_dir, department, incidents, signer):
    """Graph runs backed by the department's VERIFIED signed projection.

    Review F2: runs.jsonl is a plain append-only event log — anything that
    can write the file can claim a graph run in it. The signed execution
    projection (factory/projection.py, exported by the runner) is the
    authority instead: graph-run rows are DERIVED from it, and a runs.jsonl
    graph claim without projection backing is quarantined. Deny-by-default —
    a missing, unreadable, unverifiable, or tampered projection backs
    nothing at all.

    Known bound, stated honestly: a department whose projection covers one
    subgraph will not back graph claims from a second subgraph's runs; those
    surface as graph_identity_unbacked incidents (visible and fail-closed)
    rather than being silently trusted.
    """
    path = state_dir / "receipts" / "execution-projection.json"
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise OSError("projection is not a regular file")
    except (FileNotFoundError, OSError):
        return {}
    value = _read_json(root, path, department, incidents)
    if value is None:
        return {}
    from factory import projection as projection_module

    if signer is None:
        findings = [{"kind": "no_verifier"}]
    else:
        findings = projection_module.verify_projection(value, signer)
    if findings:
        incidents.append(
            _incident(
                department=department,
                code="graph_identity_projection_invalid",
                source_ref=_relative(root, path),
            )
        )
        return {}
    backed = {}
    for run in value.get("runs") or []:
        if not isinstance(run, dict):
            continue
        run_id = run.get("run_id")
        if isinstance(run_id, str) and run_id:
            backed[run_id] = run
    return backed


def _projection_run_rows(root, state_dir, department, backed):
    """Derive the canonical graph-run rows from the verified projection."""
    source_ref = _relative(root, state_dir / "receipts"
                           / "execution-projection.json")
    out = []
    for run_id, run in sorted(backed.items()):
        transitions = run.get("transitions") or []
        last = transitions[-1] if isinstance(transitions, list) and transitions \
            else {}
        try:
            safe_run_id = _identifier(run_id, "run_id", nullable=False)
            status = _identifier(run.get("state") or "unknown", "status",
                                 nullable=False)
            ts = _identifier(last.get("ts") if isinstance(last, dict) else None,
                             "timestamp")
            current_step = _identifier(
                last.get("from") if isinstance(last, dict) else None, "node")
        except ValueError:
            continue
        out.append({
            "id": _stable_id("run", department, safe_run_id),
            "department": department,
            "run_id": safe_run_id,
            "current_step": current_step,
            "status": status,
            "ts": ts,
            "epoch": None,
            "source_ref": source_ref,
            "schema_version": SCHEMA_VERSION,
        })
    return out


def _relative(root: Path, path: Path) -> str:
    try:
        return path.absolute().relative_to(root.absolute()).as_posix()
    except ValueError:
        return path.name


def _identifier(value: Any, field: str, *, nullable: bool = True) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} is not a safe identifier")
    return value


def _number(
    value: Any, field: str, *, integer: bool = False, nullable: bool = True
) -> int | float | None:
    if value is None and nullable:
        return None
    expected = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected):
        raise ValueError(f"{field} has an invalid type")
    if value < 0 or (not integer and not math.isfinite(value)):
        raise ValueError(f"{field} must be finite and non-negative")
    return value


def _open_source(root: Path, path: Path):
    """Open a source beneath root without following any symlink component."""
    relative = path.absolute().relative_to(root.absolute())
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(root.absolute(), flags)
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        source_fd = os.open(
            relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
        )
        return os.fdopen(source_fd, encoding="utf-8")
    finally:
        os.close(directory_fd)


def _incident(
    *, department: str | None, code: str, source_ref: str, ts: str | None = None
) -> dict[str, Any]:
    return {
        "id": _stable_id("incident", department, code, source_ref),
        "department": department,
        "code": code,
        "severity": "breach",
        "status": "open",
        "ts": ts,
        "source_ref": source_ref,
        "schema_version": SCHEMA_VERSION,
    }


def _source_available(
    root: Path,
    path: Path,
    department: str | None,
    incidents: list[dict[str, Any]],
    *,
    required: bool = False,
) -> bool:
    """Distinguish absent optional inputs from unsafe or missing required inputs."""
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        if required:
            incidents.append(
                _incident(
                    department=department,
                    code="source_missing",
                    source_ref=_relative(root, path),
                )
            )
        return False
    if not stat.S_ISREG(stat_result.st_mode):
        incidents.append(
            _incident(
                department=department,
                code="source_unreadable:UnsafeFileType",
                source_ref=_relative(root, path),
            )
        )
        return False
    return stat_result.st_size >= 0


def _read_jsonl(
    root: Path, path: Path, department: str | None, incidents: list[dict[str, Any]]
) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    source_ref = _relative(root, path)
    try:
        with _open_source(root, path) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise TypeError("row is not an object")
                        rows.append((line_number, row))
                    except (json.JSONDecodeError, TypeError, ValueError) as exc:
                        incidents.append(
                            _incident(
                                department=department,
                                code=f"source_unreadable:{type(exc).__name__}",
                                source_ref=f"{source_ref}:{line_number}",
                            )
                        )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (OSError, UnicodeError) as exc:
        incidents.append(
            _incident(
                department=department,
                code=f"source_unreadable:{type(exc).__name__}",
                source_ref=source_ref,
            )
        )
    return rows


def _read_json(
    root: Path, path: Path, department: str | None, incidents: list[dict[str, Any]]
) -> dict[str, Any] | None:
    try:
        with _open_source(root, path) as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise TypeError("source is not an object")
        return value
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        incidents.append(
            _incident(
                department=department,
                code=f"source_unreadable:{type(exc).__name__}",
                source_ref=_relative(root, path),
            )
        )
        return None


def _department_row(root: Path, department: str, state_dir: Path, state: dict | None):
    state = state or {}
    status = state.get("status") or ("ok" if state.get("ok") is True else "unknown")
    try:
        status = _identifier(status, "department status", nullable=False)
        last_cycle_at = _identifier(
            state.get("last_cycle_at") or state.get("ts"), "last_cycle_at"
        )
    except ValueError:
        status = "invalid"
        last_cycle_at = None
    return {
        "id": department,
        "epoch": state.get("epoch") if isinstance(state.get("epoch"), int) else None,
        "status": status,
        "last_cycle_at": last_cycle_at,
        "ok": None if state.get("ok") is None else int(bool(state.get("ok"))),
        "source_ref": _relative(root, state_dir),
        "schema_version": SCHEMA_VERSION,
    }


def _run_rows(
    root: Path, path: Path, department: str, incidents: list[dict[str, Any]],
    signer=None, backed: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    promoted: list[dict[str, Any]] = []
    source_ref = _relative(root, path)
    # Two different integrity stories per stream:
    #  * promoted runs-v2 rows carry the runner's canonical run_id and require
    #    a valid promotion signature. An unsigned row colliding with a signed
    #    projection run is quarantined.
    #  * runs.jsonl graph events are never the source of a graph-run row. The
    #    verified projection is authoritative; an unbacked graph event is an
    #    incident, while a backed one is skipped in favor of its projection.
    promoted_stream = path.name == "runs-v2.jsonl"
    for line_number, row in _read_jsonl(root, path, department, incidents):
        run_id = row.get("run_id")
        promotion_id = _promotion_id(row)
        if promoted_stream and promotion_id is not None:
            verdict = _promotion_verdict(row, signer)
            if verdict != "ok":
                incidents.append(
                    _incident(
                        department=department,
                        code=f"graph_identity_{verdict}",
                        source_ref=f"{source_ref}:{line_number}",
                    )
                )
                continue
        elif (promoted_stream and isinstance(run_id, str)
              and run_id in (backed or {})):
            incidents.append(
                _incident(
                    department=department,
                    code="graph_identity_unsigned",
                    source_ref=f"{source_ref}:{line_number}",
                )
            )
            continue
        elif (not promoted_stream and isinstance(row.get("loop_id"), str)
              and isinstance(run_id, str)):
            if run_id not in (backed or {}):
                incidents.append(
                    _incident(
                        department=department,
                        code="graph_identity_unbacked",
                        source_ref=f"{source_ref}:{line_number}",
                    )
                )
            # Backed or not, the projection is the source of graph-run rows.
            continue
        if not isinstance(run_id, str) or not run_id:
            run_id = _stable_id(department, source_ref, line_number)
        node = row.get("node")
        ts = row.get("ts") or row.get("timestamp") or row.get("queued_at")
        status = row.get("status")
        if status is None:
            status = "active" if node and node != "manager_tick" else "unknown"
        try:
            run_id = _identifier(run_id, "run_id", nullable=False)
            node = _identifier(node, "node")
            ts = _identifier(ts, "timestamp")
            status = _identifier(status, "status", nullable=False)
        except ValueError as exc:
            incidents.append(
                _incident(
                    department=department,
                    code=f"run_invalid:{type(exc).__name__}",
                    source_ref=f"{source_ref}:{line_number}",
                )
            )
            continue
        candidate = {
            # the stable promotion id collapses re-appended duplicates from
            # a crash between canonical append and promotion marker
            "id": (_stable_id("run", department, "promotion", promotion_id)
                   if promotion_id else _stable_id("run", department, run_id)),
            **({"_promotion_body": _body_hash(row)} if promotion_id else {}),
            "department": department,
            "run_id": run_id,
            "current_step": node,
            "status": status,
            "ts": ts,
            "epoch": row.get("epoch") if isinstance(row.get("epoch"), int) else None,
            "source_ref": f"{source_ref}:{line_number}",
            "schema_version": SCHEMA_VERSION,
        }
        if promotion_id is not None:
            # promoted rows are never collapsed here: two bodies under one
            # promotion id is a CONFLICT for _collapse_promotions to
            # quarantine, not a latest-writer-wins race (review F3)
            promoted.append(candidate)
            continue
        prior = latest.get(run_id)
        if prior is None or ((candidate["ts"] or ""), line_number) >= (
            (prior["ts"] or ""), prior.get("_line", -1)
        ):
            candidate["_line"] = line_number
            latest[run_id] = candidate
    for row in latest.values():
        row.pop("_line", None)
    return sorted(list(latest.values()) + promoted, key=lambda row: row["id"])


def validate_telemetry_row(row: dict[str, Any], department: str | None = None):
    """Full step-telemetry validation, raising ValueError on the first fault.

    Shared definition (review F4): factory/runner.py runs this over every
    spooled telemetry row BEFORE signing and promotion, so an invalid row can
    never be signed and then rejected only here at read time.
    """
    reasons = row.get("gen_ai.response.finish_reasons")
    if reasons is not None and (
        not isinstance(reasons, list)
        or len(reasons) > 8
        or any(reason not in _FINISH_REASONS for reason in reasons)
    ):
        raise ValueError("finish reasons are invalid")
    if row.get("schema_version") != "step-telemetry/v1":
        raise ValueError("telemetry schema_version is invalid")
    if row.get("loopfactory.auth.route") not in _AUTH_ROUTES:
        raise ValueError("auth route is invalid")
    if not isinstance(row.get("estimated"), bool):
        raise ValueError("estimated must be boolean")
    return {
        "ts": _identifier(row.get("ts"), "timestamp", nullable=False),
        "department": _identifier(
            row.get("loopfactory.department") or department,
            "department",
            nullable=False,
        ),
        "run_id": _identifier(row.get("loopfactory.run_id"), "run_id"),
        "step_id": _identifier(row.get("loopfactory.step_id"), "step_id"),
        "node": _identifier(row.get("loopfactory.node"), "node"),
        "operation_name": _identifier(row.get("gen_ai.operation.name"), "operation_name"),
        "provider_name": _identifier(row.get("gen_ai.provider.name"), "provider_name"),
        "request_model": _identifier(row.get("gen_ai.request.model"), "request_model"),
        "response_model": _identifier(row.get("gen_ai.response.model"), "response_model"),
        "error_type": _identifier(row.get("error.type"), "error_type"),
        "auth_route": _identifier(row.get("loopfactory.auth.route"), "auth_route"),
        "engine": _identifier(row.get("loopfactory.engine"), "engine"),
        "price_schema_version": _identifier(
            row.get("loopfactory.price.schema_version"), "price_schema_version"
        ),
        "price_effective_date": _identifier(
            row.get("loopfactory.price.effective_date"), "price_effective_date"
        ),
        "telemetry_source": _identifier(
            row.get("loopfactory.telemetry.source"), "telemetry_source"
        ),
        "input_tokens": _number(
            row.get("gen_ai.usage.input_tokens"), "input_tokens", integer=True
        ),
        "output_tokens": _number(
            row.get("gen_ai.usage.output_tokens"), "output_tokens", integer=True
        ),
        "duration_ms": _number(
            row.get("duration_ms"), "duration_ms", integer=True, nullable=False
        ),
        "cost_usd": _number(row.get("loopfactory.cost_usd"), "cost_usd"),
    }


def _telemetry_rows(root, path, department, incidents, signer=None, backed=None):
    out = []
    source_ref = _relative(root, path)
    for line_number, row in _read_jsonl(root, path, department, incidents):
        promotion_id = _promotion_id(row)
        claimed_run_id = row.get("loopfactory.run_id")
        if promotion_id is not None:
            verdict = _promotion_verdict(row, signer)
            if verdict != "ok":
                incidents.append(
                    _incident(
                        department=department,
                        code=f"graph_identity_{verdict}",
                        source_ref=f"{source_ref}:{line_number}",
                    )
                )
                continue
        elif (isinstance(claimed_run_id, str)
              and claimed_run_id in (backed or {})):
            incidents.append(
                _incident(
                    department=department,
                    code="graph_identity_unsigned",
                    source_ref=f"{source_ref}:{line_number}",
                )
            )
            continue
        reasons = row.get("gen_ai.response.finish_reasons")
        try:
            safe_values = validate_telemetry_row(row, department)
        except ValueError as exc:
            incidents.append(
                _incident(
                    department=department,
                    code=f"telemetry_invalid:{type(exc).__name__}",
                    source_ref=f"{source_ref}:{line_number}",
                )
            )
            continue
        out.append(
            {
                "id": (_stable_id("telemetry", department, "promotion",
                                  promotion_id)
                       if promotion_id
                       else _stable_id("telemetry", department, source_ref,
                                       line_number)),
                **({"_promotion_body": _body_hash(row)} if promotion_id else {}),
                "department": safe_values["department"],
                "run_id": safe_values["run_id"],
                "step_id": safe_values["step_id"],
                "node": safe_values["node"],
                "ts": safe_values["ts"],
                "operation_name": safe_values["operation_name"],
                "provider_name": safe_values["provider_name"],
                "request_model": safe_values["request_model"],
                "response_model": safe_values["response_model"],
                "input_tokens": safe_values["input_tokens"],
                "output_tokens": safe_values["output_tokens"],
                "finish_reasons_json": json.dumps(reasons, separators=(",", ":")) if reasons is not None else None,
                "duration_ms": safe_values["duration_ms"],
                "error_type": safe_values["error_type"],
                "cost_usd": safe_values["cost_usd"],
                "auth_route": safe_values["auth_route"],
                "engine": safe_values["engine"],
                "estimated": int(row.get("estimated") is True),
                "price_schema_version": safe_values["price_schema_version"],
                "price_effective_date": safe_values["price_effective_date"],
                "telemetry_source": safe_values["telemetry_source"],
                "source_ref": f"{source_ref}:{line_number}",
                "schema_version": row["schema_version"],
            }
        )
    return out


def _score_rows(root, path, department, incidents, signer=None, backed=None):
    out = []
    source_ref = _relative(root, path)
    for line_number, row in _read_jsonl(root, path, department, incidents):
        target = row.get("target_ref")
        claimed = target.get("run_id") if isinstance(target, dict) else None
        promotion_id = _promotion_id(row)
        if promotion_id is not None:
            verdict = _promotion_verdict(row, signer)
            if verdict != "ok":
                incidents.append(
                    _incident(
                        department=department,
                        code=f"graph_identity_{verdict}",
                        source_ref=f"{source_ref}:{line_number}",
                    )
                )
                continue
        elif isinstance(claimed, str) and claimed in (backed or {}):
            incidents.append(
                _incident(
                    department=department,
                    code="graph_identity_unsigned",
                    source_ref=f"{source_ref}:{line_number}",
                )
            )
            continue
        try:
            row = score_records.validate_score(row)
        except ValueError as exc:
            incidents.append(
                _incident(
                    department=department,
                    code=f"score_invalid:{type(exc).__name__}",
                    source_ref=f"{source_ref}:{line_number}",
                )
            )
            continue
        target = row["target_ref"]
        value = row["gen_ai.evaluation.score.value"]
        out.append(
            {
                "id": (_stable_id("score", department, "promotion",
                                  promotion_id)
                       if promotion_id
                       else _stable_id("score", department, source_ref,
                                       line_number)),
                **({"_promotion_body": _body_hash(row)} if promotion_id else {}),
                "department": target.get("department") or department,
                "run_id": target.get("run_id"),
                "step_id": target.get("step_id"),
                "node": target.get("node") or "unknown",
                "name": row.get("gen_ai.evaluation.name") or "unknown",
                "value": value,
                "label": row.get("gen_ai.evaluation.score.label") or "unknown",
                "explanation": row.get("gen_ai.evaluation.explanation") or "",
                "source": row.get("source") or "unknown",
                "judge_model": row.get("judge_model"),
                "config_version": row.get("config_version") or "unknown",
                "ts": row.get("ts") or "unknown",
                "source_ref": f"{source_ref}:{line_number}",
                "schema_version": row.get("schema_version") or SCHEMA_VERSION,
            }
        )
    return out


def _approval_rows(root, path, department, incidents):
    out = []
    source_ref = _relative(root, path)
    for line_number, row in _read_jsonl(root, path, department, incidents):
        decision_id = row.get("decision_id")
        packet = row.get("packet") if isinstance(row.get("packet"), dict) else {}
        status = row.get("status") or ("pending_approval" if decision_id else "pending")
        queued_at = row.get("queued_at") or row.get("ts")
        card_ref = row.get("card_ref") or packet.get("card_ref")
        try:
            safe_department = _identifier(
                row.get("department") or department, "department", nullable=False
            )
            decision_id = _identifier(decision_id, "decision_id")
            status = _identifier(status, "status", nullable=False)
            queued_at = _identifier(queued_at, "queued_at")
            card_ref = _identifier(card_ref, "card_ref")
        except ValueError as exc:
            incidents.append(
                _incident(
                    department=department,
                    code=f"approval_invalid:{type(exc).__name__}",
                    source_ref=f"{source_ref}:{line_number}",
                )
            )
            continue
        out.append(
            {
                "id": _stable_id("approval", department, decision_id, source_ref, line_number),
                "department": safe_department,
                "decision_id": decision_id,
                "status": status,
                "queued_at": queued_at,
                "card_ref": card_ref,
                "source_ref": f"{source_ref}:{line_number}",
                "schema_version": SCHEMA_VERSION,
            }
        )
    return out


def _incident_rows(root, path, department, incidents):
    out = []
    source_ref = _relative(root, path)
    for line_number, row in _read_jsonl(root, path, department, incidents):
        code = row.get("code") or row.get("issue") or row.get("kind") or "incident"
        try:
            safe_department = _identifier(
                row.get("department") or department, "department", nullable=False
            )
            code = _identifier(code, "incident code", nullable=False)
            severity = _identifier(row.get("severity"), "severity")
            status = _identifier(row.get("status") or "open", "status", nullable=False)
            timestamp = _identifier(row.get("ts") or row.get("timestamp"), "timestamp")
        except ValueError as exc:
            incidents.append(
                _incident(
                    department=department,
                    code=f"incident_invalid:{type(exc).__name__}",
                    source_ref=f"{source_ref}:{line_number}",
                )
            )
            continue
        out.append(
            {
                "id": _stable_id("incident", department, source_ref, line_number),
                "department": safe_department,
                "code": code,
                "severity": severity,
                "status": status,
                "ts": timestamp,
                "source_ref": f"{source_ref}:{line_number}",
                "schema_version": SCHEMA_VERSION,
            }
        )
    return out


def _receipt_rows(root, path, department, incidents):
    source_ref = _relative(root, path)
    if path.suffix == ".jsonl":
        source_rows = _read_jsonl(root, path, department, incidents)
    else:
        value = _read_json(root, path, department, incidents)
        source_rows = [] if value is None else [(1, value)]
    out = []
    for line_number, row in source_rows:
        receipt_id = row.get("receipt_id") or row.get("id") or row.get("nonce")
        try:
            safe_department = _identifier(
                row.get("department") or department, "department", nullable=False
            )
            receipt_id = _identifier(receipt_id, "receipt_id")
            run_id = _identifier(row.get("run_id"), "run_id")
            step_id = _identifier(row.get("step_id"), "step_id")
            node = _identifier(row.get("node"), "node")
            receipt_type = _identifier(
                row.get("receipt_type") or row.get("kind") or path.stem,
                "receipt_type",
                nullable=False,
            )
            status = _identifier(row.get("status"), "status")
            timestamp = _identifier(row.get("ts") or row.get("timestamp"), "timestamp")
        except ValueError as exc:
            incidents.append(
                _incident(
                    department=department,
                    code=f"receipt_invalid:{type(exc).__name__}",
                    source_ref=f"{source_ref}:{line_number}",
                )
            )
            continue
        out.append(
            {
                "id": _stable_id("receipt", department, receipt_id, source_ref, line_number),
                "department": safe_department,
                "run_id": run_id,
                "step_id": step_id,
                "node": node,
                "receipt_type": receipt_type,
                "status": status,
                "ts": timestamp,
                "verified": None if row.get("verified") is None else int(bool(row.get("verified"))),
                "source_ref": f"{source_ref}:{line_number}",
                "schema_version": SCHEMA_VERSION,
            }
        )
    return out


def _insert_rows(connection: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]):
    for row in sorted(rows, key=lambda item: item["id"]):
        columns = tuple(row)
        connection.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            tuple(row[column] for column in columns),
        )


def rebuild(
    root: str | Path,
    db_path: str | Path | None = None,
    signer=None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    db_path = Path(db_path or root / "estate" / "state" / "rollup.sqlite3")
    if signer is None:
        signer = _resolve_verifier()
    incidents: list[dict[str, Any]] = []
    rows: dict[str, list[dict[str, Any]]] = {entity: [] for entity in ENTITIES}

    department_dirs = sorted((root / "departments").glob("*/state"))
    for state_dir in department_dirs:
        department = state_dir.parent.name
        state_path = state_dir / "STATE.json"
        state = (
            _read_json(root, state_path, department, incidents)
            if _source_available(
                root, state_path, department, incidents, required=True
            )
            else None
        )
        rows["department"].append(_department_row(root, department, state_dir, state))
        if state:
            findings = state.get("open_findings") or state.get("findings") or []
            if isinstance(findings, list):
                for index, finding in enumerate(findings):
                    if not isinstance(finding, dict):
                        continue
                    code = finding.get("code") or "manager_finding"
                    try:
                        code = _identifier(code, "finding code", nullable=False)
                        severity = _identifier(finding.get("severity"), "severity")
                        status = _identifier(
                            finding.get("status") or "open", "status", nullable=False
                        )
                        timestamp = _identifier(
                            finding.get("ts") or state.get("last_cycle_at"), "timestamp"
                        )
                    except ValueError as exc:
                        incidents.append(
                            _incident(
                                department=department,
                                code=f"state_finding_invalid:{type(exc).__name__}",
                                source_ref=_relative(root, state_path),
                            )
                        )
                        continue
                    rows["incident"].append(
                        {
                            "id": _stable_id("state-finding", department, code, index),
                            "department": department,
                            "code": code,
                            "severity": severity,
                            "status": status,
                            "ts": timestamp,
                            "source_ref": _relative(root, state_path),
                            "schema_version": SCHEMA_VERSION,
                        }
                    )
        # F2: the signed execution projection is the authority on graph runs
        backed = _projection_runs(root, state_dir, department, incidents, signer)
        rows["run"].extend(
            _projection_run_rows(root, state_dir, department, backed))
        for run_path in (state_dir / "runs.jsonl", state_dir / "runs-v2.jsonl"):
            if _source_available(root, run_path, department, incidents):
                rows["run"].extend(_run_rows(root, run_path, department, incidents, signer, backed))
        if _source_available(root, state_dir / "telemetry.jsonl", department, incidents):
            rows["step_telemetry"].extend(_telemetry_rows(
                root, state_dir / "telemetry.jsonl", department, incidents,
                signer, backed))
        if _source_available(root, state_dir / "scores.jsonl", department, incidents):
            rows["score"].extend(_score_rows(
                root, state_dir / "scores.jsonl", department, incidents,
                signer, backed))
        for path in sorted(state_dir.rglob("*")):
            if path.is_symlink():
                incidents.append(
                    _incident(
                        department=department,
                        code="source_unreadable:UnsafeFileType",
                        source_ref=_relative(root, path),
                    )
                )
                continue
            if not path.is_file():
                continue
            name = path.name.lower()
            if "receipt" in name and path.suffix in {".json", ".jsonl"}:
                rows["receipt"].extend(_receipt_rows(root, path, department, incidents))
            elif "incident" in name and path.suffix == ".jsonl":
                rows["incident"].extend(_incident_rows(root, path, department, incidents))
            elif ("approval" in name or "outbox" in name) and path.suffix == ".jsonl":
                rows["approval"].extend(_approval_rows(root, path, department, incidents))

    estate_state = root / "estate" / "state"
    if estate_state.exists():
        estate_json = estate_state / "STATE.json"
        estate_value = (
            _read_json(root, estate_json, "estate", incidents)
            if _source_available(
                root, estate_json, "estate", incidents, required=True
            )
            else None
        )
        rows["department"].append(_department_row(root, "estate", estate_state, estate_value))
        for path in sorted(estate_state.rglob("*.jsonl")):
            if path.is_symlink():
                incidents.append(
                    _incident(
                        department="estate",
                        code="source_unreadable:UnsafeFileType",
                        source_ref=_relative(root, path),
                    )
                )
                continue
            name = path.name.lower()
            if "incident" in name:
                rows["incident"].extend(_incident_rows(root, path, "estate", incidents))
            elif "approval" in name or "outbox" in name:
                rows["approval"].extend(_approval_rows(root, path, "estate", incidents))
            elif "receipt" in name:
                rows["receipt"].extend(_receipt_rows(root, path, "estate", incidents))
    # Promotion-id-keyed rows: a crash-recovered promotion re-appends the
    # SAME spool, so duplicates must be byte-identical; conflicting bodies
    # under one id are quarantined rather than silently resolved (F3).
    for entity in ("run", "step_telemetry", "score"):
        rows[entity] = _collapse_promotions(rows[entity], incidents)
    rows["incident"].extend(incidents)
    rows["incident"] = list({row["id"]: row for row in rows["incident"]}.values())
    deduped_runs: dict[str, dict[str, Any]] = {}
    for row in rows["run"]:
        prior = deduped_runs.get(row["id"])
        if prior is None or ((row.get("ts") or ""), row.get("source_ref") or "") >= (
            (prior.get("ts") or ""), prior.get("source_ref") or ""
        ):
            deduped_runs[row["id"]] = row
    rows["run"] = list(deduped_runs.values())

    db_path = db_path.absolute()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{db_path.name}.", dir=db_path.parent))
    temp_path = temp_dir / "rollup.sqlite3"
    connection = sqlite3.connect(temp_path)
    try:
        connection.executescript(DDL)
        for entity in ENTITIES:
            _insert_rows(connection, entity, rows[entity])
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()
    complete = not incidents
    published_path = (
        db_path if complete else db_path.with_name(db_path.name + ".incomplete")
    )
    os.replace(temp_path, published_path)
    temp_dir.rmdir()
    return {
        "database": str(published_path),
        "requested_database": str(db_path),
        "counts": {entity: len(rows[entity]) for entity in ENTITIES},
        "findings": len(incidents),
        "complete": complete,
        "schema_version": SCHEMA_VERSION,
    }


def graph_run_bundle(db_path: str | Path, run_id: str) -> dict[str, list]:
    """Return every rollup row correlated to ONE graph execution.

    The graph runner's canonical run_id is the one correlation key across the
    projection-derived run, promoted run summaries, telemetry, and scores.
    """
    run_id = _identifier(run_id, "run_id", nullable=False)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        bundle = {
            "run": [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM run WHERE run_id = ? ORDER BY id",
                    (run_id,),
                )
            ],
        }
        for entity in ("step_telemetry", "score"):
            bundle[entity] = [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {entity} WHERE run_id = ? ORDER BY id",
                    (run_id,),
                )
            ]
    finally:
        connection.close()
    return bundle


def export_ndjson(db_path: str | Path, export_dir: str | Path) -> dict[str, int]:
    db_path = Path(db_path)
    if db_path.name.endswith(".incomplete"):
        raise ValueError("refusing export from an incomplete rollup")
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    counts = {}
    try:
        for entity in ENTITIES:
            columns = "*"
            if entity == "score":
                columns = (
                    "id,department,run_id,step_id,node,name,value,label,source,"
                    "judge_model,config_version,ts,source_ref,schema_version"
                )
            records = [
                dict(row)
                for row in connection.execute(
                    f"SELECT {columns} FROM {entity} ORDER BY id"
                )
            ]
            target = export_dir / f"{entity}.ndjson"
            content = "".join(
                json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
                for record in records
            )
            temp_fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=export_dir
            )
            with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
            counts[entity] = len(records)
    finally:
        connection.close()
    return counts
