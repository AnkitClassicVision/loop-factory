"""Ask the owner to confirm held evidence for attended booked calls.

The calendar source can prove a meeting existed and was attended (HubSpot
outcome COMPLETED) but can never attest that a decision-maker was present or
that the call truly held >= 20 minutes — the fetcher hard-falses
``decision_maker_present`` by design. That attestation is the owner's (C1:
held means attended + decision-maker + >= 20 min; C6/Q12: cards carry
make-sense copy + exact approvable actions, 48h SLA, silence never approves).

Two phases per run, deterministic, no model calls, zero external actions:

1. APPLY — every ``held_confirm_queue.jsonl`` row a human has decided (via
   ``factory/human_in_the_loop.py apply --queue ... --decision-id
   sales-held-<event_id> --verdict APPROVE|REJECT``) becomes one durable row
   in ``held_confirmations.jsonl`` — the held evidence held_sensor consumes.
2. ASK — every booked, not-yet-held SUBJECT with an attended past event the
   source cannot attest gets ONE pending queue row + ONE cards-v2 packet in
   the human outbox, for its most recent eligible event only (one confirmed
   event holds the subject; a declined answer falls back to the next event on
   a later run, and no new ask stacks while one is pending). The queue is the
   durable dedupe marker; outbox markers cover the crash window between
   packet append and queue append. Approve is safe-forward (confirms held);
   "did not hold" rides REJECT/FIX.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory import runrecord
from factory.events_ledger import read_transitions
from factory.human_in_the_loop import escalate


LOGGER = logging.getLogger(__name__)
MIN_TIME = datetime(1970, 1, 1, tzinfo=timezone.utc)
MAX_TIME = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
DEFAULT_OUTBOX = Path(__file__).resolve().parents[3] / "state" / "decisions_outbox.jsonl"
QUEUE_NAME = "held_confirm_queue.jsonl"
CONFIRMATIONS_NAME = "held_confirmations.jsonl"


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


def _jsonl_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


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


def _decision_id(event_id: str) -> str:
    return f"sales-held-{event_id}"


def _outbox_decision_ids(outbox: Path) -> set[str]:
    """Decision ids already carded, so a crash between packet append and queue
    append never produces a duplicate card."""
    ids: set[str] = set()
    for packet in _jsonl_rows(outbox):
        if packet.get("department") != "sales" or packet.get("kind") != "escalation":
            continue
        context = packet.get("context")
        if not isinstance(context, dict):
            continue
        decision_id = context.get("decision_id")
        if isinstance(decision_id, str) and decision_id.startswith("sales-held-"):
            ids.add(decision_id)
    return ids


def _apply_decisions(state: Path, current: datetime) -> tuple[int, int]:
    """Turn human-decided queue rows into durable confirmation evidence."""
    queue_rows = _jsonl_rows(state / QUEUE_NAME)
    recorded = {
        row.get("decision_id")
        for row in _jsonl_rows(state / CONFIRMATIONS_NAME)
    }
    confirmed = declined = 0
    for row in queue_rows:
        status, decision_id = row.get("status"), row.get("decision_id")
        if status not in {"approved", "rejected"} or not isinstance(decision_id, str):
            continue
        if decision_id in recorded:
            continue
        approved = status == "approved"
        _append_jsonl(state / CONFIRMATIONS_NAME, {
            "decision_id": decision_id,
            "event_id": row.get("event_id"),
            "subject_id": row.get("subject_id"),
            "confirmed": approved,
            "ts": current.isoformat(),
        })
        recorded.add(decision_id)
        confirmed += int(approved)
        declined += int(not approved)
    return confirmed, declined


def _card(event_id: str, subject_id: str, minutes: int | None, start: str) -> dict:
    scheduled = f"{minutes} scheduled minutes" if minutes is not None else "unknown length"
    return {
        "issue": (
            f"held_confirm: Did booked call {event_id} hold >=20 min "
            "with a decision-maker?"
        ),
        "meaning": (
            f"A booked sales call ({scheduled}, started {start}) has passed and "
            "the calendar marks it attended, but only you can attest it truly "
            "held for at least 20 minutes with a decision-maker present. Until "
            "you confirm, the funnel refuses to count it as held."
        ),
        "needs": (
            "Reply with the approve token ONLY if the call held >= 20 minutes "
            "with a decision-maker present. If it did not, reply FIX with what "
            "happened (no-show, cut short, no decision-maker). Silence never "
            "confirms."
        ),
        "actions": [{
            "action": (
                f"Confirm held: call {event_id} held >= 20 minutes with a "
                "decision-maker present"
            ),
            "effect": (
                "the held confirmation evidence is recorded and this subject "
                "counts as held on the next daily run"
            ),
            "reply": f"approve {_decision_id(event_id)}",
        }],
        "context": {
            "event_id": event_id,
            "subject_id": subject_id,
            "minutes": minutes,
            "start": start,
            "decision_id": _decision_id(event_id),
            "queue": f"state/{QUEUE_NAME}",
            "apply_hint": (
                "python3 factory/human_in_the_loop.py apply "
                f"--queue departments/sales/state/{QUEUE_NAME} "
                f"--decision-id {_decision_id(event_id)} --verdict APPROVE|REJECT"
            ),
        },
    }


def _ask(state: Path, outbox: Path, current: datetime) -> tuple[int, int]:
    salt, events = _load_inputs(state)
    booked = {
        row["subject_id"]
        for stages in (("qualified", "booked"), ("conversation_live", "booked"))
        for row in _transition_rows(state, *stages)
    }
    held = {row["subject_id"] for row in _transition_rows(state, "booked", "held")}
    queue_rows = _jsonl_rows(state / QUEUE_NAME)
    queued_events = {
        row.get("event_id")
        for row in queue_rows
        if isinstance(row.get("event_id"), str)
    }
    # One open ask per subject: never stack cards while one is unanswered,
    # and never ask again once an answer confirmed the subject (approved rows
    # and confirmed rows both block — the held transition may still be one
    # held_sensor run away). Only a REJECTED answer re-opens the fallback.
    blocked_subjects = {
        row.get("subject_id")
        for row in queue_rows
        if row.get("status") in {"pending_approval", "approved"}
    } | {
        row.get("subject_id")
        for row in _jsonl_rows(state / CONFIRMATIONS_NAME)
        if row.get("confirmed") is True
    }
    carded = _outbox_decision_ids(outbox)
    # The funnel's unit is the subject, not the meeting: one confirmed event
    # holds the subject, so ask about the MOST RECENT eligible event only. A
    # declined answer falls back to the next event on a later run. This keeps
    # a recurring-call subject from producing a card per historical meeting
    # (26 cards for 2 subjects in the first live shadow).
    candidates: dict[str, tuple[datetime, str, int | None, str]] = {}
    ineligible = eligible = 0
    for event in events:
        subject_id = _subject_id(salt, str(event.get("attendee_email") or ""))
        event_id = event.get("event_id")
        try:
            start = _utc(event.get("start"))
        except (TypeError, ValueError):
            ineligible += 1
            continue
        try:
            minutes = int(event.get("minutes"))
        except (TypeError, ValueError):
            minutes = None
        needs_ask = (
            isinstance(event_id, str)
            and bool(event_id)
            and event.get("attended") is True
            and event.get("decision_maker_present") is not True
            and start < current
            and subject_id in booked
            and subject_id not in held
            and subject_id not in blocked_subjects
            and event_id not in queued_events
        )
        if not needs_ask:
            ineligible += 1
            continue
        eligible += 1
        best = candidates.get(subject_id)
        if best is None or start > best[0]:
            candidates[subject_id] = (start, event_id, minutes, str(event.get("start")))
    asked = 0
    for subject_id in sorted(candidates):
        start, event_id, minutes, start_raw = candidates[subject_id]
        decision_id = _decision_id(event_id)
        if decision_id not in carded:
            card = _card(event_id, subject_id, minutes, start_raw)
            result = escalate(
                department="sales",
                issue=card["issue"],
                outbox_path=outbox,
                context=card["context"],
                meaning=card["meaning"],
                needs=card["needs"],
                actions=card["actions"],
                owner="ankit",
                deadline=(current + timedelta(hours=48)).isoformat(),
                next_action="Confirm whether the booked call met the held-call definition",
            )
            if result.get("escalated") is not True:
                LOGGER.error("held confirmation escalation blocked: %s", result.get("reason"))
                continue
            carded.add(decision_id)
        # The packet is durable before the queue row: a crash between the two
        # re-queues on the next run without ever duplicating the card.
        _append_jsonl(state / QUEUE_NAME, {
            "kind": "held_confirm",
            "event_id": event_id,
            "subject_id": subject_id,
            "minutes": minutes,
            "start": start_raw,
            "status": "pending_approval",
            "queued_at": current.isoformat(),
            "decision_id": decision_id,
        })
        asked += 1
    return asked, ineligible, eligible - asked


def _emit(state: Path, *, started: float, status: str, errors=(), artifacts=()) -> None:
    runrecord.emit_record(
        state,
        department="sales", node="held_confirm_card", status=status,
        release=runrecord.read_release(state.parent),
        trigger={
            "kind": "time", "id": "sales-daily",
            "dedupe_key": f"{datetime.now(timezone.utc).date().isoformat()}-held_confirm_card",
        },
        duration_ms=int((time.perf_counter() - started) * 1000),
        errors=list(errors), artifacts=list(artifacts), external_actions_taken=0,
    )


def _run(state: Path, outbox: Path, current: datetime) -> dict:
    confirmed, declined = _apply_decisions(state, current)
    asked, ineligible, deferred = _ask(state, outbox, current)
    pending = sum(
        row.get("status") == "pending_approval"
        for row in _jsonl_rows(state / QUEUE_NAME)
    )
    observation = {
        "ts": current.isoformat(), "sensor": "held_confirm",
        "subject": "held-confirm-cards", "status": "ok",
        "evidence": str(state / QUEUE_NAME),
        "detail": (
            f"asked {asked} held confirmations; applied {confirmed + declined} "
            f"answers ({confirmed} confirmed, {declined} declined); "
            f"{pending} pending; {deferred} older eligible events deferred"
        ),
        "metrics": {
            "asked": asked, "confirmed": confirmed, "declined": declined,
            "pending": pending, "ineligible": ineligible, "deferred": deferred,
        },
    }
    _append_jsonl(state / "observations.jsonl", observation)
    return observation


def run(state_dir: Path, *, outbox: Path | None = None, now: datetime | None = None) -> dict:
    state, current = Path(state_dir), _utc(now)
    outbox_path = Path(outbox) if outbox is not None else DEFAULT_OUTBOX
    started = time.perf_counter()
    try:
        observation = _run(state, outbox_path, current)
        _emit(state, started=started, status="ok", artifacts=[
            str(path) for path in (
                state / QUEUE_NAME, state / CONFIRMATIONS_NAME,
                state / "observations.jsonl", outbox_path,
            ) if path.exists()
        ])
        return observation
    except Exception as exc:
        _emit(state, started=started, status="error", errors=[type(exc).__name__])
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--outbox", type=Path, default=DEFAULT_OUTBOX)
    parser.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    try:
        run(args.state_dir, outbox=args.outbox)
    except Exception:
        LOGGER.exception("held confirm card crashed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
