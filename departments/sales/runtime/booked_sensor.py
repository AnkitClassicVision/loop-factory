"""Observe qualified sales subjects that have calendar bookings."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from factory import runrecord
from factory.events_ledger import append_event, read_transitions


LOGGER = logging.getLogger(__name__)
MIN_TIME = datetime(1970, 1, 1, tzinfo=timezone.utc)
MAX_TIME = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def _utc(value: datetime | str | None) -> datetime:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _transition_rows(state: Path, from_stage: str, to_stage: str) -> list[dict]:
    return read_transitions(
        state, from_stage=from_stage, to_stage=to_stage,
        since=MIN_TIME, until=MAX_TIME,
    ).rows


def _subject_id(salt: str, email: str) -> str:
    normalized = email.strip().lower()
    if not normalized:
        raise ValueError("calendar attendee_email must be non-empty")
    return hashlib.sha256((salt + normalized).encode()).hexdigest()[:16]


def _load_inputs(state: Path) -> tuple[str, list[dict]]:
    sources = state / "sources"
    salt = (sources / ".id_salt").read_text(encoding="utf-8")
    if not salt:
        raise ValueError("identity salt must be non-empty")
    payload = json.loads((sources / "calendar_events.json").read_text(encoding="utf-8"))
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list) or any(not isinstance(row, dict) for row in events):
        raise ValueError("calendar_events.json must contain an events list")
    return salt, events


def _emit(state: Path, *, started: float, status: str, errors=(), artifacts=()) -> None:
    runrecord.emit_record(
        state,
        department="sales",
        node="booked_sensor",
        status=status,
        release=runrecord.read_release(state.parent),
        trigger={
            "kind": "time", "id": "sales-daily",
            "dedupe_key": f"{datetime.now(timezone.utc).date().isoformat()}-booked_sensor",
        },
        duration_ms=int((time.perf_counter() - started) * 1000),
        errors=list(errors), artifacts=list(artifacts), external_actions_taken=0,
    )


def _run(state: Path, current: datetime) -> dict:
    salt, events = _load_inputs(state)
    qualified = {
        row["subject_id"]
        for row in _transition_rows(state, "received", "qualified")
    }
    conversations = {
        row["subject_id"]
        for row in _transition_rows(state, "qualified", "conversation_live")
    }
    booked = {
        row["subject_id"]
        for stages in (("qualified", "booked"), ("conversation_live", "booked"))
        for row in _transition_rows(state, *stages)
    }
    later = booked | {
        row["subject_id"] for row in _transition_rows(state, "booked", "held")
    }
    eligible = qualified | conversations | later
    booked_count = 0
    skipped = 0
    for event in events:
        subject_id = _subject_id(salt, str(event.get("attendee_email") or ""))
        if subject_id not in eligible or subject_id in booked:
            skipped += 1
            continue
        event_id = event.get("event_id")
        start = event.get("start")
        if not isinstance(event_id, str) or not event_id or not isinstance(start, str):
            raise ValueError("calendar event requires event_id and ISO start")
        _utc(start)
        fast_path = subject_id not in conversations
        append_event(
            state,
            subject_id=subject_id,
            from_stage="qualified" if fast_path else "conversation_live",
            to_stage="booked",
            ts=current,
        )
        _append_jsonl(state / "bookings.jsonl", {
            "subject_id": subject_id, "event_id": event_id, "start": start,
            "fast_path": fast_path, "ts": current.isoformat(),
        })
        booked.add(subject_id)
        booked_count += 1
    observation = {
        "ts": current.isoformat(), "sensor": "booked", "subject": "calendar-bookings",
        "status": "ok", "evidence": str(state / "bookings.jsonl"),
        "detail": f"booked {booked_count} qualified subjects",
        "metrics": {"booked": booked_count, "skipped": skipped},
    }
    _append_jsonl(state / "observations.jsonl", observation)
    return observation


def run(state_dir: Path, *, now: datetime | None = None) -> dict:
    state, current = Path(state_dir), _utc(now)
    started = time.perf_counter()
    try:
        observation = _run(state, current)
        _emit(state, started=started, status="ok", artifacts=[
            str(path) for path in (
                state / "events.jsonl", state / "bookings.jsonl",
                state / "observations.jsonl",
            ) if path.exists()
        ])
        return observation
    except Exception as exc:
        _emit(state, started=started, status="error", errors=[type(exc).__name__])
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    try:
        run(args.state_dir)
    except Exception:
        LOGGER.exception("booked sensor crashed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
