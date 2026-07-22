"""Escalate each new watchdog fingerprint to the local decision outbox once."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.human_in_the_loop import escalate
from departments.podcast.runtime import record as record_node


DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "podcast" / "state"
EscalateFn = Callable[..., dict[str, Any]]


def _load_incidents(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("incidents.json must contain an object keyed by fingerprint")
    return value


def escalate_new_incidents(
    incidents_path: str | Path,
    outbox_path: str | Path,
    *,
    shadow: bool = True,
    escalate_fn: EscalateFn = escalate,
    now: str | None = None,
) -> dict[str, Any]:
    """Write local escalation packets for un-escalated open incidents only."""
    incidents_path = Path(incidents_path)
    incidents = _load_incidents(incidents_path)
    escalated_count = 0
    timestamp = now or datetime.now(timezone.utc).isoformat()
    for key in sorted(incidents):
        incident = incidents[key]
        if incident.get("state") != "open" or incident.get("escalated"):
            continue
        evidence = [str(value) for value in incident.get("evidence", [])]
        question = str(incident.get("one_question", "What owner decision is required?"))
        issue = f"{incident.get('failure_class')}: {question}"
        escalate_fn(
            department="podcast",
            issue=issue,
            outbox_path=outbox_path,
            context={
                "fingerprint": incident["fingerprint"],
                "evidence": evidence,
                "one_question": question,
            },
        )
        incident["escalated"] = True
        incident["escalated_at"] = timestamp
        escalated_count += 1

    if escalated_count:
        record_node.atomic_write_json(incidents_path, incidents)
    return {
        "outbox_rows": escalated_count,
        "delivered_count": 0,
        "external_actions_taken": [],
        "shadow": bool(shadow),
    }


def run_escalate(state_dir: str | Path, *, shadow: bool = True) -> dict[str, Any]:
    state_dir = Path(state_dir)
    result = escalate_new_incidents(
        state_dir / "incidents.json",
        state_dir / "decisions_outbox.jsonl",
        shadow=shadow,
    )
    record_node.write_record(state_dir, "escalate_outbox", result, shadow=shadow)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Escalate new watchdog incidents once")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", dest="shadow", action="store_true", default=True)
    mode.add_argument("--live", dest="shadow", action="store_false")
    args = parser.parse_args()
    result = run_escalate(args.state_dir, shadow=args.shadow)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
