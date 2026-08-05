"""Escalate each new watchdog fingerprint to the local decision outbox once."""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import runrecord
from factory.human_in_the_loop import _atomic_write, escalate
from departments.podcast.runtime.compare_charter import MEANINGS
from departments.podcast.runtime import record as record_node


DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "podcast" / "state"
EscalateFn = Callable[..., dict[str, Any]]
_FINGERPRINT = re.compile(r"^[0-9a-f]{12}$")
_DEFECT_MARKER = re.compile(r"^department_defect:[1-9][0-9]*$")
LOGGER = logging.getLogger(__name__)


def _plain_copy(failure_class: str) -> tuple[str, str]:
    meaning = MEANINGS.get(failure_class)
    if meaning is None:
        return (
            "A podcast process needs attention and the team could not fix it automatically.",
            "Ops must investigate and bring you a specific decision only if one is required.",
        )
    return meaning["what_it_means"], meaning["what_it_needs"]


def _eli5(failure_class: str) -> str:
    what_it_means, what_it_needs = _plain_copy(failure_class)
    return (
        "[podcast] Action needed — "
        f"WHAT THIS MEANS: {what_it_means} "
        f"WHAT IT NEEDS: {what_it_needs}"
    )


def _replace_latest_eli5(path: Path, eli5: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if not lines[index].strip():
            continue
        packet = json.loads(lines[index])
        packet["eli5"] = eli5
        lines[index] = json.dumps(packet)
        _atomic_write(path, "\n".join(lines) + "\n")
        return
    raise ValueError("escalation did not append an outbox row")


def _load_incidents(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("incidents.json must contain an object keyed by fingerprint")
    return value


def _outbox_warning(path: Path, line_number: int, detail: str) -> None:
    print(
        f"ignored malformed escalation outbox row {line_number}: {path}: {detail}",
        file=sys.stderr,
    )


def _load_outbox_markers(
    path: Path,
) -> dict[tuple[str, str], str | None]:
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
            _outbox_warning(path, line_number, str(exc))
            continue
        if not isinstance(packet, dict):
            _outbox_warning(path, line_number, "row is not an object")
            continue
        if packet.get("department") != "podcast" or packet.get("kind") != "escalation":
            continue
        context = packet.get("context", {})
        if not isinstance(context, dict):
            _outbox_warning(path, line_number, "context is not an object")
            continue
        fingerprint = context.get("fingerprint")
        marker = context.get("escalation_marker")
        incident_state = context.get("incident_state")
        question = context.get("one_question")
        evidence = context.get("evidence")
        issue = packet.get("issue")
        timestamp = packet.get("ts")
        failure_class = issue.split(":", 1)[0] if isinstance(issue, str) else ""
        marker_matches_state = (
            (marker == "open" and incident_state == "open")
            or (
                isinstance(marker, str)
                and _DEFECT_MARKER.fullmatch(marker) is not None
                and incident_state == "department_defect"
            )
        )
        structurally_valid = (
            isinstance(fingerprint, str)
            and _FINGERPRINT.fullmatch(fingerprint) is not None
            and marker_matches_state
            and isinstance(question, str)
            and bool(question)
            and isinstance(evidence, list)
            and all(isinstance(value, str) for value in evidence)
            and isinstance(issue, str)
            and issue.endswith(f": {question}")
            and len(issue) > len(question) + 2
            and isinstance(timestamp, str)
            and bool(timestamp)
            and packet.get("eli5") == _eli5(failure_class)
        )
        if not structurally_valid:
            _outbox_warning(path, line_number, "packet does not match podcast escalation schema")
            continue
        markers[(fingerprint, marker)] = timestamp
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
            evidence = [str(value) for value in incident.get("evidence", [])]
            question = str(
                incident.get("one_question", "What owner decision is required?")
            )
            issue = f"{incident.get('failure_class')}: {question}"
            durable_key = (fingerprint, marker)

            if durable_key in durable_markers:
                if not incident.get(escalated_field):
                    incident[escalated_field] = True
                    incident[escalated_at_field] = durable_markers[durable_key] or timestamp
                    state_changed = True
                continue
            if incident.get(escalated_field):
                continue

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
            _replace_latest_eli5(outbox_path, _eli5(str(incident.get("failure_class"))))
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


def _run_escalate(state_dir: str | Path, *, shadow: bool = True) -> dict[str, Any]:
    state_dir = Path(state_dir)
    result = escalate_new_incidents(
        state_dir / "incidents.json",
        state_dir / "decisions_outbox.jsonl",
        shadow=shadow,
    )
    record_node.write_record(state_dir, "escalate_outbox", result, shadow=shadow)
    return result


def _emit_run_record(
    state_dir: Path,
    *,
    started: float,
    status: str,
    errors: list[str],
    artifacts: list[Path],
) -> None:
    try:
        runrecord.emit_record(
            state_dir,
            department="podcast",
            node="escalate_outbox",
            status=status,
            release=runrecord.read_release(state_dir.parent),
            trigger={
                "kind": "time",
                "id": "podcast-daily",
                "dedupe_key": (
                    f"{datetime.now(timezone.utc).date().isoformat()}-escalate_outbox"
                ),
            },
            duration_ms=int((time.perf_counter() - started) * 1000),
            errors=errors,
            artifacts=[str(path) for path in artifacts],
            external_actions_taken=0,
        )
    except Exception:
        LOGGER.exception("escalate_outbox failed to append its runs-v2 record")
        raise


def _file_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns)


def run_escalate(state_dir: str | Path, *, shadow: bool = True) -> dict[str, Any]:
    state_path = Path(state_dir)
    started = time.perf_counter()
    possible_artifacts = [
        state_path / "runs.jsonl",
        state_path / "incidents.json",
        state_path / "decisions_outbox.jsonl",
    ]
    before = {path: _file_signature(path) for path in possible_artifacts}
    try:
        result = _run_escalate(state_path, shadow=shadow)
    except Exception as exc:
        artifacts = [
            path
            for path in possible_artifacts
            if _file_signature(path) != before[path]
        ]
        _emit_run_record(
            state_path,
            started=started,
            status="error",
            errors=[type(exc).__name__],
            artifacts=artifacts,
        )
        raise
    artifacts = [
        path
        for path in possible_artifacts
        if _file_signature(path) != before[path]
    ]
    _emit_run_record(
        state_path,
        started=started,
        status="ok",
        errors=[],
        artifacts=artifacts,
    )
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
