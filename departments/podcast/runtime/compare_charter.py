"""Classify watchdog observations through a charter-backed state machine."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import runrecord
from factory.charter_loader import load_charter
from departments.podcast.runtime import record as record_node


DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "podcast" / "state"
DEFAULT_CHARTER_PATH = REPO_ROOT / "departments" / "podcast" / "charter.yaml"
LOGGER = logging.getLogger(__name__)

FAILURE_CLASSES = {
    # dag_supervisor alarms when the pipeline's hashed DAG projection shows a
    # silent skip, forged/invalid skip artifact, hash mismatch, or a stale or
    # missing projection — every one is a never-skip contract violation.
    ("dag_supervisor", "alarm"): ("dag_receipt_violation", "critical"),
    ("timer", "fail"): ("timer_failed", "high"),
    ("receipt", "fail"): ("receipt_stale", "high"),
    ("log", "fail"): ("log_error", "high"),
    ("channel", "fail"): ("channel_failed", "critical"),
    ("vps", "fail"): ("vps_service_failed", "critical"),
    ("timer", "unknown"): ("timer_unknown", "med"),
    # A ledger lane the watchdog cannot see is blindness, not health
    # (the 8af90d8 ledger-sensor fix exists because every lane was blind);
    # surfaced 2026-07-31 when the first full daily run hit an unmapped
    # ("ledger", "unknown") observation and correctly refused to continue.
    ("ledger", "unknown"): ("ledger_blind", "high"),
    # Recurred 2026-08-05 when unavailable publish-reliability evidence emitted
    # an unmapped ("hopper", "unknown") observation and stopped the daily chain.
    ("hopper", "unknown"): ("hopper_blind", "high"),
    ("funnel", "alarm"): ("funnel_behind", "high"),
    ("funnel", "unknown"): ("funnel_blind", "med"),
    ("ledger", "fail"): ("ledger_failed", "high"),
    ("receipt", "unknown"): ("receipt_unknown", "med"),
    ("log", "unknown"): ("log_unknown", "med"),
    ("channel", "unknown"): ("channel_unknown", "med"),
    ("vps", "unknown"): ("vps_unknown", "med"),
    ("timer", "warn"): ("timer_warning", "low"),
    ("receipt", "warn"): ("receipt_warning", "low"),
    ("log", "warn"): ("log_warning", "low"),
    ("channel", "warn"): ("channel_warning", "med"),
    ("vps", "warn"): ("vps_warning", "med"),
    ("pipeline", "fail"): ("pipeline_below_target", "high"),
    ("pipeline", "warn"): ("pipeline_warn", "med"),
    ("pipeline", "unknown"): ("pipeline_unknown", "med"),
    ("publishday", "fail"): ("publish_missing", "high"),
    ("publishday", "unknown"): ("publish_unknown", "med"),
    ("manifest", "fail"): ("manifest_incomplete", "high"),
    ("manifest", "warn"): ("manifest_gap", "med"),
    ("manifest", "unknown"): ("manifest_unknown", "med"),
}

FAILURE_HINT_CLASSES = {
    ("receipt", "fail", "receipt_hollow"): ("receipt_hollow", "high"),
}

MEANINGS = {
    "dag_receipt_violation": {
        "what_it_means": "A required podcast step was skipped or its proof of completion cannot be trusted.",
        "what_it_needs": "Ops must repair the skipped step and confirm who approved any intentional skip; nothing needed from you unless approval is disputed.",
    },
    "timer_failed": {
        "what_it_means": "A scheduled podcast job tried to run and failed.",
        "what_it_needs": "Ops must repair or retire the scheduled job; you only need to decide if the job should no longer run.",
    },
    "receipt_stale": {
        "what_it_means": "A podcast job has not recently proven that it finished successfully.",
        "what_it_needs": "Ops must check the job and restore a fresh completion record; nothing needed from you unless the job should be retired.",
    },
    "log_error": {
        "what_it_means": "A podcast job recorded an error while running.",
        "what_it_needs": "Ops must use the approved repair steps or bring you a specific decision if none apply.",
    },
    "channel_failed": {
        "what_it_means": "The podcast alert route is not working, so important problems may not reach you.",
        "what_it_needs": "Ops must restore the approved alert route; nothing needed from you unless a different route is required.",
    },
    "vps_service_failed": {
        "what_it_means": "A podcast service on the hosted server is down.",
        "what_it_needs": "Ops must repair the service through an approved playbook or ask you to approve a different recovery path.",
    },
    "timer_unknown": {
        "what_it_means": "The system cannot tell whether a scheduled podcast job is working.",
        "what_it_needs": "Ops must restore the job status check; you only need to decide if the job should be retired.",
    },
    "ledger_blind": {
        "what_it_means": "The system cannot read the record that shows whether podcast messages were sent.",
        "what_it_needs": "Ops must restore that send record; nothing needed from you unless the sending process should change.",
    },
    "hopper_blind": {
        "what_it_means": "The watchdog gauge that checks episodes are publishing on time has no data to read; the podcast itself may be fine, but this gauge is blind.",
        "what_it_needs": "An ops repair of the publish-schedule data file; nothing needed from you unless you want it handled differently.",
    },
    "funnel_behind": {
        "what_it_means": "The guest pipeline fell behind its daily numbers, so future episodes are at risk of running dry.",
        "what_it_needs": "Approve the catch-up actions the loops propose, or tell them to pause the pace; the daily work order has the exact items.",
    },
    "funnel_blind": {
        "what_it_means": "The daily guest pipeline count has no trustworthy information to read, so it cannot tell whether future episodes are on track.",
        "what_it_needs": "Ops must restore the guest and booking records; nothing needed from you unless the tracking process should change.",
    },
    "ledger_failed": {
        "what_it_means": "The record of podcast message sends shows a failure.",
        "what_it_needs": "Ops must repair the failed send path and confirm the affected message status; nothing needed from you unless a resend needs approval.",
    },
    "receipt_unknown": {
        "what_it_means": "The system cannot tell whether a podcast job finished successfully.",
        "what_it_needs": "Ops must restore the job's completion record; nothing needed from you unless the job should be retired.",
    },
    "log_unknown": {
        "what_it_means": "The system cannot read the podcast job's error record.",
        "what_it_needs": "Ops must restore access to the job record; nothing needed from you unless the record location should change.",
    },
    "channel_unknown": {
        "what_it_means": "The system cannot confirm that podcast alerts can reach you.",
        "what_it_needs": "Ops must test and restore the approved alert route; nothing needed from you unless a new route is required.",
    },
    "vps_unknown": {
        "what_it_means": "The system cannot tell whether a podcast service on the hosted server is running.",
        "what_it_needs": "Ops must restore the service status check; nothing needed from you unless server access is required.",
    },
    "timer_warning": {
        "what_it_means": "A scheduled podcast job is showing an early warning but has not fully failed.",
        "what_it_needs": "Ops should inspect the job before its next run; nothing needed from you unless it should be retired.",
    },
    "receipt_warning": {
        "what_it_means": "A podcast job's proof of completion is getting old and may soon be overdue.",
        "what_it_needs": "Ops should confirm the next successful run updates the completion record; nothing needed from you.",
    },
    "log_warning": {
        "what_it_means": "A podcast job recorded a warning that may become a failure.",
        "what_it_needs": "Ops should inspect the warning and apply the approved repair if needed; nothing needed from you.",
    },
    "channel_warning": {
        "what_it_means": "The podcast alert route is showing signs it may stop working.",
        "what_it_needs": "Ops should test and repair the alert route; nothing needed from you unless a different route is required.",
    },
    "vps_warning": {
        "what_it_means": "A podcast service on the hosted server is showing signs it may fail.",
        "what_it_needs": "Ops should inspect and repair the service; nothing needed from you unless a different recovery path needs approval.",
    },
    "pipeline_below_target": {
        "what_it_means": "There are too few confirmed podcast guests ready for recording.",
        "what_it_needs": "The podcast team must identify and clear the missing guest work; you only need to help if an owner decision is blocking them.",
    },
    "pipeline_warn": {
        "what_it_means": "The number of confirmed podcast guests is close to falling below target.",
        "what_it_needs": "The podcast team should fill the guest gaps before they affect recording; nothing needed from you unless they flag a decision.",
    },
    "pipeline_unknown": {
        "what_it_means": "The system cannot count how many podcast guests are ready for recording.",
        "what_it_needs": "Ops must restore the guest-status data; nothing needed from you unless the tracking process should change.",
    },
    "publish_missing": {
        "what_it_means": "Something required for today's podcast release is missing.",
        "what_it_needs": "The publishing owner must restore the missing item or tell you exactly what decision is blocking release.",
    },
    "publish_unknown": {
        "what_it_means": "The system cannot confirm whether today's podcast release is complete.",
        "what_it_needs": "Ops must restore the release check; nothing needed from you unless the publishing owner reports a real release problem.",
    },
    "manifest_incomplete": {
        "what_it_means": "Required guest details are missing before the episode can be published safely.",
        "what_it_needs": "The podcast team must complete the missing guest details before publishing; nothing needed from you unless they cannot obtain them.",
    },
    "manifest_gap": {
        "what_it_means": "Some guest details may be incomplete ahead of publishing.",
        "what_it_needs": "The podcast team should complete the guest details before publish day; nothing needed from you unless they flag a blocker.",
    },
    "manifest_unknown": {
        "what_it_means": "The system cannot confirm whether all required guest details are complete.",
        "what_it_needs": "Ops must restore the guest-details check; nothing needed from you unless the podcast team reports missing information.",
    },
    "receipt_hollow": {
        "what_it_means": "A podcast job created an empty completion record that does not prove the work finished.",
        "what_it_needs": "Ops must rerun or repair the job so it produces a complete record; nothing needed from you unless the job itself should change.",
    },
}

QUESTIONS = {
    "dag_supervisor": "Which episode step skipped without an authorized skip artifact, and who authorizes or repairs it?",
    "ledger": "Which send-lane ledger is missing or unreadable, and what restores watchdog visibility into it?",
    "hopper": "Hopper/publish-reliability evidence is unavailable — what broke the publish schedule source or the publish-day verifier?",
    "funnel": "Which guest pipeline number missed its line, and what catch-up action will restore it?",
    "timer": "Should the owner repair this timer or retire it from the estate inventory?",
    "receipt": "What blocked this unit from producing a fresh execution receipt?",
    "log": "Which versioned repair playbook should handle this logged failure?",
    "channel": "Which owner-approved path should restore escalation-channel liveness?",
    "vps": "Should the VPS service be repaired through an approved playbook or escalated?",
    "pipeline": "Which unresolved or missing guest evidence is keeping the pipeline below target?",
    "publishday": "Which expected publish artifact is missing, and who owns its recovery?",
    "manifest": "Which required guest-manifest fields must be completed before publish?",
}


class ObservationEvidenceError(RuntimeError):
    """Raised when the comparison input is missing, unreadable, or hollow."""


def load_observations(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise ObservationEvidenceError(f"observations evidence is missing: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ObservationEvidenceError(
            f"observations evidence is unreadable: {path}: {exc}"
        ) from exc
    rows = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ObservationEvidenceError(
                f"observations evidence is malformed at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise ObservationEvidenceError(
                f"observations evidence is malformed at {path}:{line_number}: "
                "row is not an object"
            )
        rows.append(value)
    if not rows:
        raise ObservationEvidenceError(f"observations evidence contains no rows: {path}")
    return rows


def _evidence_missing_candidate(path: Path, detail: str) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "sensor": "evidence",
        "subject": "observations.jsonl",
        "failure_class": "evidence_missing",
        "severity": "high",
        "setpoint": "readable, nonempty observations.jsonl required",
        "observed": detail,
        "evidence": [str(path)],
        "one_question": "What removed or made the watchdog observation evidence unreadable?",
    }


def charter_setpoints(charter: dict[str, Any]) -> dict[str, Any]:
    """Extract only ratified watchdog numbers from the loaded charter."""
    operational = (charter.get("setpoints") or {}).get("operational") or {}
    outcome = (charter.get("setpoints") or {}).get("outcome") or {}
    additional = (charter.get("setpoints") or {}).get("outcome_additional") or []
    values: dict[str, Any] = {
        operational.get("metric", "operational"): operational.get("target"),
        outcome.get("metric", "outcome"): outcome.get("target"),
    }
    for row in additional:
        if isinstance(row, dict) and row.get("metric"):
            values[row["metric"]] = row.get("target")
    return values


def _latest_by_sensor_subject(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in observations:
        key = (str(row.get("sensor", "")), str(row.get("subject", "")))
        current = latest.get(key)
        if current is None or str(row.get("ts", "")) >= str(current.get("ts", "")):
            latest[key] = row
    return [latest[key] for key in sorted(latest)]


def _receipt_limit_minutes(row: dict[str, Any], setpoints: dict[str, Any]) -> Any:
    metrics = row.get("metrics") or {}
    cadence = metrics.get("expected_cadence")
    if cadence == "15min":
        return setpoints.get("silent_failure_detection_latency_minutes")
    if cadence == "daily":
        hours = setpoints.get("detection_latency_daily_loops_hours")
        return hours * 60 if isinstance(hours, (int, float)) else None
    return metrics.get("stale_after_minutes")


def _setpoint_for(row: dict[str, Any], setpoints: dict[str, Any]) -> str:
    sensor = row["sensor"]
    metrics = row.get("metrics") or {}
    if sensor == "receipt":
        threshold = _receipt_limit_minutes(row, setpoints)
        return f"receipt age <= {threshold} minutes" if threshold is not None else "fresh receipt required"
    if sensor == "timer":
        return "ActiveState=active, Result=success, ExecMainStatus=0"
    if sensor == "channel":
        ceiling = setpoints.get("escalation_pings_per_day")
        return f"configured escalation channel; <= {ceiling} pings/day"
    if sensor == "vps":
        return "service state observable and active"
    if sensor == "pipeline":
        return f"recording pipeline guests >= {setpoints.get('pipeline_guests')}"
    if sensor == "publishday":
        return "all due publish artifacts verified"
    if sensor == "manifest":
        return "required guest manifest complete by publish"
    return "no error pattern in the inspected log tail"


def _observed_for(row: dict[str, Any]) -> Any:
    metrics = row.get("metrics") or {}
    sensor = row["sensor"]
    if sensor == "receipt":
        return metrics.get("receipt_age_minutes", row.get("status"))
    if sensor == "timer":
        return {
            "ActiveState": metrics.get("active_state"),
            "SubState": metrics.get("sub_state"),
            "Result": metrics.get("result"),
            "ExecMainStatus": metrics.get("exec_main_status"),
        }
    if sensor == "channel":
        return {
            "config_present": metrics.get("config_present"),
            "reachability_checked": metrics.get("reachability_checked"),
        }
    return row.get("status")


def compare_observations(
    observations: list[dict[str, Any]], charter: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return candidates from the finite sensor/status transition table."""
    setpoints = charter_setpoints(charter)
    candidates: list[dict[str, Any]] = []
    for row in _latest_by_sensor_subject(observations):
        sensor = row.get("sensor")
        status = row.get("status")
        if sensor == "receipt":
            limit = _receipt_limit_minutes(row, setpoints)
            age = (row.get("metrics") or {}).get("receipt_age_minutes")
            if isinstance(limit, (int, float)) and isinstance(age, (int, float)) and age > limit:
                status = "fail"
        if status == "ok":
            continue
        failure_hint = (row.get("metrics") or {}).get("failure_hint")
        transition = FAILURE_HINT_CLASSES.get((sensor, status, failure_hint))
        if transition is None:
            transition = FAILURE_CLASSES.get((sensor, status))
        if transition is None:
            raise ValueError(f"no charter comparison transition for sensor={sensor!r}, status={status!r}")
        failure_class, severity = transition
        candidates.append(
            {
                "ts": row.get("ts"),
                "sensor": sensor,
                "subject": row.get("subject"),
                "failure_class": failure_class,
                "severity": severity,
                "setpoint": _setpoint_for(row, setpoints),
                "observed": _observed_for(row),
                "evidence": [str(row.get("evidence", ""))],
                "one_question": QUESTIONS[sensor],
                **MEANINGS[failure_class],
            }
        )
    return candidates


def write_candidates(path: str | Path, candidates: list[dict[str, Any]]) -> None:
    record_node.atomic_write_json(Path(path), candidates)


def _run_compare(
    state_dir: str | Path,
    *,
    charter_path: str | Path = DEFAULT_CHARTER_PATH,
    shadow: bool = True,
) -> list[dict[str, Any]]:
    state_dir = Path(state_dir)
    charter = load_charter(charter_path, expect_department="podcast")
    observations_path = state_dir / "observations.jsonl"
    try:
        observations = load_observations(observations_path)
    except ObservationEvidenceError as exc:
        observations = []
        candidates = [_evidence_missing_candidate(observations_path, str(exc))]
    else:
        candidates = compare_observations(observations, charter)
    write_candidates(state_dir / "incident_candidates.json", candidates)
    record_node.write_record(
        state_dir,
        "compare_charter",
        {
            "observations_compared": len(
                _latest_by_sensor_subject(observations)
            ),
            "candidates": len(candidates),
        },
        shadow=shadow,
    )
    return candidates


def _emit_run_record(
    state_dir: Path,
    *,
    started: float,
    status: str,
    errors: list[str],
) -> None:
    artifacts = [
        state_dir / "incident_candidates.json",
        state_dir / "runs.jsonl",
    ]
    try:
        runrecord.emit_record(
            state_dir,
            department="podcast",
            node="compare_charter",
            status=status,
            release=runrecord.read_release(state_dir.parent),
            trigger={
                "kind": "time",
                "id": "podcast-daily",
                "dedupe_key": (
                    f"{datetime.now(timezone.utc).date().isoformat()}-compare_charter"
                ),
            },
            duration_ms=int((time.perf_counter() - started) * 1000),
            errors=errors,
            artifacts=[str(path) for path in artifacts if path.exists()],
            external_actions_taken=0,
        )
    except Exception:
        LOGGER.exception("compare_charter failed to append its runs-v2 record")
        raise


def run_compare(
    state_dir: str | Path,
    *,
    charter_path: str | Path = DEFAULT_CHARTER_PATH,
    shadow: bool = True,
) -> list[dict[str, Any]]:
    state_path = Path(state_dir)
    started = time.perf_counter()
    try:
        candidates = _run_compare(
            state_path,
            charter_path=charter_path,
            shadow=shadow,
        )
    except Exception as exc:
        _emit_run_record(
            state_path,
            started=started,
            status="error",
            errors=[type(exc).__name__],
        )
        raise
    errors = [str(candidate["failure_class"]) for candidate in candidates]
    _emit_run_record(
        state_path,
        started=started,
        status="error" if errors else "ok",
        errors=errors,
    )
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare watchdog observations to the charter")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--charter", default=str(DEFAULT_CHARTER_PATH))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", dest="shadow", action="store_true", default=True)
    mode.add_argument("--live", dest="shadow", action="store_false")
    args = parser.parse_args()
    candidates = run_compare(args.state_dir, charter_path=args.charter, shadow=args.shadow)
    print(json.dumps({"candidates": len(candidates), "shadow": args.shadow}))


if __name__ == "__main__":
    main()
