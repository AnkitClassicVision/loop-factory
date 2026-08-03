"""Fail-closed verification for charter objectives and their observations."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from factory.charter_loader import CharterError, load_charter


SCHEMA = "objectives-observed/v1"
MAX_AGE = timedelta(hours=48)
REQUIRED_FIELDS = ("label", "unit", "minimum", "target")


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def verify(
    name: str,
    *,
    charter_path: Path,
    objectives_path: Path,
    allow_unknown: bool = False,
    allow_stale: bool = False,
    now: datetime | None = None,
) -> list[str]:
    """Return every failure as a human-readable WHY line."""
    failures: list[str] = []
    try:
        charter = load_charter(charter_path, expect_department=name)
    except (CharterError, OSError) as exc:
        return [f"WHY {name} charter: {exc}"]

    objectives = charter.get("setpoints", {}).get("objectives")
    if not isinstance(objectives, dict) or not objectives:
        return [f"WHY {name} setpoints.objectives: missing or not a mapping"]

    valid_objectives: dict[str, dict[str, Any]] = {}
    for objective_id, contract in objectives.items():
        if not isinstance(objective_id, str) or not isinstance(contract, dict):
            failures.append(f"WHY {objective_id!s} contract: must be a mapping")
            continue
        for field in REQUIRED_FIELDS:
            if field not in contract or contract[field] is None:
                failures.append(f"WHY {objective_id} {field}: required field missing")
        if "setpoint" not in contract or contract.get("setpoint") is None:
            failures.append(f"WHY {objective_id} setpoint: required for consistency check")
        if not isinstance(contract.get("label"), str) or not contract.get("label", "").strip():
            failures.append(f"WHY {objective_id} label: must be a non-empty string")
        if not isinstance(contract.get("unit"), str) or not contract.get("unit", "").strip():
            failures.append(f"WHY {objective_id} unit: must be a non-empty string")
        for field in ("minimum", "setpoint", "target", "maximum"):
            if field in contract and not _number(contract[field]):
                failures.append(f"WHY {objective_id} {field}: must be a finite number")
        minimum = contract.get("minimum")
        setpoint = contract.get("setpoint")
        target = contract.get("target")
        maximum = contract.get("maximum")
        if all(_number(value) for value in (minimum, setpoint, target)):
            if not minimum <= setpoint <= target:
                failures.append(
                    f"WHY {objective_id} setpoint: expected minimum <= setpoint <= target"
                )
            if _number(maximum) and not target <= maximum:
                failures.append(
                    f"WHY {objective_id} maximum: expected target <= maximum"
                )
        valid_objectives[objective_id] = contract

    try:
        raw = json.loads(objectives_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        failures.append(f"WHY {name} objectives_observed: malformed or unreadable ({exc})")
        return failures
    if not isinstance(raw, dict):
        failures.append(f"WHY {name} objectives_observed: root must be an object")
        return failures
    if raw.get("schema") != SCHEMA:
        failures.append(f"WHY {name} schema: expected {SCHEMA}")
    observed_at = _parse_ts(raw.get("ts"))
    if observed_at is None:
        failures.append(f"WHY {name} ts: must be an ISO-8601 timestamp with timezone")
    else:
        age = (now or datetime.now(timezone.utc)) - observed_at
        if age > MAX_AGE and not allow_stale:
            failures.append(f"WHY {name} ts: stale (older than 48h)")
    values = raw.get("values")
    if not isinstance(values, dict):
        failures.append(f"WHY {name} values: must be an object")
        values = {}

    for objective_id, contract in valid_objectives.items():
        if objective_id not in values:
            print(f"UNKNOWN {objective_id}: observed value absent")
            if not allow_unknown:
                failures.append(f"WHY {objective_id} observed: absent (honest unknown)")
            continue
        observed = values[objective_id]
        if not _number(observed):
            failures.append(
                f"WHY {objective_id} observed: must be a finite number for unit {contract.get('unit')!r}"
            )
            continue
        minimum = contract.get("minimum")
        maximum = contract.get("maximum")
        if _number(minimum) and observed < minimum:
            failures.append(f"OBJECTIVE_BELOW_MIN {objective_id}")
        if _number(maximum) and observed > maximum:
            failures.append(
                f"WHY {objective_id} observed: {observed} exceeds maximum {maximum}"
            )
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--objectives-file", type=Path)
    parser.add_argument("--charter", type=Path)
    parser.add_argument("--allow-stale", action="store_true")
    parser.add_argument("--allow-unknown", action="store_true")
    args = parser.parse_args(argv)
    department = Path("departments") / args.name
    failures = verify(
        args.name,
        charter_path=args.charter or department / "charter.yaml",
        objectives_path=args.objectives_file or department / "state" / "objectives_observed.json",
        allow_unknown=args.allow_unknown,
        allow_stale=args.allow_stale,
    )
    for failure in failures:
        print(failure)
    if failures:
        return 1
    print(f"OBJECTIVES_VERIFY_OK {args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
