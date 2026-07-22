"""Select a known podcast heal playbook for one open incident.

Selection is deterministic and fail-closed. Unknown or ambiguous failure
classes are recorded as refusals; this node never invents a command.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory.charter_loader import CharterError, immutable_invariants, load_charter
from factory.heal_ladder import (
    ImmutableHealError,
    assert_heal_target_allowed as assert_factory_heal_target_allowed,
)


DEFAULT_STATE_DIR = ROOT / "departments/podcast/state"
DEFAULT_PLAYBOOKS_PATH = Path(__file__).with_name("playbooks.json")
DEFAULT_CHARTER_PATH = Path(__file__).resolve().parents[1] / "charter.yaml"


def assert_heal_target_allowed(target: str) -> None:
    """Enforce both the factory floor and podcast's stricter charter floor."""
    assert_factory_heal_target_allowed(target)
    try:
        charter = load_charter(DEFAULT_CHARTER_PATH, expect_department="podcast")
    except CharterError as exc:
        raise ImmutableHealError(
            f"charter invariant allowlist unavailable: {exc}"
        ) from exc
    if target in immutable_invariants(charter):
        raise ImmutableHealError(
            f"heal may not modify immutable invariant: {target}"
        )


def _timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_playbooks(path=DEFAULT_PLAYBOOKS_PATH) -> list[dict[str, Any]]:
    """Load and minimally validate the versioned playbook allowlist."""
    document = _load_json(Path(path))
    if not isinstance(document, dict) or not isinstance(document.get("version"), int):
        raise ValueError("playbook document requires an integer version")
    playbooks = document.get("playbooks")
    if not isinstance(playbooks, list):
        raise ValueError("playbook document requires a playbooks list")
    required = {
        "id", "matches_failure_class", "description", "commands",
        "heal_target", "max_attempts_per_day",
    }
    seen: set[str] = set()
    for playbook in playbooks:
        if not isinstance(playbook, dict) or not required.issubset(playbook):
            raise ValueError("each playbook must contain the complete contract")
        if playbook["id"] in seen:
            raise ValueError(f"duplicate playbook id: {playbook['id']}")
        if not isinstance(playbook["commands"], list) or not all(
            isinstance(command, str) and command for command in playbook["commands"]
        ):
            raise ValueError(f"playbook {playbook['id']} has invalid commands")
        if not isinstance(playbook["max_attempts_per_day"], int) or playbook[
            "max_attempts_per_day"
        ] < 1:
            raise ValueError(f"playbook {playbook['id']} has invalid attempt cap")
        seen.add(playbook["id"])
    return playbooks


def append_heal_receipt(
    state_dir,
    *,
    fingerprint: str,
    playbook: str,
    mode: str,
    commands: list[str],
    result: str,
    detail: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one receipt using the frozen SG-HEAL receipt contract."""
    row = {
        "ts": _timestamp(now),
        "fingerprint": str(fingerprint),
        "playbook": str(playbook),
        "mode": mode,
        "commands": list(commands),
        "result": result,
        "detail": detail,
    }
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "heals.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def select_heal(
    state_dir,
    fingerprint: str,
    *,
    playbooks_path=DEFAULT_PLAYBOOKS_PATH,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the sole allowed playbook, or append a refusal and return None."""
    state_dir = Path(state_dir)
    incidents_path = state_dir / "incidents.json"
    try:
        incidents = _load_json(incidents_path)
    except (OSError, ValueError) as exc:
        append_heal_receipt(
            state_dir,
            fingerprint=fingerprint,
            playbook="",
            mode="proposed",
            commands=[],
            result="refused",
            detail=f"incident data unavailable — escalate: {exc}",
            now=now,
        )
        return None

    incident = incidents.get(fingerprint) if isinstance(incidents, dict) else None
    if not isinstance(incident, dict) or incident.get("state") not in {
        "open", "department_defect",
    }:
        append_heal_receipt(
            state_dir,
            fingerprint=fingerprint,
            playbook="",
            mode="proposed",
            commands=[],
            result="refused",
            detail="incident is missing or not healable — escalate",
            now=now,
        )
        return None

    try:
        playbooks = load_playbooks(playbooks_path)
    except (OSError, ValueError) as exc:
        append_heal_receipt(
            state_dir,
            fingerprint=fingerprint,
            playbook="",
            mode="proposed",
            commands=[],
            result="refused",
            detail=f"playbook allowlist unavailable — escalate: {exc}",
            now=now,
        )
        return None

    matches = [
        playbook
        for playbook in playbooks
        if playbook["matches_failure_class"] == incident.get("failure_class")
    ]
    if not matches:
        append_heal_receipt(
            state_dir,
            fingerprint=fingerprint,
            playbook="",
            mode="proposed",
            commands=[],
            result="refused",
            detail="unknown pattern — escalate",
            now=now,
        )
        return None
    if len(matches) != 1:
        append_heal_receipt(
            state_dir,
            fingerprint=fingerprint,
            playbook="",
            mode="proposed",
            commands=[],
            result="refused",
            detail="ambiguous pattern — escalate",
            now=now,
        )
        return None

    selected = matches[0]
    try:
        assert_heal_target_allowed(selected["heal_target"])
    except ImmutableHealError as exc:
        append_heal_receipt(
            state_dir,
            fingerprint=fingerprint,
            playbook=selected["id"],
            mode="proposed",
            commands=[],
            result="refused",
            detail=str(exc),
            now=now,
        )
        return None
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Select an allowlisted podcast heal")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--playbooks", default=str(DEFAULT_PLAYBOOKS_PATH))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", dest="shadow", action="store_true")
    mode.add_argument("--live", dest="shadow", action="store_false")
    parser.set_defaults(shadow=True)
    args = parser.parse_args()
    selected = select_heal(
        args.state_dir, args.fingerprint, playbooks_path=args.playbooks
    )
    if selected is not None:
        print(json.dumps(selected, sort_keys=True))


if __name__ == "__main__":
    main()
