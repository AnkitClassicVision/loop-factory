from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from factory.events_ledger import LedgerError, append_event, read_transitions


NOW = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)


def test_append_event_happy_path(tmp_path):
    path = append_event(
        tmp_path,
        subject_id="opaque-1",
        from_stage="lead",
        to_stage="qualified",
        ts=NOW,
        meta={"source": "fixture", "cohort": "august", "note": "clean"},
    )
    assert path == tmp_path / "events.jsonl"
    assert json.loads(path.read_text()) == {
        "subject_id": "opaque-1",
        "from_stage": "lead",
        "to_stage": "qualified",
        "ts": "2026-08-05T12:00:00+00:00",
        "meta": {"source": "fixture", "cohort": "august", "note": "clean"},
    }


@pytest.mark.parametrize("value", ["person@example.com", "call 2125551212"])
def test_append_rejects_email_or_phone_like_pii(tmp_path, value):
    with pytest.raises(LedgerError):
        append_event(tmp_path, subject_id="opaque", from_stage="lead", to_stage="won", meta={"note": value})


def test_append_rejects_disallowed_keys(tmp_path):
    with pytest.raises(LedgerError):
        append_event(tmp_path, subject_id="opaque", from_stage="lead", to_stage="won", meta={"email": "redacted"})


def test_append_rejects_same_utc_day_duplicate(tmp_path):
    append_event(tmp_path, subject_id="opaque", from_stage="lead", to_stage="won", ts=NOW)
    with pytest.raises(LedgerError):
        append_event(tmp_path, subject_id="opaque", from_stage="lead", to_stage="won", ts=NOW + timedelta(hours=2))


def test_read_window_counts_malformed_lines(tmp_path):
    append_event(tmp_path, subject_id="inside", from_stage="lead", to_stage="won", ts=NOW)
    append_event(tmp_path, subject_id="outside", from_stage="lead", to_stage="won", ts=NOW - timedelta(days=10))
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")
        handle.write(json.dumps({"subject_id": "broken"}) + "\n")
    window = read_transitions(
        tmp_path,
        from_stage="lead",
        to_stage="won",
        since=NOW - timedelta(days=1),
        until=NOW + timedelta(days=1),
    )
    assert [row["subject_id"] for row in window.rows] == ["inside"]
    assert window.malformed == 2
