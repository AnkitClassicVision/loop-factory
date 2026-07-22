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


def _load_outbox_markers(path: Path) -> dict[tuple[str, str], str | None]:
    """Return durable escalation markers already appended to the outbox."""
    if not path.exists():
        return {}
    markers: dict[tuple[str, str], str | None] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read escalation outbox: {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            packet = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"malformed escalation outbox row {line_number}: {path}: {exc}"
            ) from exc
        if not isinstance(packet, dict):
            continue
        context = packet.get("context", {})
        if not isinstance(context, dict) or not context.get("fingerprint"):
            continue
        fingerprint = str(context["fingerprint"])
        # Rows written before state markers existed were initial-open escalations.
        marker = str(context.get("escalation_marker") or "open")
        markers[(fingerprint, marker)] = str(packet["ts"]) if packet.get("ts") else None
    return markers


def _escalation_state(incident: dict[str, Any]) -> tuple[str, str, str]:
    state = str(incident.get("state", ""))
    if state == "open":
        return "open", "escalated", "escalated_at"
    if state == "department_defect":
        recurrence = max(1, int(incident.get("defect_recurrence_count", 1)))
        return (
            f"department_defect:{recurrence}",
            "escalated_defect",
            "escalated_defect_at",
        )
    raise ValueError(f"incident state is not escalation-eligible: {state!r}")


def escalate_new_incidents(
    incidents_path: str | Path,
    outbox_path: str | Path,
    *,
    shadow: bool = True,
    escalate_fn: EscalateFn = escalate,
    now: str | None = None,
) -> dict[str, Any]:
    """Write each open or defect-state escalation packet exactly once."""
    incidents_path = Path(incidents_path)
    outbox_path = Path(outbox_path)
    escalated_count = 0
    timestamp = now or datetime.now(timezone.utc).isoformat()
    with record_node.records_lock(incidents_path.parent):
        incidents = _load_incidents(incidents_path)
        durable_markers = _load_outbox_markers(outbox_path)
        state_changed = False
        for key in sorted(incidents):
            incident = incidents[key]
            if incident.get("state") not in {"open", "department_defect"}:
                continue
            marker, escalated_field, escalated_at_field = _escalation_state(incident)
            fingerprint = str(incident["fingerprint"])
            durable_key = (fingerprint, marker)

            if durable_key in durable_markers:
                if not incident.get(escalated_field):
                    incident[escalated_field] = True
                    incident[escalated_at_field] = durable_markers[durable_key] or timestamp
                    state_changed = True
                continue
            if incident.get(escalated_field):
                continue

            evidence = [str(value) for value in incident.get("evidence", [])]
            question = str(
                incident.get("one_question", "What owner decision is required?")
            )
            issue = f"{incident.get('failure_class')}: {question}"
            escalate_fn(
                department="podcast",
                issue=issue,
                outbox_path=outbox_path,
                context={
                    "fingerprint": fingerprint,
                    "incident_state": incident["state"],
                    "escalation_marker": marker,
                    "evidence": evidence,
                    "one_question": question,
                },
            )
            durable_markers[durable_key] = timestamp
            incident[escalated_field] = True
            incident[escalated_at_field] = timestamp
            state_changed = True
            escalated_count += 1

        if state_changed:
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
