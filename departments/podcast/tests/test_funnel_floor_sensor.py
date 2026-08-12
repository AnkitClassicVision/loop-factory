from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from departments.podcast.runtime import funnel_floor_sensor


NOW = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
LINES = {
    "active_warm_threads": 10,
    "live_replies": 4,
    "prep_done_awaiting_recording": 3,
    "recordings_booked_future": 2,
    "stale_touches": 0,
    "expired_holds_unactioned": 0,
}


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _ledger(pipeline: Path, people: list[dict] | None = None) -> Path:
    return _write(
        pipeline / "episodes" / "FUNNEL-LEDGER.json",
        {"schema": "funnel-ledger/v1", "people": people or []},
    )


def _episode(pipeline: Path, number: int, *, dated: bool = True) -> None:
    value = {"stage": "recording-booked"}
    if dated:
        value["recording_date"] = "2026-08-06"
    _write(pipeline / "episodes" / f"ep-{number}" / "episode.json", value)


def _person_for(subject: str, number: int) -> dict:
    person = {"id": str(number), "name": f"Person {number}", "stage": "closed"}
    if subject == "active_warm_threads":
        person.update(stage="contacted", last_touch={"at": "2026-08-04T12:00:00Z", "direction": "outbound"})
    elif subject == "live_replies":
        person.update(stage="contacted", last_touch={"at": "2026-08-04T12:00:00Z", "direction": "inbound"})
    elif subject == "prep_done_awaiting_recording":
        person.update(stage="prep_call_done")
    elif subject == "stale_touches":
        person.update(stage="contacted", last_touch={"at": "2026-07-20T12:00:00Z", "direction": "outbound"})
    elif subject == "expired_holds_unactioned":
        person.update(stage="contacted", hold={"next_action_on": "2026-08-01"})
    return person


@pytest.mark.parametrize("subject", LINES)
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_each_floor_computes_at_above_and_below_its_line(tmp_path, subject, delta):
    line = LINES[subject]
    count = max(0, line + delta)
    pipeline = tmp_path / "pipeline"
    people = [] if subject == "recordings_booked_future" else [
        _person_for(subject, number) for number in range(count)
    ]
    _ledger(pipeline, people)
    episode_count = count if subject == "recordings_booked_future" else 1
    for number in range(episode_count):
        _episode(pipeline, number)

    values, _details = funnel_floor_sensor._measure(people, pipeline, NOW)

    assert values[subject] == count
    expected = (
        "ok"
        if (count <= line if subject in {"stale_touches", "expired_holds_unactioned"} else count >= line)
        else "alarm"
    )
    assert funnel_floor_sensor._status(subject, count, line) == expected


def test_missing_ledger_makes_every_floor_and_quota_unknown(tmp_path):
    observations = funnel_floor_sensor.run(
        tmp_path / "state", tmp_path / "sources", tmp_path / "pipeline", now=NOW
    )

    assert len(observations) == 7
    assert {row["status"] for row in observations} == {"unknown"}
    assert all("unreadable source" in row["detail"] for row in observations)


def test_undatable_recording_is_unknown_without_guessing_zero(tmp_path):
    pipeline = tmp_path / "pipeline"
    _ledger(pipeline)
    _episode(pipeline, 1, dated=False)

    observations = funnel_floor_sensor.run(
        tmp_path / "state", tmp_path / "sources", pipeline, now=NOW
    )
    recording = next(row for row in observations if row["subject"] == "recordings_booked_future")

    assert recording["status"] == "unknown"
    assert recording["metrics"]["count"] is None
    assert "no recording-booked episode has usable date evidence" in recording["detail"]


@pytest.mark.parametrize(
    ("hopper_depth", "expected_status", "expected_quota"),
    [(5, "alarm", 8), (6, "ok", 4)],
)
def test_quota_switches_between_rebuild_and_steady(tmp_path, hopper_depth, expected_status, expected_quota):
    pipeline = tmp_path / "pipeline"
    people = [
        {
            "id": str(number),
            "name": f"Person {number}",
            "stage": "contacted",
            "last_touch": {"at": "2026-08-04T12:00:00Z", "direction": "outbound"},
        }
        for number in range(5)
    ]
    _ledger(pipeline, people)
    _episode(pipeline, 1)
    state = tmp_path / "state"
    _write(
        state / "objectives_observed.json",
        {"schema": "objectives-observed/v1", "ts": "old", "values": {"hopper_depth": hopper_depth}},
    )

    observations = funnel_floor_sensor.run(state, tmp_path / "sources", pipeline, now=NOW)
    quota = next(row for row in observations if row["subject"] == "new_outreach_this_week")

    assert quota["status"] == expected_status
    assert quota["metrics"] == {"count": 5, "floor": expected_quota}


def test_expired_hold_with_post_expiry_touch_is_actioned(tmp_path):
    pipeline = tmp_path / "pipeline"
    people = [
        {
            "id": "actioned",
            "name": "Actioned",
            "stage": "contacted",
            "hold": {"next_action_on": "2026-08-01"},
            "last_touch": {"at": "2026-08-02T12:00:00Z", "direction": "outbound"},
        },
        {
            "id": "missed",
            "name": "Missed",
            "stage": "contacted",
            "hold": {"next_action_on": "2026-08-01"},
            "last_touch": {"at": "2026-07-31T12:00:00Z", "direction": "outbound"},
        },
    ]
    _ledger(pipeline, people)
    _episode(pipeline, 1)

    values, _details = funnel_floor_sensor._measure(people, pipeline, NOW)

    assert values["expired_holds_unactioned"] == 1
