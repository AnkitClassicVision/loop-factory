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


def fingerprint(sensor: str, subject: str, failure_class: str) -> str:
    material = f"{sensor}|{subject}|{failure_class}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:12]


def load_json(path: str | Path, default: Any) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def merge_candidates(
    candidates: list[dict[str, Any]],
    incidents: dict[str, dict[str, Any]] | None = None,
    *,
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
        key = fingerprint(sensor, subject, failure_class)
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
                "failure_class": failure_class,
                "first_seen": seen_at,
                "last_seen": seen_at,
                "state": "open",
                "severity": candidate["severity"],
                "setpoint": str(candidate["setpoint"]),
                "observed": candidate.get("observed"),
                "evidence": evidence,
                "one_question": str(candidate["one_question"]),
                "count": 1,
                "escalated": False,
                "escalated_at": None,
            }
            stats["new"] += 1
            continue

        current = dict(current)
        current["last_seen"] = seen_at
        current["count"] = int(current.get("count", 0)) + 1
        current["severity"] = candidate["severity"]
        current["observed"] = candidate.get("observed")
        current["setpoint"] = str(candidate["setpoint"])
        current["evidence"] = list(
            dict.fromkeys([*current.get("evidence", []), *evidence])
        )
        if current.get("state") == "resolved":
            current["state"] = "department_defect"
            current["one_question"] = (
                "This resolved fingerprint recurred. What root-cause control failed, "
                "and what permanent department fix is required?"
            )
            stats["department_defects"] += 1
        else:
            stats["deduplicated"] += 1
        merged[key] = current
    return merged, stats


def run_dedup(state_dir: str | Path, *, shadow: bool = True) -> dict[str, dict[str, Any]]:
    state_dir = Path(state_dir)
    candidates = load_json(state_dir / "incident_candidates.json", [])
    incidents = load_json(state_dir / "incidents.json", {})
    if not isinstance(candidates, list):
        raise ValueError("incident_candidates.json must contain a list")
    if not isinstance(incidents, dict):
        raise ValueError("incidents.json must contain an object keyed by fingerprint")
    merged, stats = merge_candidates(candidates, incidents)
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
