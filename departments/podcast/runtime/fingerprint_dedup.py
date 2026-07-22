"""Fingerprint incident candidates and preserve one thread per failure."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from departments.podcast.runtime import record as record_node


DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "podcast" / "state"
DEFAULT_ESTATE_PATH = Path(__file__).with_name("estate.json")
SEVERITY_RANK = {"low": 0, "med": 1, "high": 2, "critical": 3}
STATE_RANK = {"resolved": 0, "open": 1, "department_defect": 2}


def fingerprint(sensor: str, subject: str) -> str:
    material = f"{sensor}|{subject}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:12]


def _legacy_fingerprint(sensor: str, subject: str, failure_class: str) -> str:
    material = f"{sensor}|{subject}|{failure_class}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:12]


def load_json(path: str | Path, default: Any) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _recent_evidence(*groups: list[Any]) -> list[str]:
    """Return the ten most recent unique non-empty evidence entries."""
    newest_first: list[str] = []
    seen: set[str] = set()
    combined = [value for group in groups for value in group]
    for value in reversed(combined):
        normalized = str(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        newest_first.append(normalized)
        if len(newest_first) == 10:
            break
    return list(reversed(newest_first))


def _timestamp_key(value: Any) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _collapse_incident_group(
    key: str, variants: list[dict[str, Any]]
) -> dict[str, Any]:
    chronological = sorted(
        variants, key=lambda row: _timestamp_key(row.get("last_seen", ""))
    )
    newest = dict(chronological[-1])
    earliest = min(
        variants,
        key=lambda row: _timestamp_key(
            row.get("first_seen") or row.get("last_seen") or ""
        ),
    )
    latest = max(
        variants,
        key=lambda row: _timestamp_key(
            row.get("last_seen") or row.get("first_seen") or ""
        ),
    )
    first_seen = str(earliest.get("first_seen") or earliest.get("last_seen") or "")
    last_seen = str(latest.get("last_seen") or latest.get("first_seen") or "")
    severity = max(
        (str(row.get("severity", "low")) for row in variants),
        key=lambda value: SEVERITY_RANK.get(value, -1),
    )
    state = max(
        (str(row.get("state", "resolved")) for row in variants),
        key=lambda value: STATE_RANK.get(value, -1),
    )
    escalated_at_values = sorted(
        str(row["escalated_at"])
        for row in variants
        if row.get("escalated_at")
    )
    escalated_defect_at_values = sorted(
        str(row["escalated_defect_at"])
        for row in variants
        if row.get("escalated_defect_at")
    )
    newest.update(
        {
            "fingerprint": key,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "severity": severity,
            "state": state,
            "evidence": _recent_evidence(
                *(list(row.get("evidence", [])) for row in chronological)
            ),
            "count": sum(int(row.get("count", 1)) for row in variants),
            "escalated": any(bool(row.get("escalated")) for row in variants),
            "escalated_at": escalated_at_values[0] if escalated_at_values else None,
            "escalated_defect": any(
                bool(row.get("escalated_defect")) for row in variants
            ),
            "escalated_defect_at": (
                escalated_defect_at_values[0]
                if escalated_defect_at_values
                else None
            ),
            "defect_recurrence_count": max(
                int(row.get("defect_recurrence_count", 0)) for row in variants
            ),
        }
    )
    if state == "department_defect":
        newest["one_question"] = (
            "This resolved fingerprint recurred. What root-cause control failed, "
            "and what permanent department fix is required?"
        )
    return newest


def _estate_identities() -> list[tuple[str, str]]:
    estate = load_json(DEFAULT_ESTATE_PATH, {})
    identities: list[tuple[str, str]] = []
    for timer in estate.get("systemd_user_timers", []):
        subject = str(timer.get("name", ""))
        identities.extend((sensor, subject) for sensor in ("timer", "receipt", "log"))
    identities.extend(
        ("channel", str(channel.get("name", "")))
        for channel in estate.get("channels", [])
    )
    identities.extend(
        ("vps", str(subject))
        for subject in (estate.get("vps") or {}).get("services", [])
    )
    return [(sensor, subject) for sensor, subject in identities if subject]


def _incident_identity(
    stored_key: str,
    incident: dict[str, Any],
    estate_identities: list[tuple[str, str]],
) -> tuple[str, str]:
    if incident.get("sensor") and incident.get("subject"):
        return str(incident["sensor"]), str(incident["subject"])

    failure_class = str(incident.get("failure_class", ""))
    stored_fingerprints = {stored_key, str(incident.get("fingerprint", ""))}
    matches = [
        (sensor, subject)
        for sensor, subject in estate_identities
        if fingerprint(sensor, subject) in stored_fingerprints
        or _legacy_fingerprint(sensor, subject, failure_class) in stored_fingerprints
    ]
    if len(matches) != 1:
        raise ValueError(
            f"incident {stored_key!r} lacks a uniquely recoverable sensor and subject"
        )
    return matches[0]


def load_incidents(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load incidents and atomically migrate old failure-class-based keys."""
    path = Path(path)
    incidents = load_json(path, {})
    if not isinstance(incidents, dict):
        raise ValueError("incidents.json must contain an object keyed by fingerprint")

    estate_identities = _estate_identities()
    groups: dict[str, list[dict[str, Any]]] = {}
    for stored_key, value in incidents.items():
        if not isinstance(value, dict):
            raise ValueError(f"incident {stored_key!r} must be an object")
        sensor, subject = _incident_identity(stored_key, value, estate_identities)
        normalized = {**value, "sensor": sensor, "subject": subject}
        key = fingerprint(sensor, subject)
        groups.setdefault(key, []).append(normalized)

    migrated = {
        key: _collapse_incident_group(key, variants)
        for key, variants in groups.items()
    }
    if migrated != incidents:
        record_node.atomic_write_json(path, migrated)
    return migrated


def merge_candidates(
    candidates: list[dict[str, Any]],
    incidents: dict[str, dict[str, Any]] | None = None,
    *,
    observations: list[dict[str, Any]] | None = None,
    now: str | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Merge candidates without creating duplicate incident threads."""
    merged = dict(incidents or {})
    stats = {"new": 0, "deduplicated": 0, "department_defects": 0}
    timestamp = now or datetime.now(timezone.utc).isoformat()
    for candidate in candidates:
        sensor = str(candidate["sensor"])
        subject = str(candidate["subject"])
        failure_class = str(candidate["failure_class"])
        key = fingerprint(sensor, subject)
        seen_at = str(candidate.get("ts") or timestamp)
        evidence = [
            str(value)
            for value in candidate.get("evidence", [])
            if str(value)
        ]
        current = merged.get(key)
        if current is None:
            merged[key] = {
                "fingerprint": key,
                "sensor": sensor,
                "subject": subject,
                "failure_class": failure_class,
                "first_seen": seen_at,
                "last_seen": seen_at,
                "state": "open",
                "severity": candidate["severity"],
                "setpoint": str(candidate["setpoint"]),
                "observed": candidate.get("observed"),
                "evidence": _recent_evidence(evidence),
                "one_question": str(candidate["one_question"]),
                "count": 1,
                "escalated": False,
                "escalated_at": None,
            }
            stats["new"] += 1
            continue

        current = dict(current)
        current["count"] = int(current.get("count", 0)) + 1
        current["sensor"] = sensor
        current["subject"] = subject
        current["severity"] = max(
            (str(current.get("severity", "low")), str(candidate["severity"])),
            key=lambda value: SEVERITY_RANK.get(value, -1),
        )
        incoming_is_newest = _timestamp_key(seen_at) >= _timestamp_key(
            current.get("last_seen", "")
        )
        if incoming_is_newest:
            current["failure_class"] = failure_class
            current["last_seen"] = seen_at
            current["observed"] = candidate.get("observed")
            current["setpoint"] = str(candidate["setpoint"])
            current["evidence"] = _recent_evidence(
                list(current.get("evidence", [])), evidence
            )
        else:
            current["evidence"] = _recent_evidence(
                evidence, list(current.get("evidence", []))
            )
        if current.get("state") == "resolved":
            current["state"] = "department_defect"
            current["defect_recurrence_count"] = int(
                current.get("defect_recurrence_count", 0)
            ) + 1
            current["escalated_defect"] = False
            current["escalated_defect_at"] = None
            current["one_question"] = (
                "This resolved fingerprint recurred. What root-cause control failed, "
                "and what permanent department fix is required?"
            )
            stats["department_defects"] += 1
        else:
            stats["deduplicated"] += 1
        current["consecutive_healthy"] = 0
        merged[key] = current

    candidate_identities = {
        (str(candidate["sensor"]), str(candidate["subject"]))
        for candidate in candidates
    }
    observation_rows = observations or []
    newest_observation_ts = max(
        (str(row.get("ts", "")) for row in observation_rows),
        key=_timestamp_key,
        default=None,
    )
    current_observations = {
        (str(row.get("sensor", "")), str(row.get("subject", ""))): row
        for row in observation_rows
        if str(row.get("ts", "")) == newest_observation_ts
    }
    for key, stored in list(merged.items()):
        if stored.get("state") not in {"open", "department_defect"}:
            continue
        incident = dict(stored)
        incident.setdefault("consecutive_healthy", 0)
        identity = (str(incident.get("sensor", "")), str(incident.get("subject", "")))
        observation = current_observations.get(identity)
        healthy = (
            identity not in candidate_identities
            and observation is not None
            and observation.get("status") == "ok"
        )
        observation_ts = str(observation.get("ts", "")) if observation else ""
        if healthy and observation_ts != incident.get("last_healthy_observation_at"):
            incident["consecutive_healthy"] = int(
                incident.get("consecutive_healthy", 0)
            ) + 1
            incident["last_healthy_observation_at"] = observation_ts
        elif not healthy:
            incident["consecutive_healthy"] = 0
        if incident["consecutive_healthy"] >= 3:
            incident["state"] = "resolved"
            incident["resolved_at"] = observation_ts
            incident["resolution"] = "observed_healthy_3_cycles"
        merged[key] = incident
    return merged, stats


def run_dedup(state_dir: str | Path, *, shadow: bool = True) -> dict[str, dict[str, Any]]:
    state_dir = Path(state_dir)
    candidates_path = state_dir / "incident_candidates.json"
    candidates_error: str | None = None
    try:
        candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        candidates = None
        candidates_error = f"{type(exc).__name__}: {exc}"

    if candidates_error is not None:
        with record_node.records_lock(state_dir):
            incidents = load_incidents(state_dir / "incidents.json")
        note = (
            "incident candidates evidence unavailable; froze all incident health "
            f"accrual and state transitions: {candidates_path}: {candidates_error}"
        )
        print(f"fingerprint_dedup: {note}", file=sys.stderr)
        record_node.write_record(
            state_dir,
            "fingerprint_dedup",
            {
                "new": 0,
                "deduplicated": 0,
                "department_defects": 0,
                "evidence_available": False,
                "note": note,
            },
            shadow=shadow,
        )
        return incidents

    if not isinstance(candidates, list):
        raise ValueError("incident_candidates.json must contain a list")
    observations = load_jsonl(state_dir / "observations.jsonl")
    with record_node.records_lock(state_dir):
        incidents = load_incidents(state_dir / "incidents.json")
        merged, stats = merge_candidates(
            candidates, incidents, observations=observations
        )
        record_node.atomic_write_json(state_dir / "incidents.json", merged)
    record_node.write_record(state_dir, "fingerprint_dedup", stats, shadow=shadow)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Fingerprint and deduplicate watchdog incidents")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", dest="shadow", action="store_true", default=True)
    mode.add_argument("--live", dest="shadow", action="store_false")
    args = parser.parse_args()
    incidents = run_dedup(args.state_dir, shadow=args.shadow)
    print(json.dumps({"incidents": len(incidents), "shadow": args.shadow}))


if __name__ == "__main__":
    main()
