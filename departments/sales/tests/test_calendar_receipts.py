from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory.events_ledger import append_event
from departments.sales.runtime import booked_sensor, held_sensor


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
        "decision_maker_present": True,
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


def test_booked_happy_path_fast_path_and_idempotent_rerun(tmp_path, monkeypatch):
    state = _state(tmp_path, _event())
    _qualified(state)
    monkeypatch.setattr(booked_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")

    first = booked_sensor.run(state, now=NOW)
    second = booked_sensor.run(state, now=NOW)

    bookings = _rows(state / "bookings.jsonl")
    transitions = _rows(state / "events.jsonl")
    assert first["metrics"]["booked"] == 1
    assert second["metrics"]["booked"] == 0
    assert bookings == [{
        "event_id": "event-001", "fast_path": True,
        "start": (NOW - timedelta(hours=1)).isoformat(),
        "subject_id": _subject(), "ts": NOW.isoformat(),
    }]
    assert sum(row["to_stage"] == "booked" for row in transitions) == 1
    assert next(row for row in transitions if row["to_stage"] == "booked")["from_stage"] == "qualified"


def test_no_show_produces_no_held_transition(tmp_path, monkeypatch):
    state = _state(tmp_path, _event(attended=False))
    monkeypatch.setattr(booked_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")
    monkeypatch.setattr(held_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")
    _booked(state)
    held_sensor.run(state, now=NOW)
    assert not any(row["to_stage"] == "held" for row in _rows(state / "events.jsonl"))
    assert _rows(state / "held.jsonl") == []


def test_short_call_produces_no_held_transition(tmp_path, monkeypatch):
    state = _state(tmp_path, _event(minutes=15))
    monkeypatch.setattr(booked_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")
    monkeypatch.setattr(held_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")
    _booked(state)
    held_sensor.run(state, now=NOW)
    assert not any(row["to_stage"] == "held" for row in _rows(state / "events.jsonl"))


def test_held_happy_path_carries_bar_and_source_from_received_meta(tmp_path, monkeypatch):
    state = _state(tmp_path, _event())
    monkeypatch.setattr(booked_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")
    monkeypatch.setattr(held_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")
    _booked(state)
    result = held_sensor.run(state, now=NOW)
    assert result["status"] == "ok"
    assert _rows(state / "held.jsonl") == [{
        "bar": "services", "event_id": "event-001", "minutes": 25,
        "source": "icaregrow", "subject_id": _subject(), "ts": NOW.isoformat(),
    }]
    assert any(row["from_stage"] == "booked" and row["to_stage"] == "held" for row in _rows(state / "events.jsonl"))


def test_unresolvable_attribution_alarms_without_transition(tmp_path, monkeypatch):
    state = _state(tmp_path, _event())
    monkeypatch.setattr(booked_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")
    monkeypatch.setattr(held_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")
    _booked(state)
    (state / "qualifications.jsonl").write_text("", encoding="utf-8")
    result = held_sensor.run(state, now=NOW)
    assert result["status"] == "alarm"
    assert result["metrics"]["attribution_unresolved"] == 1
    assert not any(row["to_stage"] == "held" for row in _rows(state / "events.jsonl"))
    assert _rows(state / "held.jsonl") == []


def test_written_outputs_contain_no_pii(tmp_path, monkeypatch):
    state = _state(tmp_path, _event())
    monkeypatch.setattr(booked_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")
    monkeypatch.setattr(held_sensor.runrecord, "emit_record", lambda *a, **kw: state / "runs.jsonl")
    _booked(state)
    held_sensor.run(state, now=NOW)
    for path in state.iterdir():
        if path.is_file():
            assert EMAIL not in path.read_text(encoding="utf-8"), path
