"""Observe completed sales meetings with resolved funnel attribution."""
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


def _rows(state: Path, from_stage: str, to_stage: str) -> list[dict]:
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


def _qualification_bars(state: Path) -> dict[str, str]:
    path = state / "qualifications.jsonl"
    bars: dict[str, str] = {}
    if not path.exists():
        return bars
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(row, dict) and row.get("bar") in {"services", "seller"}:
            subject_id = row.get("subject_id")
            if isinstance(subject_id, str) and subject_id:
                bars[subject_id] = row["bar"]
    return bars


def _confirmations(state: Path) -> dict[str, str]:
    """event_id -> decision_id for owner-confirmed held calls.

    Written by held_confirm_card from the owner's queue answers. A confirmed
    event is decision-maker-attested (>= 20 min held) by the owner; the
    decision_id on the receipt traces to the card that carried the attestation.
    """
    confirmed: dict[str, str] = {}
    path = state / "held_confirmations.jsonl"
    if not path.exists():
        return confirmed
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict) or row.get("confirmed") is not True:
            continue
        event_id, decision_id = row.get("event_id"), row.get("decision_id")
        if isinstance(event_id, str) and event_id and isinstance(decision_id, str):
            confirmed[event_id] = decision_id
    return confirmed


def _received_sources(state: Path) -> dict[str, str]:
    sources: dict[str, str] = {}
    for row in _rows(state, "arrival", "received"):
        source = row.get("meta", {}).get("source")
        if isinstance(source, str) and source:
            sources[row["subject_id"]] = source
    return sources


def _emit(state: Path, *, started: float, status: str, errors=(), artifacts=()) -> None:
    runrecord.emit_record(
        state,
        department="sales", node="held_sensor", status=status,
        release=runrecord.read_release(state.parent),
        trigger={
            "kind": "time", "id": "sales-daily",
            "dedupe_key": f"{datetime.now(timezone.utc).date().isoformat()}-held_sensor",
        },
        duration_ms=int((time.perf_counter() - started) * 1000),
        errors=list(errors), artifacts=list(artifacts), external_actions_taken=0,
    )


def _run(state: Path, current: datetime) -> dict:
    salt, events = _load_inputs(state)
    booked = {
        row["subject_id"]
        for stages in (("qualified", "booked"), ("conversation_live", "booked"))
        for row in _rows(state, *stages)
    }
    held = {row["subject_id"] for row in _rows(state, "booked", "held")}
    bars, sources = _qualification_bars(state), _received_sources(state)
    confirmations = _confirmations(state)
    held_count = 0
    unresolved = 0
    ineligible = 0
    for event in events:
        subject_id = _subject_id(salt, str(event.get("attendee_email") or ""))
        try:
            minutes = int(event.get("minutes"))
            start = _utc(event.get("start"))
        except (TypeError, ValueError):
            ineligible += 1
            continue
        raw_event_id = event.get("event_id")
        confirmed_by = (
            confirmations.get(raw_event_id)
            if isinstance(raw_event_id, str)
            else None
        )
        eligible_event = (
            event.get("attended") is True
            and start < current
            and (
                confirmed_by is not None
                or (event.get("decision_maker_present") is True and minutes >= 20)
            )
        )
        if not eligible_event or subject_id not in booked or subject_id in held:
            ineligible += 1
            continue
        bar, source = bars.get(subject_id), sources.get(subject_id)
        if bar is None or source is None:
            unresolved += 1
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("eligible calendar event requires event_id")
        append_event(
            state, subject_id=subject_id, from_stage="booked", to_stage="held",
            ts=current,
        )
        receipt = {
            "subject_id": subject_id, "event_id": event_id, "minutes": minutes,
            "bar": bar, "source": source, "ts": current.isoformat(),
        }
        if confirmed_by is not None:
            receipt["confirmed_by"] = confirmed_by
        _append_jsonl(state / "held.jsonl", receipt)
        held.add(subject_id)
        held_count += 1
    status = "alarm" if unresolved else "ok"
    observation = {
        "ts": current.isoformat(), "sensor": "held", "subject": "calendar-held",
        "status": status, "evidence": str(state / "held.jsonl"),
        "detail": (
            f"refused {unresolved} held transitions with unresolved attribution"
            if unresolved else f"held {held_count} completed meetings"
        ),
        "metrics": {
            "held": held_count, "attribution_unresolved": unresolved,
            "ineligible": ineligible,
        },
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
                state / "events.jsonl", state / "held.jsonl",
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
        LOGGER.exception("held sensor crashed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
