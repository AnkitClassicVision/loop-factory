from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from departments.sales.runtime import sense_gates


NOW = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)
SALT = "dummy-fixture-salt"


def _subject(email: str) -> str:
    return hashlib.sha256((SALT + email.strip().lower()).encode()).hexdigest()[:16]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    state = tmp_path / "state"
    dept = tmp_path / "sales"
    (state / "sources").mkdir(parents=True)
    (state / "sources" / ".id_salt").write_text(SALT, encoding="utf-8")
    _write_json(state / "sources" / "threads.json", {"threads": []})
    charter = yaml.safe_load((REPO_ROOT / "departments/sales/charter.yaml").read_text(encoding="utf-8"))
    (dept / "charter.yaml").parent.mkdir(parents=True, exist_ok=True)
    (dept / "charter.yaml").write_text(yaml.safe_dump(charter), encoding="utf-8")
    return state, dept


def _events(state: Path, rows: list[dict]) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _event(subject: str, to_stage: str, ts: str, source: str | None = None) -> dict:
    return {
        "subject_id": subject,
        "from_stage": "received" if to_stage != "received" else "arrival",
        "to_stage": to_stage,
        "ts": ts,
        "meta": {} if source is None else {"source": source},
    }


def _by_subject(observations: list[dict], subject: str) -> dict:
    return next(row for row in observations if row["subject"] == subject)


def test_stale_conversation_alarms_with_subject_and_fresh_does_not(tmp_path, monkeypatch):
    state, dept = _setup(tmp_path)
    stale_email = "stale@example.test"
    fresh_email = "fresh@example.test"
    _events(state, [
        _event(_subject(stale_email), "conversation_live", "2026-08-01T10:00:00+00:00"),
        _event(_subject(fresh_email), "conversation_live", "2026-08-01T11:00:00+00:00"),
    ])
    _write_json(state / "sources/threads.json", {"threads": [
        {"email": stale_email, "last_two_way_ts": "2026-07-29T12:00:00+00:00", "kind": "reply"},
        {"email": fresh_email, "last_two_way_ts": "2026-08-05T12:00:00+00:00", "kind": "live"},
    ]})
    emitted = []
    monkeypatch.setattr(sense_gates.runrecord, "emit_record", lambda *a, **kw: emitted.append((a, kw)))

    observations = sense_gates.run(state, dept, now=NOW)

    stale = _by_subject(observations, "conversation_staleness")
    assert stale["status"] == "alarm"
    assert stale["metrics"] == {"count": 1, "subject_ids": [_subject(stale_email)]}
    assert _subject(fresh_email) not in stale["metrics"]["subject_ids"]
    assert len(emitted) == 1


def test_cross_lane_collision_inside_window_alarms_outside_does_not(tmp_path, monkeypatch):
    state, dept = _setup(tmp_path)
    inside = _subject("inside@example.test")
    outside = _subject("outside@example.test")
    _events(state, [
        _event(inside, "received", "2026-08-01T10:00:00+00:00", "icaregrow"),
        _event(inside, "received", "2026-08-04T10:00:00+00:00", "luma"),
        _event(outside, "received", "2026-07-20T10:00:00+00:00", "icaregrow"),
        _event(outside, "received", "2026-07-26T10:00:00+00:00", "luma"),
    ])
    monkeypatch.setattr(sense_gates.runrecord, "emit_record", lambda *a, **kw: None)

    collisions = _by_subject(sense_gates.run(state, dept, now=NOW), "cross_lane_double_touch")

    assert collisions["status"] == "alarm"
    assert collisions["metrics"]["count"] == 1
    assert collisions["metrics"]["collision_pairs"] == [
        {"subject_id": inside, "lanes": ["icaregrow", "luma"]}
    ]


def test_missing_floors_is_unknown_and_record_is_always_emitted(tmp_path, monkeypatch):
    state, dept = _setup(tmp_path)
    emitted = []
    monkeypatch.setattr(sense_gates.runrecord, "emit_record", lambda *a, **kw: emitted.append(kw))

    floors = _by_subject(sense_gates.run(state, dept, now=NOW), "floors_attainment")

    assert floors["status"] == "unknown"
    assert len(emitted) == 1
    assert emitted[0]["status"] == "ok"


def test_short_week_alarms_with_exact_shortfalls(tmp_path, monkeypatch):
    state, dept = _setup(tmp_path)
    sid = _subject("flow@example.test")
    _events(state, [
        _event(sid, "qualified", "2026-08-03T10:00:00+00:00"),
        _event(sid, "qualified", "2026-07-30T10:00:00+00:00"),
    ])
    (dept / "floors.yaml").write_text(yaml.safe_dump({"floors": {
        "qualified": {"flow_per_week": 3}, "held": {"flow_per_week": 2}
    }}), encoding="utf-8")
    monkeypatch.setattr(sense_gates.runrecord, "emit_record", lambda *a, **kw: None)

    floors = _by_subject(sense_gates.run(state, dept, now=NOW), "floors_attainment")

    assert floors["status"] == "alarm"
    assert floors["metrics"]["shortfalls"] == {
        "held": {"actual": 0, "required": 2, "shortfall": 2},
        "qualified": {"actual": 1, "required": 3, "shortfall": 2},
    }


def test_healthy_week_is_ok_and_context_coverage_is_vacuously_ok(tmp_path, monkeypatch):
    state, dept = _setup(tmp_path)
    sid = _subject("healthy@example.test")
    _events(state, [_event(sid, "qualified", "2026-08-03T10:00:00+00:00")])
    (dept / "floors.yaml").write_text(
        yaml.safe_dump({"floors": {"qualified": {"flow_per_week": 1}}}), encoding="utf-8"
    )
    monkeypatch.setattr(sense_gates.runrecord, "emit_record", lambda *a, **kw: None)

    observations = sense_gates.run(state, dept, now=NOW)

    assert _by_subject(observations, "floors_attainment")["status"] == "ok"
    coverage = _by_subject(observations, "context_voice_coverage")
    assert coverage["status"] == "ok"
    assert coverage["metrics"] == {"drafts": 0, "checked": 0}


def test_observations_contain_no_source_pii(tmp_path, monkeypatch):
    state, dept = _setup(tmp_path)
    email = "private.person@example.test"
    _events(state, [_event(_subject(email), "conversation_live", "2026-07-20T10:00:00+00:00")])
    _write_json(state / "sources/threads.json", {"threads": [{
        "email": email, "last_two_way_ts": "2026-07-21T10:00:00+00:00", "kind": "reply"
    }]})
    monkeypatch.setattr(sense_gates.runrecord, "emit_record", lambda *a, **kw: None)

    observations = sense_gates.run(state, dept, now=NOW)

    assert email not in json.dumps(observations)
    assert "private.person" not in (state / "observations.jsonl").read_text(encoding="utf-8")
