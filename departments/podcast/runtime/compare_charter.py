"""Classify watchdog observations through a charter-backed state machine."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.charter_loader import load_charter
from departments.podcast.runtime import record as record_node


DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "podcast" / "state"
DEFAULT_CHARTER_PATH = REPO_ROOT / "departments" / "podcast" / "charter.yaml"

FAILURE_CLASSES = {
    ("timer", "fail"): ("timer_failed", "high"),
    ("receipt", "fail"): ("receipt_stale", "high"),
    ("log", "fail"): ("log_error", "high"),
    ("channel", "fail"): ("channel_failed", "critical"),
    ("vps", "fail"): ("vps_service_failed", "critical"),
    ("timer", "unknown"): ("timer_unknown", "med"),
    ("receipt", "unknown"): ("receipt_unknown", "med"),
    ("log", "unknown"): ("log_unknown", "med"),
    ("channel", "unknown"): ("channel_unknown", "med"),
    ("vps", "unknown"): ("vps_unknown", "med"),
    ("timer", "warn"): ("timer_warning", "low"),
    ("receipt", "warn"): ("receipt_warning", "low"),
    ("log", "warn"): ("log_warning", "low"),
    ("channel", "warn"): ("channel_warning", "med"),
    ("vps", "warn"): ("vps_warning", "med"),
}

QUESTIONS = {
    "timer": "Should the owner repair this timer or retire it from the estate inventory?",
    "receipt": "What blocked this unit from producing a fresh execution receipt?",
    "log": "Which versioned repair playbook should handle this logged failure?",
    "channel": "Which owner-approved path should restore escalation-channel liveness?",
    "vps": "Should the VPS service be repaired through an approved playbook or escalated?",
}


def load_observations(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"observation line {line_number} is not an object")
        rows.append(value)
    return rows


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


def _latest_by_subject(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in observations:
        subject = str(row.get("subject", ""))
        current = latest.get(subject)
        if current is None or str(row.get("ts", "")) >= str(current.get("ts", "")):
            latest[subject] = row
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
    for row in _latest_by_subject(observations):
        sensor = row.get("sensor")
        status = row.get("status")
        if sensor == "receipt":
            limit = _receipt_limit_minutes(row, setpoints)
            age = (row.get("metrics") or {}).get("receipt_age_minutes")
            if isinstance(limit, (int, float)) and isinstance(age, (int, float)) and age > limit:
                status = "fail"
        if status == "ok":
            continue
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
            }
        )
    return candidates


def write_candidates(path: str | Path, candidates: list[dict[str, Any]]) -> None:
    record_node.atomic_write_json(Path(path), candidates)


def run_compare(
    state_dir: str | Path,
    *,
    charter_path: str | Path = DEFAULT_CHARTER_PATH,
    shadow: bool = True,
) -> list[dict[str, Any]]:
    state_dir = Path(state_dir)
    charter = load_charter(charter_path, expect_department="podcast")
    observations = load_observations(state_dir / "observations.jsonl")
    candidates = compare_observations(observations, charter)
    write_candidates(state_dir / "incident_candidates.json", candidates)
    record_node.write_record(
        state_dir,
        "compare_charter",
        {"observations_compared": len(_latest_by_subject(observations)), "candidates": len(candidates)},
        shadow=shadow,
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
