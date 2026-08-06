from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory.events_ledger import append_event
from factory.human_in_the_loop import apply
from departments.sales.runtime import booked_sensor, held_confirm_card, held_sensor


NOW = datetime(2026, 8, 6, 16, 0, tzinfo=timezone.utc)
SALT = "fixture-only-salt"
EMAIL = "owner1@example.test"


def _subject(email: str = EMAIL) -> str:
    return hashlib.sha256((SALT + email.strip().lower()).encode()).hexdigest()[:16]


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _state(tmp_path: Path, event: dict) -> Path:
    state = tmp_path / "state"
    sources = state / "sources"
    sources.mkdir(parents=True)
    (sources / ".id_salt").write_text(SALT, encoding="utf-8")
    (sources / "calendar_events.json").write_text(
        json.dumps({"events": [event]}), encoding="utf-8"
    )
    return state


def _event(**overrides) -> dict:
    row = {
        "event_id": "event-001",
        "attendee_email": EMAIL,
        "start": (NOW - timedelta(hours=1)).isoformat(),
        "minutes": 25,
        "attended": True,
        "decision_maker_present": False,
    }
    row.update(overrides)
    return row


def _qualified(state: Path, *, source: str = "icaregrow") -> None:
    append_event(
        state,
        subject_id=_subject(),
        from_stage="arrival",
        to_stage="received",
        ts=NOW - timedelta(days=2),
        meta={"source": source},
    )
    append_event(
        state,
        subject_id=_subject(),
        from_stage="received",
        to_stage="qualified",
        ts=NOW - timedelta(days=1),
    )
    with (state / "qualifications.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"subject_id": _subject(), "bar": "services", "ts": NOW.isoformat()}) + "\n")


def _booked(state: Path) -> None:
    _qualified(state)
    booked_sensor.run(state, now=NOW)


def _silence_records(state: Path, monkeypatch) -> None:
    for module in (booked_sensor, held_confirm_card, held_sensor):
        monkeypatch.setattr(module.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")


def _outbox(tmp_path: Path) -> Path:
    return tmp_path / "outbox" / "decisions_outbox.jsonl"


def test_card_emitted_once_with_exact_v2_shape(tmp_path, monkeypatch):
    state = _state(tmp_path, _event())
    outbox = _outbox(tmp_path)
    _silence_records(state, monkeypatch)
    _booked(state)

    first = held_confirm_card.run(state, outbox=outbox, now=NOW)
    second = held_confirm_card.run(state, outbox=outbox, now=NOW)

    assert first["metrics"]["asked"] == 1
    assert second["metrics"]["asked"] == 0
    packets = _rows(outbox)
    assert len(packets) == 1
    packet = packets[0]
    assert packet["kind"] == "escalation"
    assert packet["department"] == "sales"
    assert packet["issue"].startswith("held_confirm: ")
    card = packet["card"]
    assert set(card) == {"what_it_means", "what_it_needs", "approvable_actions"}
    assert card["approvable_actions"] == [{
        "action": "Confirm held: call event-001 held >= 20 minutes with a decision-maker present",
        "effect": (
            "the held confirmation evidence is recorded and this subject "
            "counts as held on the next daily run"
        ),
        "reply": "approve sales-held-event-001",
    }]
    assert packet["context"]["decision_id"] == "sales-held-event-001"
    queue = _rows(state / "held_confirm_queue.jsonl")
    assert queue == [{
        "decision_id": "sales-held-event-001",
        "event_id": "event-001",
        "kind": "held_confirm",
        "minutes": 25,
        "queued_at": NOW.isoformat(),
        "start": (NOW - timedelta(hours=1)).isoformat(),
        "status": "pending_approval",
        "subject_id": _subject(),
    }]


def test_no_card_when_not_attended_future_unbooked_or_source_attested(tmp_path, monkeypatch):
    cases = [
        ({"attended": False}, True),
        ({"start": (NOW + timedelta(hours=2)).isoformat()}, True),
        ({"decision_maker_present": True}, True),
        ({}, False),  # attended + past + dm-false but never booked
    ]
    for index, (overrides, booked) in enumerate(cases):
        case_dir = tmp_path / f"case{index}"
        case_dir.mkdir()
        state = _state(case_dir, _event(**overrides))
        outbox = _outbox(case_dir)
        _silence_records(state, monkeypatch)
        if booked:
            _booked(state)
        else:
            _qualified(state)
        result = held_confirm_card.run(state, outbox=outbox, now=NOW)
        assert result["metrics"]["asked"] == 0, overrides
        assert _rows(outbox) == [], overrides


def test_approve_becomes_confirmation_and_held_receipt(tmp_path, monkeypatch):
    state = _state(tmp_path, _event())
    outbox = _outbox(tmp_path)
    _silence_records(state, monkeypatch)
    _booked(state)
    held_confirm_card.run(state, outbox=outbox, now=NOW)

    verdict = apply(state / "held_confirm_queue.jsonl", "sales-held-event-001", "APPROVE")
    assert verdict == {"applied": True, "status": "approved"}

    applied = held_confirm_card.run(state, outbox=outbox, now=NOW)
    assert applied["metrics"]["confirmed"] == 1
    confirmations = _rows(state / "held_confirmations.jsonl")
    assert confirmations == [{
        "confirmed": True,
        "decision_id": "sales-held-event-001",
        "event_id": "event-001",
        "subject_id": _subject(),
        "ts": NOW.isoformat(),
    }]

    held_result = held_sensor.run(state, now=NOW)
    assert held_result["metrics"]["held"] == 1
    assert _rows(state / "held.jsonl") == [{
        "bar": "services", "confirmed_by": "sales-held-event-001",
        "event_id": "event-001", "minutes": 25, "source": "icaregrow",
        "subject_id": _subject(), "ts": NOW.isoformat(),
    }]
    assert any(
        row["from_stage"] == "booked" and row["to_stage"] == "held"
        for row in _rows(state / "events.jsonl")
    )


def test_reject_records_decline_and_never_holds_or_recards(tmp_path, monkeypatch):
    state = _state(tmp_path, _event())
    outbox = _outbox(tmp_path)
    _silence_records(state, monkeypatch)
    _booked(state)
    held_confirm_card.run(state, outbox=outbox, now=NOW)

    verdict = apply(state / "held_confirm_queue.jsonl", "sales-held-event-001", "REJECT")
    assert verdict == {"applied": True, "status": "rejected"}

    applied = held_confirm_card.run(state, outbox=outbox, now=NOW)
    assert applied["metrics"]["declined"] == 1
    assert applied["metrics"]["asked"] == 0
    assert _rows(state / "held_confirmations.jsonl") == [{
        "confirmed": False,
        "decision_id": "sales-held-event-001",
        "event_id": "event-001",
        "subject_id": _subject(),
        "ts": NOW.isoformat(),
    }]

    held_sensor.run(state, now=NOW)
    assert not any(row["to_stage"] == "held" for row in _rows(state / "events.jsonl"))
    assert _rows(state / "held.jsonl") == []
    assert len(_rows(outbox)) == 1


def test_crash_leftover_card_requeues_without_duplicate_card(tmp_path, monkeypatch):
    state = _state(tmp_path, _event())
    outbox = _outbox(tmp_path)
    _silence_records(state, monkeypatch)
    _booked(state)
    held_confirm_card.run(state, outbox=outbox, now=NOW)
    # Simulate the crash window: the packet landed but the queue row did not.
    (state / "held_confirm_queue.jsonl").unlink()

    result = held_confirm_card.run(state, outbox=outbox, now=NOW)

    assert result["metrics"]["asked"] == 1
    assert len(_rows(outbox)) == 1
    assert len(_rows(state / "held_confirm_queue.jsonl")) == 1


def test_one_card_per_subject_most_recent_then_decline_falls_back(tmp_path, monkeypatch):
    state = _state(tmp_path, _event())
    older = _event(event_id="event-000", start=(NOW - timedelta(days=3)).isoformat())
    newer = _event()
    (state / "sources" / "calendar_events.json").write_text(
        json.dumps({"events": [older, newer]}), encoding="utf-8"
    )
    outbox = _outbox(tmp_path)
    _silence_records(state, monkeypatch)
    _booked(state)

    first = held_confirm_card.run(state, outbox=outbox, now=NOW)
    assert first["metrics"]["asked"] == 1
    assert first["metrics"]["deferred"] == 1
    queue = _rows(state / "held_confirm_queue.jsonl")
    assert [row["event_id"] for row in queue] == ["event-001"]

    # While the ask is pending, nothing stacks.
    stacked = held_confirm_card.run(state, outbox=outbox, now=NOW)
    assert stacked["metrics"]["asked"] == 0

    apply(state / "held_confirm_queue.jsonl", "sales-held-event-001", "REJECT")
    fallback = held_confirm_card.run(state, outbox=outbox, now=NOW)
    assert fallback["metrics"]["asked"] == 1
    queue = _rows(state / "held_confirm_queue.jsonl")
    assert [row["event_id"] for row in queue] == ["event-001", "event-000"]
    assert len(_rows(outbox)) == 2


def test_approved_subject_never_gets_a_fallback_card_before_held(tmp_path, monkeypatch):
    """Shadow-found defect: between APPROVE and the held_sensor run, the
    subject is not yet held — that window must not re-card an older event."""
    state = _state(tmp_path, _event())
    older = _event(event_id="event-000", start=(NOW - timedelta(days=3)).isoformat())
    (state / "sources" / "calendar_events.json").write_text(
        json.dumps({"events": [older, _event()]}), encoding="utf-8"
    )
    outbox = _outbox(tmp_path)
    _silence_records(state, monkeypatch)
    _booked(state)
    held_confirm_card.run(state, outbox=outbox, now=NOW)
    apply(state / "held_confirm_queue.jsonl", "sales-held-event-001", "APPROVE")

    applied = held_confirm_card.run(state, outbox=outbox, now=NOW)

    assert applied["metrics"]["confirmed"] == 1
    assert applied["metrics"]["asked"] == 0
    assert len(_rows(outbox)) == 1
    held_sensor.run(state, now=NOW)
    assert len(_rows(state / "held.jsonl")) == 1


def test_source_attested_events_still_hold_without_confirmation(tmp_path, monkeypatch):
    state = _state(tmp_path, _event(decision_maker_present=True))
    _silence_records(state, monkeypatch)
    _booked(state)
    held_sensor.run(state, now=NOW)
    receipts = _rows(state / "held.jsonl")
    assert len(receipts) == 1
    assert "confirmed_by" not in receipts[0]


def test_confirmation_does_not_resurrect_short_unattended_calls(tmp_path, monkeypatch):
    state = _state(tmp_path, _event(attended=False))
    _silence_records(state, monkeypatch)
    _booked(state)
    (state / "held_confirmations.jsonl").write_text(
        json.dumps({
            "confirmed": True, "decision_id": "sales-held-event-001",
            "event_id": "event-001", "subject_id": _subject(), "ts": NOW.isoformat(),
        }) + "\n",
        encoding="utf-8",
    )
    held_sensor.run(state, now=NOW)
    assert _rows(state / "held.jsonl") == []


def test_written_outputs_contain_no_pii(tmp_path, monkeypatch):
    state = _state(tmp_path, _event())
    outbox = _outbox(tmp_path)
    _silence_records(state, monkeypatch)
    _booked(state)
    held_confirm_card.run(state, outbox=outbox, now=NOW)
    apply(state / "held_confirm_queue.jsonl", "sales-held-event-001", "APPROVE")
    held_confirm_card.run(state, outbox=outbox, now=NOW)
    held_sensor.run(state, now=NOW)
    for path in [*state.iterdir(), outbox]:
        if path.is_file():
            assert EMAIL not in path.read_text(encoding="utf-8"), path
