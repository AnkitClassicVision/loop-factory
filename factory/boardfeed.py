"""Build the deterministic estate board feed from department-owned records.

The aggregator is deliberately pull-only: department state is opened only for
reading, and the sole write is the atomically replaced estate feed.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from factory.charter_loader import CharterError, load_charter
from factory.runrecord import validate_record


LOGGER = logging.getLogger(__name__)
UNKNOWN = "unknown"
ALLOWED_AUTH_CLASSES = frozenset({"oauth_cli", "service_oauth", "local_model"})
OPEN_APPROVAL_STATUSES = frozenset({"pending_approval", "pending", "open", "queued"})
TIMER_SNAPSHOT_SCHEMA = "timers-snapshot/v1"
TIMER_RESULTS = frozenset({"success", "failure", UNKNOWN})


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


def _load_runs(path: Path) -> tuple[list[dict[str, Any]], int, bool]:
    existed = path.exists()
    rows, malformed = _read_jsonl(path)
    valid: list[dict[str, Any]] = []
    for row in rows:
        try:
            record = validate_record(row)
        except (KeyError, TypeError, ValueError):
            malformed += 1
            LOGGER.warning("invalid run-record row: %s", path)
            continue
        if _parse_ts(record.get("ts")) is None:
            malformed += 1
            LOGGER.warning("run-record row has invalid timestamp: %s", path)
            continue
        valid.append(record)
    return valid, malformed, existed


def _count(value: Any) -> int | str:
    if isinstance(value, bool):
        return UNKNOWN
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return len(value)
    return UNKNOWN


def _scalar(value: Any) -> Any:
    if value is None:
        return UNKNOWN
    if isinstance(value, (str, int, float, bool)):
        return value
    return UNKNOWN


def _status_line(
    department: str,
    state: dict[str, Any] | None,
    heartbeat: dict[str, Any] | None,
) -> dict[str, Any]:
    state = state or {}
    heartbeat = heartbeat or {}
    status_ts = state.get("last_cycle_at")
    if _parse_ts(status_ts) is None:
        status_ts = heartbeat.get("ts")
    if _parse_ts(status_ts) is None:
        status_ts = UNKNOWN
    findings = state.get("open_findings")
    return _line(
        kind="dept_status",
        ts=status_ts,
        department=department,
        subject="status",
        event=False,
        data={
            "autonomy_state": _scalar(state.get("autonomy_state")),
            "epoch": _scalar(state.get("epoch")),
            "last_cycle_at": _scalar(state.get("last_cycle_at")),
            "ok": _scalar(heartbeat.get("ok")),
            "open_findings": len(findings) if isinstance(findings, list) else UNKNOWN,
            "escalations": _count(state.get("escalations", heartbeat.get("escalations"))),
        },
    )


def _state_andons(
    department: str, state: dict[str, Any] | None, fallback_ts: str
) -> list[dict[str, Any]]:
    findings = (state or {}).get("open_findings")
    if not isinstance(findings, list):
        return []
    output: list[dict[str, Any]] = []
    seen: defaultdict[str, int] = defaultdict(int)
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        code = str(finding.get("code", UNKNOWN))
        subject = str(finding.get("fingerprint") or code)
        ordinal = seen[subject]
        seen[subject] += 1
        if ordinal:
            subject = f"{subject}-{ordinal}"
        ts = finding.get("ts") or finding.get("observed_at") or fallback_ts
        if _parse_ts(ts) is None:
            ts = fallback_ts
        output.append(
            _line(
                kind="andon",
                ts=ts,
                department=department,
                subject=subject,
                event=True,
                data={
                    "severity": _scalar(finding.get("severity")),
                    "code": code,
                    "detail": _scalar(finding.get("detail")),
                    "observed": _scalar(finding.get("observed")),
                    "setpoint": _scalar(finding.get("setpoint")),
                },
            )
        )
    return output


def _run_andons(department: str, records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for record in records:
        ts = _iso(_parse_ts(record["ts"]))  # validated by _load_runs
        run_id = record["run_id"]
        if record.get("status") == "blocked" and record.get("auth_class") == "blocked":
            output.append(
                _line(
                    kind="andon",
                    ts=ts,
                    department=department,
                    subject=f"AUTH-{run_id}",
                    event=True,
                    data={
                        "severity": "breach",
                        "code": "AUTH",
                        "detail": "authentication lane blocked",
                        "observed": "blocked",
                        "setpoint": "authorized subscription lane",
                        "run_id": run_id,
                    },
                )
            )
        if record.get("metered_violation"):
            output.append(
                _line(
                    kind="andon",
                    ts=ts,
                    department=department,
                    subject=f"POLICY-{run_id}",
                    event=True,
                    data={
                        "severity": "breach",
                        "code": "POLICY",
                        "detail": "metered model lane is forbidden",
                        "observed": "metered_forbidden",
                        "setpoint": "subscription_oauth_only",
                        "run_id": run_id,
                    },
                )
            )
    return output


def _active_runs(
    department: str, records: Iterable[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    lower = now - timedelta(hours=24)
    output: list[dict[str, Any]] = []
    for record in records:
        record_dt = _parse_ts(record["ts"])
        if record_dt is None or not (lower <= record_dt <= now):
            continue
        ts = _iso(record_dt)
        output.append(
            _line(
                kind="active_run",
                ts=ts,
                department=department,
                subject=record["run_id"],
                event=True,
                data={
                    "run_id": record["run_id"],
                    "node": record["node"],
                    "status": record["status"],
                    "attempt": record["attempt"],
                    "engine": _scalar(record.get("engine")),
                    "model": _scalar(record.get("model")),
                    "ts": ts,
                },
            )
        )
    return output


def _sum_measurement(records: list[dict[str, Any]], parent: str, field: str) -> int | str:
    values: list[int] = []
    for record in records:
        container = record.get(parent)
        if container is None:
            # A script node made zero model calls — that is a measured zero,
            # not an unknown; it must not poison the department's sums.
            continue
        value = container.get(field) if isinstance(container, dict) else None
        if isinstance(value, bool) or not isinstance(value, int):
            return UNKNOWN
        values.append(value)
    return sum(values)


def _daily_metrics(
    department: str,
    records: list[dict[str, Any]],
    now: datetime,
    source_exists: bool,
) -> list[dict[str, Any]]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily = [record for record in records if start <= _parse_ts(record["ts"]) <= now]
    # Script nodes carry auth_class None and MUST count as runs; only
    # policy-violating records are excluded from stats (they become andons).
    clean = [
        record
        for record in daily
        if not record.get("metered_violation")
        and (
            record.get("auth_class") is None
            or record.get("auth_class") in ALLOWED_AUTH_CLASSES
        )
    ]
    period = start.date().isoformat()
    ts = _iso(now)
    if source_exists:
        summary_data: dict[str, Any] = {
            "metric_type": "daily_rollup",
            "period": period,
            "runs": len(clean),
            "ok": sum(record["status"] == "ok" for record in clean),
            "error": sum(record["status"] == "error" for record in clean),
            "blocked": sum(record["status"] == "blocked" for record in clean),
            "tokens_in": _sum_measurement(clean, "usage", "input_tokens"),
            "tokens_out": _sum_measurement(clean, "usage", "output_tokens"),
            "model_calls": _sum_measurement(clean, "cost", "model_calls"),
        }
    else:
        summary_data = {
            "metric_type": "daily_rollup",
            "period": period,
            "runs": UNKNOWN,
            "ok": UNKNOWN,
            "error": UNKNOWN,
            "blocked": UNKNOWN,
            "tokens_in": UNKNOWN,
            "tokens_out": UNKNOWN,
            "model_calls": UNKNOWN,
        }
    output = [
        _line(
            kind="metrics",
            ts=ts,
            department=department,
            subject=f"daily-{period}",
            event=False,
            data=summary_data,
        )
    ]

    groups: defaultdict[tuple[Any, Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for record in clean:
        # Lane telemetry describes model-calling lanes only; script nodes
        # (engine None) count in the rollup above but have no lane row.
        if record.get("engine") is None:
            continue
        groups[(record.get("engine"), record.get("model"), record.get("auth_class"))].append(record)
    for (engine, model, auth_class), lane_records in sorted(
        groups.items(), key=lambda item: tuple("" if value is None else str(value) for value in item[0])
    ):
        subject = "lane-" + "-".join(
            str(value) if value is not None else UNKNOWN for value in (engine, model, auth_class)
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
                    "auth_class": _scalar(auth_class),
                    "calls": _sum_measurement(lane_records, "cost", "model_calls"),
                    "tokens_in": _sum_measurement(lane_records, "usage", "input_tokens"),
                    "tokens_out": _sum_measurement(lane_records, "usage", "output_tokens"),
                    "period": period,
                },
            )
        )
    return output


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


def _approvals(
    department: str, rows: Iterable[dict[str, Any]], now: datetime
) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    malformed = 0
    for index, row in enumerate(rows):
        if row.get("status") not in OPEN_APPROVAL_STATUSES:
            continue
        queued_dt = _parse_ts(row.get("queued_at"))
        if queued_dt is None:
            malformed += 1
            continue
        queued_at = _iso(queued_dt)
        subject = row.get("card_ref") or row.get("decision_id") or row.get("id") or f"row-{index}"
        data = {
            "status": row["status"],
            "queued_at": queued_at,
            "age_s": max(0, int((now - queued_dt).total_seconds())),
        }
        if row.get("card_ref") is not None:
            data["card_ref"] = _scalar(row.get("card_ref"))
        output.append(
            _line(
                kind="approval",
                ts=queued_at,
                department=department,
                subject=str(subject),
                event=True,
                data=data,
            )
        )
    return output, malformed


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


def build_feed(
    repo_root: str | Path,
    *,
    out: str | Path | None = None,
    department: str | None = None,
    now: str | datetime | None = None,
    timers_path: str | Path | None = None,
) -> dict[str, Any]:
    """Aggregate department records, atomically write the feed, and return its receipt."""
    repo_root = Path(repo_root)
    output_path = Path(out) if out is not None else repo_root / "estate" / "state" / "board-feed.ndjson"
    now_dt = _now(now)
    feed: list[dict[str, Any]] = []
    malformed = 0
    department_dirs = _departments(repo_root, department)

    for department_dir in department_dirs:
        name = department_dir.name
        state_dir = department_dir / "state"
        state, count = _read_json(state_dir / "STATE.json")
        malformed += count
        heartbeat_rows, count = _read_jsonl(state_dir / "heartbeats.jsonl")
        malformed += count
        heartbeat = heartbeat_rows[-1] if heartbeat_rows else None
        records, count, runs_exist = _load_runs(state_dir / "runs-v2.jsonl")
        malformed += count
        approval_rows, count = _read_jsonl(state_dir / "approval_queue.jsonl")
        malformed += count
        charter, count = _load_charter(department_dir / "charter.yaml", name)
        malformed += count

        status = _status_line(name, state, heartbeat)
        feed.append(status)
        feed.extend(_state_andons(name, state, status["ts"]))
        feed.extend(_run_andons(name, records))
        feed.extend(_active_runs(name, records, now_dt))
        approvals, count = _approvals(name, approval_rows, now_dt)
        malformed += count
        feed.extend(approvals)
        feed.extend(_daily_metrics(name, records, now_dt, runs_exist))
        feed.extend(_objective_metrics(name, charter, now_dt))

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
            data={"malformed": malformed},
        )
    )
    content = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in feed
    )
    _atomic_write(output_path, content)
    return {
        "departments": len(department_dirs),
        "lines": len(feed),
        "loops": loops,
        "malformed": malformed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the estate board feed")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--department", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument("--timers-path", default=None)
    args = parser.parse_args(argv)
    try:
        receipt = build_feed(
            args.repo_root,
            out=args.out,
            department=args.department,
            now=args.now,
            timers_path=args.timers_path,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
