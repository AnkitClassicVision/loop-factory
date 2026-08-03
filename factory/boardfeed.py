"""Build the deterministic estate board feed from the canonical rollup.

``estate/state/rollup.sqlite3`` is authoritative for every entity it covers:
departments, runs, step telemetry, scores, incidents, approvals, and receipts.
The board never reconstructs those entities from department records and never
falls back to direct reads when the rollup is missing, incomplete, or stale.

The only sanctioned direct-read exceptions are:

* ``heartbeats.jsonl`` for live liveness, because heartbeats are intentionally
  newer than the periodic rollup and only augment its department status;
* ``estate/state/timers.json`` for the host timer snapshot, because timers are
  not part of the rollup schema;
* ``charter.yaml`` for display metadata such as autonomy mode and objective
  setpoints, because governance metadata is not part of the reporting rollup.
* ``objectives_observed.json`` for measured objective values, because each
  department owns the generic sensor that produces those measurements.

These exceptions may add live or descriptive context. They may not replace a
canonical rollup entity. The sole writes are the atomically replaced estate
feed and its deterministic history snapshot.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import stat
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from factory.charter_loader import CharterError, load_charter
LOGGER = logging.getLogger(__name__)
UNKNOWN = "unknown"
ALLOWED_AUTH_CLASSES = frozenset({"oauth_cli", "service_oauth", "local_model"})
OPEN_APPROVAL_STATUSES = frozenset({"pending_approval", "pending", "open", "queued"})
CLOSED_INCIDENT_STATUSES = frozenset({"closed", "resolved", "dismissed", "cleared"})
TIMER_SNAPSHOT_SCHEMA = "timers-snapshot/v1"
TIMER_RESULTS = frozenset({"success", "failure", UNKNOWN})
OBJECTIVES_OBSERVED_SCHEMA = "objectives-observed/v1"
OBJECTIVES_OBSERVED_MAX_AGE = timedelta(hours=48)
ROLLUP_ENTITIES = (
    "department",
    "run",
    "step_telemetry",
    "receipt",
    "score",
    "incident",
    "approval",
)
ROLLUP_REQUIRED_COLUMNS = {
    "department": {
        "id", "epoch", "status", "last_cycle_at", "ok", "source_ref", "schema_version",
    },
    "run": {
        "id", "department", "run_id", "current_step", "status", "ts", "epoch",
        "source_ref", "schema_version",
    },
    "step_telemetry": {
        "id", "department", "run_id", "step_id", "node", "ts", "operation_name",
        "provider_name", "request_model", "response_model", "input_tokens",
        "output_tokens", "finish_reasons_json", "duration_ms", "error_type",
        "cost_usd", "auth_route", "engine", "estimated", "price_schema_version",
        "price_effective_date", "telemetry_source", "source_ref", "schema_version",
    },
    "receipt": {
        "id", "department", "run_id", "step_id", "node", "receipt_type", "status",
        "ts", "verified", "source_ref", "schema_version",
    },
    "score": {
        "id", "department", "run_id", "step_id", "node", "name", "value", "label",
        "explanation", "source", "judge_model", "config_version", "ts", "source_ref",
        "schema_version",
    },
    "incident": {
        "id", "department", "code", "severity", "status", "ts", "source_ref",
        "schema_version",
    },
    "approval": {
        "id", "department", "decision_id", "status", "queued_at", "card_ref",
        "source_ref", "schema_version",
    },
}
DEFAULT_ROLLUP_MAX_AGE_SECONDS = 15 * 60


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    parsed = _parse_ts(value)
    if parsed is None:
        raise ValueError(f"invalid ISO timestamp: {value!r}")
    return parsed


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _hour_bucket(value: str) -> str:
    parsed = _parse_ts(value)
    if parsed is None:
        return UNKNOWN
    return parsed.strftime("%Y-%m-%dT%H:00:00Z")


def _event_id(kind: str, department: str, subject: str, ts: str) -> str:
    return f"{kind}:{department}:{subject}:{ts}"


def _bucket_id(kind: str, department: str, subject: str, ts: str) -> str:
    return f"{kind}:{department}:{subject}:{_hour_bucket(ts)}"


def _line(
    *, kind: str, ts: str, department: str, subject: str, data: dict[str, Any], event: bool
) -> dict[str, Any]:
    make_id = _event_id if event else _bucket_id
    return {
        "id": make_id(kind, department, subject, ts),
        "kind": kind,
        "ts": ts,
        "department": department,
        "data": data,
    }


def _read_json(path: Path) -> tuple[dict[str, Any] | None, int]:
    if not path.exists():
        return None, 0
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        LOGGER.warning("malformed JSON source: %s", path)
        return None, 1
    if not isinstance(value, dict):
        LOGGER.warning("JSON source is not an object: %s", path)
        return None, 1
    return value, 0


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], 0
    rows: list[dict[str, Any]] = []
    malformed = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        LOGGER.warning("unreadable JSONL source: %s", path)
        return [], 1
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            malformed += 1
            LOGGER.warning("malformed JSONL source: %s:%d", path, line_number)
            continue
        if not isinstance(value, dict):
            malformed += 1
            LOGGER.warning("JSONL row is not an object: %s:%d", path, line_number)
            continue
        rows.append(value)
    return rows, malformed


def _scalar(value: Any) -> Any:
    if value is None:
        return UNKNOWN
    if isinstance(value, (str, int, float, bool)):
        return value
    return UNKNOWN


def _rollup_file_state(
    db_path: Path, now: datetime, max_age_seconds: int
) -> dict[str, Any]:
    """Describe canonical projection availability without opening unsafe files."""
    if isinstance(max_age_seconds, bool) or max_age_seconds < 0:
        raise ValueError("rollup_max_age_seconds must be a non-negative integer")
    incomplete_path = db_path.with_name(db_path.name + ".incomplete")

    def regular_mtime(path: Path) -> float | None:
        try:
            result = path.lstat()
        except OSError:
            return None
        if path.is_symlink() or not stat.S_ISREG(result.st_mode):
            return None
        return result.st_mtime

    db_mtime = regular_mtime(db_path)
    incomplete_mtime = regular_mtime(incomplete_path)
    if db_mtime is None:
        return {
            "status": "incomplete",
            "reason": (
                "rollup_rebuild_incomplete"
                if incomplete_mtime is not None
                else "rollup_missing"
            ),
            "age_s": UNKNOWN,
            "updated_at": UNKNOWN,
            "readable": False,
        }

    age_s = max(0, int(now.timestamp() - db_mtime))
    updated_at = _iso(datetime.fromtimestamp(db_mtime, tz=timezone.utc))
    if incomplete_mtime is not None and incomplete_mtime >= db_mtime:
        status = "incomplete"
        reason = "newer_incomplete_rebuild"
    elif age_s > max_age_seconds:
        status = "stale"
        reason = "rollup_too_old"
    else:
        status = "fresh"
        reason = "rollup_current"
    return {
        "status": status,
        "reason": reason,
        "age_s": age_s,
        "updated_at": updated_at,
        "readable": True,
    }


def _load_rollup_rows(db_path: Path) -> dict[str, list[dict[str, Any]]]:
    """Read the published rollup read-only and reject partial schemas."""
    rows = {entity: [] for entity in ROLLUP_ENTITIES}
    connection = sqlite3.connect(
        f"file:{db_path.resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing = set(ROLLUP_ENTITIES) - tables
        if missing:
            raise ValueError(
                "rollup is missing tables: " + ", ".join(sorted(missing))
            )
        for entity in ROLLUP_ENTITIES:
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({entity})")
            }
            missing_columns = ROLLUP_REQUIRED_COLUMNS[entity] - columns
            if missing_columns:
                raise ValueError(
                    f"rollup {entity} is missing columns: "
                    + ", ".join(sorted(missing_columns))
                )
            entity_rows = [
                dict(row)
                for row in connection.execute(f"SELECT * FROM {entity} ORDER BY id")
            ]
            rows[entity] = entity_rows
    finally:
        connection.close()
    return rows


def _canonical_snapshot(
    db_path: Path, now: datetime, max_age_seconds: int
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], int]:
    health = _rollup_file_state(db_path, now, max_age_seconds)
    empty = {entity: [] for entity in ROLLUP_ENTITIES}
    if not health["readable"]:
        return empty, health, 0
    try:
        return _load_rollup_rows(db_path), health, 0
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        LOGGER.warning("unreadable canonical rollup: %s", exc)
        health.update(
            {
                "status": "incomplete",
                "reason": "rollup_unreadable",
                "readable": False,
            }
        )
        return empty, health, 1


def _latest_heartbeats(
    department_dirs: Sequence[Path],
) -> tuple[dict[str, dict[str, Any]], int]:
    heartbeats: dict[str, dict[str, Any]] = {}
    malformed = 0
    for department_dir in department_dirs:
        rows, count = _read_jsonl(
            department_dir / "state" / "heartbeats.jsonl"
        )
        malformed += count
        if rows:
            heartbeats[department_dir.name] = rows[-1]
    return heartbeats, malformed


def _canonical_status_line(
    department: str,
    canonical: dict[str, Any] | None,
    heartbeat: dict[str, Any] | None,
    charter: dict[str, Any] | None,
    open_incidents: int,
) -> dict[str, Any]:
    canonical = canonical or {}
    heartbeat = heartbeat or {}
    status_ts = canonical.get("last_cycle_at")
    if _parse_ts(status_ts) is None:
        status_ts = heartbeat.get("ts")
    if _parse_ts(status_ts) is None:
        status_ts = UNKNOWN
    heartbeat_ok = heartbeat.get("ok")
    canonical_ok = canonical.get("ok")
    ok = heartbeat_ok if isinstance(heartbeat_ok, bool) else canonical_ok
    if ok in (0, 1):
        ok = bool(ok)
    elif not isinstance(ok, bool):
        ok = UNKNOWN
    return _line(
        kind="dept_status",
        ts=status_ts,
        department=department,
        subject="status",
        event=False,
        data={
            "autonomy_state": _scalar((charter or {}).get("autonomy_state")),
            "epoch": _scalar(canonical.get("epoch")),
            "last_cycle_at": _scalar(canonical.get("last_cycle_at")),
            "ok": ok,
            "open_findings": open_incidents,
            "escalations": UNKNOWN,
        },
    )


def _canonical_andons(
    incidents: Iterable[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for incident in incidents:
        if incident.get("status") in CLOSED_INCIDENT_STATUSES:
            continue
        department = incident.get("department") or "estate"
        ts = incident.get("ts")
        if _parse_ts(ts) is None:
            ts = _iso(now)
        output.append(
            _line(
                kind="andon",
                ts=ts,
                department=department,
                subject=str(incident["id"]),
                event=True,
                data={
                    "severity": _scalar(incident.get("severity")),
                    "code": _scalar(incident.get("code")),
                    "detail": UNKNOWN,
                    "observed": UNKNOWN,
                    "setpoint": UNKNOWN,
                },
            )
        )
    return output


def _telemetry_policy_andons(
    telemetry: Iterable[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in telemetry:
        auth_route = row.get("auth_route")
        if auth_route in ALLOWED_AUTH_CLASSES:
            continue
        code = "AUTH" if auth_route == "blocked" else "POLICY"
        department = row.get("department") or "estate"
        run_id = row.get("run_id") or row["id"]
        key = (department, str(run_id), code)
        if key in seen:
            continue
        seen.add(key)
        ts = row.get("ts")
        if _parse_ts(ts) is None:
            ts = _iso(now)
        output.append(
            _line(
                kind="andon",
                ts=ts,
                department=department,
                subject=f"{code}-{run_id}",
                event=True,
                data={
                    "severity": "breach",
                    "code": code,
                    "detail": (
                        "authentication lane blocked"
                        if code == "AUTH"
                        else "metered model lane is forbidden"
                    ),
                    "observed": _scalar(auth_route),
                    "setpoint": "subscription_oauth_only",
                    "run_id": _scalar(row.get("run_id")),
                },
            )
        )
    return output


def _canonical_active_runs(
    records: Iterable[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    lower = now - timedelta(hours=24)
    output: list[dict[str, Any]] = []
    for record in records:
        record_dt = _parse_ts(record.get("ts"))
        if record_dt is None or not (lower <= record_dt <= now):
            continue
        ts = _iso(record_dt)
        output.append(
            _line(
                kind="active_run",
                ts=ts,
                department=record["department"],
                subject=record["run_id"],
                event=True,
                data={
                    "run_id": record["run_id"],
                    "node": _scalar(record.get("current_step")),
                    "status": _scalar(record.get("status")),
                    "attempt": UNKNOWN,
                    "engine": UNKNOWN,
                    "model": UNKNOWN,
                    "ts": ts,
                },
            )
        )
    return output


def _sum_canonical(rows: Iterable[dict[str, Any]], field: str) -> int | str:
    values = [row.get(field) for row in rows]
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        return UNKNOWN
    return sum(values)


def _percent_pass(scores: Sequence[dict[str, Any]]) -> int | float | str:
    if not scores:
        return UNKNOWN
    rate = 100 * sum(row.get("label") == "pass" for row in scores) / len(scores)
    return int(rate) if rate.is_integer() else round(rate, 1)


def _canonical_metrics(
    department: str,
    runs: Sequence[dict[str, Any]],
    telemetry: Sequence[dict[str, Any]],
    scores: Sequence[dict[str, Any]],
    receipts: Sequence[dict[str, Any]],
    now: datetime,
    source_available: bool,
) -> list[dict[str, Any]]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    forbidden_run_ids = {
        row.get("run_id")
        for row in telemetry
        if row.get("department") == department
        and row.get("auth_route") not in ALLOWED_AUTH_CLASSES
    }
    daily_runs = [
        row
        for row in runs
        if row.get("department") == department
        and row.get("run_id") not in forbidden_run_ids
        and (stamp := _parse_ts(row.get("ts"))) is not None
        and start <= stamp <= now
    ]
    daily_telemetry = [
        row
        for row in telemetry
        if row.get("department") == department
        and row.get("auth_route") in ALLOWED_AUTH_CLASSES
        and (stamp := _parse_ts(row.get("ts"))) is not None
        and start <= stamp <= now
    ]
    daily_scores = [
        row
        for row in scores
        if row.get("department") == department
        and (stamp := _parse_ts(row.get("ts"))) is not None
        and start <= stamp <= now
    ]
    period = start.date().isoformat()
    ts = _iso(now)
    summary = {
        "metric_type": "daily_rollup",
        "period": period,
        "runs": len(daily_runs),
        "ok": sum(row.get("status") == "ok" for row in daily_runs),
        "error": sum(row.get("status") == "error" for row in daily_runs),
        "blocked": sum(row.get("status") == "blocked" for row in daily_runs),
        "tokens_in": _sum_canonical(daily_telemetry, "input_tokens"),
        "tokens_out": _sum_canonical(daily_telemetry, "output_tokens"),
        "model_calls": len(daily_telemetry),
        "evaluator_pass_rate": _percent_pass(daily_scores),
    }
    if not source_available:
        summary.update(
            {
                key: UNKNOWN
                for key in (
                    "runs",
                    "ok",
                    "error",
                    "blocked",
                    "tokens_in",
                    "tokens_out",
                    "model_calls",
                    "evaluator_pass_rate",
                )
            }
        )
    output = [
        _line(
            kind="metrics",
            ts=ts,
            department=department,
            subject=f"daily-{period}",
            event=False,
            data=summary,
        )
    ]

    lanes: defaultdict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in daily_telemetry:
        lanes[
            (
                row.get("engine"),
                row.get("response_model") or row.get("request_model"),
                row.get("auth_route"),
            )
        ].append(row)
    for (engine, model, auth_route), lane_rows in sorted(
        lanes.items(),
        key=lambda item: tuple(
            "" if value is None else str(value) for value in item[0]
        ),
    ):
        subject = "lane-" + "-".join(
            str(value) if value is not None else UNKNOWN
            for value in (engine, model, auth_route)
        )
        output.append(
            _line(
                kind="metrics",
                ts=ts,
                department=department,
                subject=subject,
                event=False,
                data={
                    "metric_type": "lane_telemetry",
                    "lane": _scalar(engine),
                    "model": _scalar(model),
                    "auth_class": _scalar(auth_route),
                    "calls": len(lane_rows),
                    "tokens_in": _sum_canonical(lane_rows, "input_tokens"),
                    "tokens_out": _sum_canonical(lane_rows, "output_tokens"),
                    "period": period,
                },
            )
        )

    department_scores = [
        row for row in scores if row.get("department") == department
    ]
    output.append(
        _line(
            kind="metrics",
            ts=ts,
            department=department,
            subject="canonical-scores",
            event=False,
            data={
                "group": "canonical scores",
                "total": len(department_scores) if source_available else UNKNOWN,
                "pass": (
                    sum(row.get("label") == "pass" for row in department_scores)
                    if source_available
                    else UNKNOWN
                ),
                "fail": (
                    sum(row.get("label") == "fail" for row in department_scores)
                    if source_available
                    else UNKNOWN
                ),
                "pass_rate": (
                    _percent_pass(department_scores) if source_available else UNKNOWN
                ),
            },
        )
    )
    department_receipts = [
        row for row in receipts if row.get("department") == department
    ]
    output.append(
        _line(
            kind="metrics",
            ts=ts,
            department=department,
            subject="canonical-receipts",
            event=False,
            data={
                "group": "canonical receipts",
                "total": len(department_receipts) if source_available else UNKNOWN,
                "verified": (
                    sum(row.get("verified") == 1 for row in department_receipts)
                    if source_available
                    else UNKNOWN
                ),
                "unverified": (
                    sum(row.get("verified") != 1 for row in department_receipts)
                    if source_available
                    else UNKNOWN
                ),
            },
        )
    )
    return output


def _canonical_approvals(
    rows: Iterable[dict[str, Any]], now: datetime
) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    malformed = 0
    for row in rows:
        if row.get("status") not in OPEN_APPROVAL_STATUSES:
            continue
        queued_dt = _parse_ts(row.get("queued_at"))
        if queued_dt is None:
            malformed += 1
            continue
        queued_at = _iso(queued_dt)
        subject = row.get("card_ref") or row.get("decision_id") or row["id"]
        data = {
            "status": row["status"],
            "queued_at": queued_at,
            "age_s": max(0, int((now - queued_dt).total_seconds())),
        }
        if row.get("card_ref") is not None:
            data["card_ref"] = row["card_ref"]
        output.append(
            _line(
                kind="approval",
                ts=queued_at,
                department=row.get("department") or "estate",
                subject=str(subject),
                event=True,
                data=data,
            )
        )
    return output, malformed


def _objective_metrics(
    department: str, charter: dict[str, Any] | None, now: datetime
) -> list[dict[str, Any]]:
    if not charter:
        return []
    setpoints = charter.get("setpoints")
    objectives = setpoints.get("objectives") if isinstance(setpoints, dict) else None
    if objectives is None:
        objectives = charter.get("objectives")
    if not isinstance(objectives, dict):
        return []
    ts = _iso(now)
    output: list[dict[str, Any]] = []
    for objective_id, raw in sorted(objectives.items(), key=lambda item: str(item[0])):
        values = raw if isinstance(raw, dict) else {"setpoint": raw}
        objective_id = str(objective_id)
        output.append(
            _line(
                kind="metrics",
                ts=ts,
                department=department,
                subject=f"objective-{objective_id}",
                event=False,
                data={
                    "metric_type": "objective",
                    "objective_id": objective_id,
                    "label": _scalar(values.get("label")),
                    "setpoint": _scalar(values.get("setpoint")),
                    "minimum": _scalar(values.get("minimum")),
                    "target": _scalar(values.get("target")),
                    "observed": _scalar(values.get("observed")),
                    "unit": _scalar(values.get("unit")),
                },
            )
        )
    return output


def _load_objectives_observed(
    path: Path, now: datetime
) -> tuple[dict[str, Any] | None, int]:
    snapshot, malformed = _read_json(path)
    if snapshot is None:
        return None, malformed
    observed_at = _parse_ts(snapshot.get("ts"))
    values = snapshot.get("values")
    valid_values = isinstance(values, dict) and all(
        isinstance(objective_id, str)
        and (
            isinstance(value, str)
            or (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
        )
        for objective_id, value in (values.items() if isinstance(values, dict) else ())
    )
    if (
        snapshot.get("schema") != OBJECTIVES_OBSERVED_SCHEMA
        or observed_at is None
        or not valid_values
    ):
        LOGGER.warning("invalid objectives-observed source: %s", path)
        return None, malformed + 1
    return {
        "ts": snapshot["ts"],
        "values": values,
        "stale": now - observed_at > OBJECTIVES_OBSERVED_MAX_AGE,
    }, malformed


def _merge_objectives_observed(
    objectives: list[dict[str, Any]], snapshot: dict[str, Any] | None
) -> None:
    if snapshot is None:
        return
    values = snapshot["values"]
    for objective in objectives:
        data = objective["data"]
        objective_id = data["objective_id"]
        if objective_id not in values:
            continue
        data["observed"] = values[objective_id]
        data["observed_ts"] = snapshot["ts"]
        if snapshot["stale"]:
            data["stale"] = True


def _objective_breach_andons(
    objectives: Iterable[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for objective in objectives:
        data = objective["data"]
        observed = data.get("observed")
        minimum = data.get("minimum")
        if (
            isinstance(observed, bool)
            or isinstance(minimum, bool)
            or not isinstance(observed, (int, float))
            or not isinstance(minimum, (int, float))
            or not math.isfinite(observed)
            or not math.isfinite(minimum)
            or observed >= minimum
        ):
            continue
        label = data.get("label")
        if label == UNKNOWN:
            label = data["objective_id"]
        ts = data.get("observed_ts", _iso(now))
        output.append(
            _line(
                kind="andon",
                ts=ts,
                department=objective["department"],
                subject=f"objective-below-min-{data['objective_id']}",
                event=True,
                data={
                    "code": "OBJECTIVE_BELOW_MIN",
                    "severity": "breach",
                    "detail": (
                        f"{label}: observed {observed} below minimum {minimum}"
                    ),
                    "observed": observed,
                    "setpoint": minimum,
                },
            )
        )
    return output


def _load_charter(path: Path, department: str) -> tuple[dict[str, Any] | None, int]:
    if not path.exists():
        return None, 0
    try:
        return load_charter(path, expect_department=department), 0
    except CharterError:
        LOGGER.warning("invalid charter source: %s", path)
        return None, 1


def _timer_lines(
    path: Path, restrict: str | None
) -> tuple[list[dict[str, Any]], int, int]:
    snapshot, malformed = _read_json(path)
    if snapshot is None:
        return [], malformed, 0

    captured_dt = _parse_ts(snapshot.get("captured_at"))
    timers = snapshot.get("timers")
    if (
        snapshot.get("schema") != TIMER_SNAPSHOT_SCHEMA
        or captured_dt is None
        or not isinstance(timers, list)
    ):
        LOGGER.warning("invalid timer snapshot schema: %s", path)
        return [], malformed + 1, 0

    captured_at = _iso(captured_dt)
    output: list[dict[str, Any]] = []
    loops = 0
    required = {
        "unit",
        "service",
        "enabled",
        "next_run",
        "last_run",
        "last_result",
        "exit_status",
        "group",
    }
    for timer in timers:
        if not isinstance(timer, dict) or not required.issubset(timer):
            malformed += 1
            LOGGER.warning("invalid timer row in snapshot: %s", path)
            continue

        unit = timer["unit"]
        service = timer["service"]
        group = timer["group"]
        enabled = timer["enabled"]
        next_run = timer["next_run"]
        last_run = timer["last_run"]
        last_result = timer["last_result"]
        exit_status = timer["exit_status"]
        valid = (
            isinstance(unit, str)
            and bool(unit)
            and isinstance(service, str)
            and bool(service)
            and isinstance(group, str)
            and bool(group)
            and isinstance(enabled, bool)
            and (next_run is None or isinstance(next_run, str))
            and (last_run is None or isinstance(last_run, str))
            and isinstance(last_result, str)
            and last_result in TIMER_RESULTS
            and (
                exit_status is None
                or isinstance(exit_status, (str, int))
                and not isinstance(exit_status, bool)
            )
        )
        if not valid:
            malformed += 1
            LOGGER.warning("invalid timer row in snapshot: %s", path)
            continue
        if restrict is not None and group != restrict:
            continue

        output.append(
            _line(
                kind="loop_status",
                ts=captured_at,
                department=group,
                subject=unit,
                event=False,
                data={
                    "unit": unit,
                    "service": service,
                    "enabled": enabled,
                    "next_run": next_run,
                    "last_run": last_run,
                    "last_result": last_result,
                    "exit_status": exit_status,
                },
            )
        )
        loops += 1
        if last_result == "failure":
            status = UNKNOWN if exit_status is None else str(exit_status)
            output.append(
                _line(
                    kind="andon",
                    ts=captured_at,
                    department=group,
                    subject=f"LOOP_FAILED-{unit}",
                    event=True,
                    data={
                        "code": "LOOP_FAILED",
                        "severity": "breach",
                        "detail": f"{unit} last run failed (exit {status})",
                        "observed": "failure",
                        "setpoint": "success",
                    },
                )
            )
    return output, malformed, loops


def _departments(repo_root: Path, restrict: str | None) -> list[Path]:
    root = repo_root / "departments"
    if not root.exists():
        return []
    candidates = [path for path in root.iterdir() if path.is_dir() and (path / "state").is_dir()]
    if restrict is not None:
        candidates = [path for path in candidates if path.name == restrict]
    return sorted(candidates, key=lambda path: path.name)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _history_snapshot(feed: Sequence[dict[str, Any]], date: str) -> dict[str, Any]:
    departments: dict[str, dict[str, Any]] = {}
    loop_totals: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "failed": 0}
    )
    metric_keys = (
        "runs",
        "ok",
        "error",
        "blocked",
        "tokens_in",
        "tokens_out",
        "model_calls",
    )
    for row in feed:
        data = row["data"]
        if row["kind"] == "metrics" and data.get("metric_type") == "daily_rollup":
            departments[row["department"]] = {
                key: data.get(key, UNKNOWN) for key in metric_keys
            }
        elif row["kind"] == "loop_status":
            group = loop_totals[row["department"]]
            group["total"] += 1
            if data.get("last_result") == "failure":
                group["failed"] += 1
    return {
        "schema": "board-history/v1",
        "date": date,
        "departments": dict(sorted(departments.items())),
        "loops": dict(sorted(loop_totals.items())),
    }


def build_feed(
    repo_root: str | Path,
    *,
    out: str | Path | None = None,
    department: str | None = None,
    now: str | datetime | None = None,
    timers_path: str | Path | None = None,
    history_dir: str | Path | None = None,
    rollup_path: str | Path | None = None,
    rollup_max_age_seconds: int = DEFAULT_ROLLUP_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Render canonical rollup state plus sanctioned live metadata exceptions."""
    repo_root = Path(repo_root)
    output_path = Path(out) if out is not None else repo_root / "estate" / "state" / "board-feed.ndjson"
    now_dt = _now(now)
    feed: list[dict[str, Any]] = []
    malformed = 0
    department_dirs = _departments(repo_root, department)
    canonical_path = (
        Path(rollup_path)
        if rollup_path is not None
        else repo_root / "estate" / "state" / "rollup.sqlite3"
    )
    canonical, projection, count = _canonical_snapshot(
        canonical_path, now_dt, rollup_max_age_seconds
    )
    malformed += count
    if department is not None:
        for entity, rows in canonical.items():
            if entity == "department":
                canonical[entity] = [row for row in rows if row.get("id") == department]
            else:
                canonical[entity] = [
                    row for row in rows if row.get("department") == department
                ]

    directory_by_name = {path.name: path for path in department_dirs}
    canonical_departments = {
        row["id"]: row
        for row in canonical["department"]
        if row.get("id") != "estate"
    }
    names = sorted(canonical_departments)
    heartbeat_dirs = [
        directory_by_name[name] for name in names if name in directory_by_name
    ]
    heartbeats, count = _latest_heartbeats(heartbeat_dirs)
    malformed += count
    open_incidents: defaultdict[str, int] = defaultdict(int)
    for incident in canonical["incident"]:
        if incident.get("status") not in CLOSED_INCIDENT_STATUSES:
            open_incidents[incident.get("department") or "estate"] += 1

    for name in names:
        charter = None
        department_dir = directory_by_name.get(name)
        if department_dir is not None:
            charter, count = _load_charter(department_dir / "charter.yaml", name)
            malformed += count
        feed.append(
            _canonical_status_line(
                name,
                canonical_departments.get(name),
                heartbeats.get(name),
                charter,
                open_incidents[name],
            )
        )
        objectives = _objective_metrics(name, charter, now_dt)
        observed = None
        if department_dir is not None:
            observed, count = _load_objectives_observed(
                department_dir / "state" / "objectives_observed.json", now_dt
            )
            malformed += count
        _merge_objectives_observed(objectives, observed)
        feed.extend(objectives)
        feed.extend(_objective_breach_andons(objectives, now_dt))
        feed.extend(
            _canonical_metrics(
                name,
                canonical["run"],
                canonical["step_telemetry"],
                canonical["score"],
                canonical["receipt"],
                now_dt,
                projection["readable"],
            )
        )

    feed.extend(_canonical_andons(canonical["incident"], now_dt))
    feed.extend(_telemetry_policy_andons(canonical["step_telemetry"], now_dt))
    feed.extend(_canonical_active_runs(canonical["run"], now_dt))
    approvals, count = _canonical_approvals(canonical["approval"], now_dt)
    malformed += count
    feed.extend(approvals)

    timer_source = (
        Path(timers_path)
        if timers_path is not None
        else repo_root / "estate" / "state" / "timers.json"
    )
    timer_feed, count, loops = _timer_lines(timer_source, department)
    malformed += count
    feed.extend(timer_feed)

    feed.sort(key=lambda row: (row["department"], row["kind"], row["id"]))
    health_ts = _iso(now_dt)
    feed.append(
        _line(
            kind="feed_health",
            ts=health_ts,
            department="estate",
            subject="aggregate",
            event=False,
            data={
                "malformed": malformed,
                "projection_status": projection["status"],
                "projection_reason": projection["reason"],
                "rollup_age_s": projection["age_s"],
                "rollup_max_age_s": rollup_max_age_seconds,
                "rollup_updated_at": projection["updated_at"],
            },
        )
    )
    content = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in feed
    )
    _atomic_write(output_path, content)
    history_root = (
        Path(history_dir)
        if history_dir is not None
        else repo_root / "estate" / "state" / "history"
    )
    history_path = history_root / f"{now_dt.date().isoformat()}.json"
    history = _history_snapshot(feed, now_dt.date().isoformat())
    if projection["status"] == "fresh":
        _atomic_write(
            history_path,
            json.dumps(history, sort_keys=True, separators=(",", ":")) + "\n",
        )
    elif not history_path.exists():
        history["projection_status"] = projection["status"]
        history["projection_reason"] = projection["reason"]
        _atomic_write(
            history_path,
            json.dumps(history, sort_keys=True, separators=(",", ":")) + "\n",
        )
    return {
        "departments": len(names),
        "lines": len(feed),
        "loops": loops,
        "malformed": malformed,
        "history": str(history_path),
        "projection_status": projection["status"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the estate board feed")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--department", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument("--timers-path", default=None)
    parser.add_argument("--history-dir", default=None)
    parser.add_argument("--rollup-path", default=None)
    parser.add_argument(
        "--rollup-max-age-seconds",
        type=int,
        default=DEFAULT_ROLLUP_MAX_AGE_SECONDS,
    )
    args = parser.parse_args(argv)
    try:
        receipt = build_feed(
            args.repo_root,
            out=args.out,
            department=args.department,
            now=args.now,
            timers_path=args.timers_path,
            history_dir=args.history_dir,
            rollup_path=args.rollup_path,
            rollup_max_age_seconds=args.rollup_max_age_seconds,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
