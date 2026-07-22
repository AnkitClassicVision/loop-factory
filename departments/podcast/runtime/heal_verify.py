"""Verify that an applied podcast heal actually cleared its incident."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .heal_select import DEFAULT_STATE_DIR, append_heal_receipt
except ImportError:  # direct script execution
    from heal_select import DEFAULT_STATE_DIR, append_heal_receipt


Probe = Callable[[dict[str, Any]], bool | tuple[bool, str]]
_RECEIPT_AGE_LIMIT = re.compile(
    r"^receipt age <= (?P<minutes>\d+(?:\.\d+)?) minutes$"
)


def _last_commands(state_dir: Path, fingerprint: str, playbook_id: str) -> list[str]:
    path = state_dir / "heals.jsonl"
    if not path.exists():
        return []
    commands: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("fingerprint") == fingerprint and row.get("playbook") == playbook_id:
            candidate = row.get("commands")
            if isinstance(candidate, list) and all(isinstance(item, str) for item in candidate):
                commands = candidate
    return commands


def _systemctl_inactive(unit: str) -> tuple[bool, str]:
    completed = subprocess.run(
        ["systemctl", "--user", "is-active", unit],
        check=False,
        capture_output=True,
        text=True,
    )
    active = completed.returncode == 0 and completed.stdout.strip() == "active"
    return not active, f"systemctl is-active {unit}: {completed.stdout.strip() or completed.returncode}"


def _verification_evidence(incident: dict[str, Any]) -> dict[str, Any]:
    """Normalize real list evidence and the legacy structured evidence shape."""
    evidence = incident.get("evidence")
    if isinstance(evidence, dict):
        return evidence
    if not isinstance(evidence, list):
        return {}

    normalized: dict[str, Any] = {}
    entries = [item for item in evidence if isinstance(item, str) and item]
    for entry in reversed(entries):
        if entry.startswith("systemd://") and "unit" not in normalized:
            unit = entry.removeprefix("systemd://")
            if unit:
                normalized["unit"] = unit
        elif "://" not in entry and "path" not in normalized:
            normalized["path"] = entry

    match = _RECEIPT_AGE_LIMIT.fullmatch(str(incident.get("setpoint", "")))
    if match:
        normalized["max_age_seconds"] = float(match.group("minutes")) * 60
    return normalized


def default_condition_persists(incident: dict[str, Any]) -> tuple[bool, str]:
    """Repeat only fixed read-only checks described by incident evidence."""
    evidence = _verification_evidence(incident)
    failure_class = incident.get("failure_class")
    probe_kind = evidence.get("probe") or evidence.get("check")

    if probe_kind in {"systemctl_is_active", "systemctl-active"} or failure_class == "timer_failed":
        unit = evidence.get("unit")
        if not isinstance(unit, str) or not unit:
            return True, "incident evidence lacks the unit needed for verification"
        return _systemctl_inactive(unit)

    if probe_kind in {"file_exists", "file-exists"} or failure_class == "missing_tracker_file":
        path = evidence.get("path")
        if not isinstance(path, str) or not path:
            return True, "incident evidence lacks the path needed for verification"
        exists = Path(path).is_file()
        return not exists, f"file {'exists' if exists else 'is still missing'}: {path}"

    if probe_kind in {"file_fresh", "file-fresh"} or failure_class == "receipt_stale":
        path = evidence.get("path")
        max_age = evidence.get("max_age_seconds")
        if not isinstance(path, str) or not path or not isinstance(max_age, (int, float)):
            return True, "incident evidence lacks path/max_age_seconds for verification"
        target = Path(path)
        if not target.is_file():
            return True, f"receipt is still missing: {path}"
        age = datetime.now(timezone.utc).timestamp() - target.stat().st_mtime
        return age > max_age, f"receipt age {age:.1f}s; maximum {max_age}s"

    return True, "incident evidence has no supported read-only verification probe"


def verify_heal(
    state_dir,
    fingerprint: str,
    playbook_id: str,
    *,
    prober: Probe | None = None,
    shadow: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append verified only when the prober reports the failure is gone.

    A prober returns ``True`` when the incident condition still persists. It
    may instead return ``(persists, detail)`` for a diagnostic receipt.
    """
    state_dir = Path(state_dir)
    mode = "proposed" if shadow else "applied"
    commands = _last_commands(state_dir, fingerprint, playbook_id)
    try:
        incidents = json.loads((state_dir / "incidents.json").read_text(encoding="utf-8"))
        incident = incidents.get(fingerprint) if isinstance(incidents, dict) else None
        if not isinstance(incident, dict):
            raise ValueError("incident is missing")
    except (OSError, ValueError) as exc:
        return append_heal_receipt(
            state_dir, fingerprint=fingerprint, playbook=playbook_id, mode=mode,
            commands=commands, result="failed", detail=f"verification unavailable: {exc}",
            now=now,
        )

    check = prober or default_condition_persists
    try:
        observation = check(incident)
        if isinstance(observation, tuple):
            persists, detail = observation
        else:
            persists = bool(observation)
            detail = "incident condition persists" if persists else "incident condition cleared"
    except Exception as exc:  # a failed probe can never prove clearance
        persists, detail = True, f"verification probe failed: {exc}"

    return append_heal_receipt(
        state_dir,
        fingerprint=fingerprint,
        playbook=playbook_id,
        mode=mode,
        commands=commands,
        result="failed" if persists else "verified",
        detail=detail,
        now=now,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify an applied podcast heal")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--playbook", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", dest="shadow", action="store_true")
    mode.add_argument("--live", dest="shadow", action="store_false")
    parser.set_defaults(shadow=True)
    args = parser.parse_args()
    row = verify_heal(
        args.state_dir,
        args.fingerprint,
        args.playbook,
        shadow=args.shadow,
    )
    print(json.dumps(row, sort_keys=True))


if __name__ == "__main__":
    main()
