from __future__ import annotations

import json
from pathlib import Path

import pytest

from departments.sales.runtime import intake_sensor, qualify_scorer


LANES = ("icaregrow", "podcast_handoffs", "pfs_warm", "website_forms", "luma")


def _row(email: str, *, role="owner", icp_fit=True, exit_intent=False, ts="2026-08-06T12:00:00Z"):
    return {
        "email": email,
        "name": "Synthetic Person",
        "role": role,
        "icp_fit": icp_fit,
        "exit_intent": exit_intent,
        "ts": ts,
    }


def _state(tmp_path: Path, lanes: dict[str, list[dict]]) -> Path:
    state = tmp_path / "state"
    sources = state / "sources"
    sources.mkdir(parents=True)
    (sources / ".id_salt").write_text("fixture-salt", encoding="utf-8")
    for lane, rows in lanes.items():
        (sources / f"{lane}.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return state


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_intake_happy_path_is_attributed_opaque_and_idempotent(tmp_path):
    state = _state(tmp_path, {"icaregrow": [_row("Owner@Example.test")]})
    intake_sensor.run(state)
    intake_sensor.run(state)

    events = _jsonl(state / "events.jsonl")
    assert len(events) == 1
    assert events[0]["from_stage"] == "arrival"
    assert events[0]["to_stage"] == "received"
    assert events[0]["meta"]["source"] == "icaregrow"
    assert events[0]["meta"]["cohort"] == "2026-W32"
    assert len(events[0]["subject_id"]) == 16
    assert "@" not in events[0]["subject_id"]


def test_cross_lane_duplicate_uses_priority_and_is_observed(tmp_path):
    duplicate = _row("duplicate@example.test")
    state = _state(tmp_path, {"icaregrow": [duplicate], "luma": [duplicate]})
    observations = intake_sensor.run(state)

    assert _jsonl(state / "events.jsonl")[0]["meta"]["source"] == "icaregrow"
    assert observations[-1]["metrics"]["cross_lane_duplicates"] == 1
    assert observations[-1]["metrics"]["priority_lane_wins"] == {"icaregrow": 1}


def test_missing_lane_files_are_visible_but_not_fatal(tmp_path):
    state = _state(tmp_path, {"icaregrow": [_row("one@example.test")]})
    observation = intake_sensor.run(state)[-1]
    assert observation["status"] == "ok"
    assert set(observation["metrics"]["missing_lanes"]) == set(LANES) - {"icaregrow"}
    assert "missing lane files" in observation["detail"]


def test_unparseable_lane_is_an_alarm(tmp_path):
    state = _state(tmp_path, {})
    (state / "sources" / "luma.json").write_text("not json", encoding="utf-8")
    observation = intake_sensor.run(state)[-1]
    assert observation["status"] == "alarm"
    assert observation["metrics"]["unparseable_lanes"] == ["luma"]


def test_missing_salt_crashes(tmp_path):
    state = _state(tmp_path, {"icaregrow": [_row("one@example.test")]})
    (state / "sources" / ".id_salt").unlink()
    with pytest.raises(FileNotFoundError):
        intake_sensor.run(state)


def test_qualify_services_and_seller_bars_both_directions(tmp_path):
    state = _state(tmp_path, {
        "icaregrow": [
            _row("services-yes@example.test", role="owner", icp_fit=True),
            _row("services-no@example.test", role="staff", icp_fit=True),
        ],
        "pfs_warm": [
            _row("seller-yes1@example.test", role="decision_maker", exit_intent=True),
            _row("seller-no1@example.test", role="owner", exit_intent=False),
        ],
    })
    intake_sensor.run(state)
    qualify_scorer.run(state)

    qualified = _jsonl(state / "qualifications.jsonl")
    parked = _jsonl(state / "parked_out.jsonl")
    assert {row["bar"] for row in qualified} == {"services", "seller"}
    assert len(qualified) == 2
    assert len(parked) == 2
    events = _jsonl(state / "events.jsonl")
    assert sum(row["to_stage"] == "qualified" for row in events) == 2

    qualify_scorer.run(state)
    assert len(_jsonl(state / "qualifications.jsonl")) == 2


def test_no_written_output_contains_raw_email_or_name(tmp_path):
    email = "pii-person@example.test"
    name = "PII Fixture Name"
    row = _row(email)
    row["name"] = name
    parked_email = "parked-person@example.test"
    parked_name = "Parked Fixture Name"
    parked_row = _row(parked_email, role="staff")
    parked_row["name"] = parked_name
    state = _state(tmp_path, {"icaregrow": [row, parked_row]})
    intake_sensor.run(state)
    qualify_scorer.run(state)

    for filename in ("events.jsonl", "qualifications.jsonl", "parked_out.jsonl", "observations.jsonl"):
        path = state / filename
        written = path.read_text(encoding="utf-8")
        assert email not in written
        assert name not in written
        assert parked_email not in written
        assert parked_name not in written
