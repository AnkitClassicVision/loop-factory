from __future__ import annotations

import json
from pathlib import Path

from factory import rollup
from factory.board import render_board
from factory.boardfeed import build_feed


NOW = "2026-08-02T20:00:00+00:00"
OBJECTIVE_ID = "service_quality"


def _department(root: Path, name: str = "demodept") -> Path:
    department = root / "departments" / name
    state = department / "state"
    state.mkdir(parents=True)
    (state / "STATE.json").write_text(
        json.dumps(
            {
                "department": name,
                "epoch": 4,
                "last_cycle_at": "2026-08-02T19:30:00+00:00",
                "autonomy_state": "shadow",
                "open_findings": [],
                "escalations": 0,
            }
        ),
        encoding="utf-8",
    )
    (state / "heartbeats.jsonl").write_text(
        json.dumps({"ts": "2026-08-02T19:30:01+00:00", "ok": True}) + "\n",
        encoding="utf-8",
    )
    (department / "charter.yaml").write_text(
        f"""department: {name}
owner: owner
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [autonomy_state]
setpoints:
  objectives:
    {OBJECTIVE_ID}:
      label: Service quality
      setpoint: 100
      minimum: 80
      target: 100
      unit: percent
    unmeasured_quality:
      label: Unmeasured quality
      minimum: 50
      target: 75
""",
        encoding="utf-8",
    )
    return department


def _observed(department: Path, value, *, ts: str = "2026-08-02T19:00:00+00:00") -> None:
    (department / "state" / "objectives_observed.json").write_text(
        json.dumps(
            {
                "schema": "objectives-observed/v1",
                "ts": ts,
                "values": {OBJECTIVE_ID: value},
            }
        ),
        encoding="utf-8",
    )


def _build(root: Path) -> tuple[list[dict], dict]:
    result = rollup.rebuild(root)
    assert result["complete"] is True
    output = root / "feed.ndjson"
    receipt = build_feed(root, out=output, now=NOW)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    return rows, receipt


def _objective(rows: list[dict], objective_id: str = OBJECTIVE_ID) -> dict:
    return next(
        row
        for row in rows
        if row["kind"] == "metrics"
        and row["data"].get("metric_type") == "objective"
        and row["data"].get("objective_id") == objective_id
    )


def _breaches(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if row["kind"] == "andon"
        and row["data"].get("code") == "OBJECTIVE_BELOW_MIN"
    ]


def test_observed_value_merges_onto_matching_objective_only(tmp_path):
    department = _department(tmp_path)
    _observed(department, 92)

    rows, _ = _build(tmp_path)

    measured = _objective(rows)
    assert measured["data"]["observed"] == 92
    assert measured["data"]["observed_ts"] == "2026-08-02T19:00:00+00:00"
    assert _objective(rows, "unmeasured_quality")["data"]["observed"] == "unknown"


def test_missing_observed_file_leaves_objectives_unknown(tmp_path):
    _department(tmp_path)

    rows, receipt = _build(tmp_path)

    assert _objective(rows)["data"]["observed"] == "unknown"
    assert receipt["malformed"] == 0


def test_malformed_observed_file_counts_in_feed_health_and_leaves_unknown(tmp_path):
    department = _department(tmp_path)
    (department / "state" / "objectives_observed.json").write_text(
        "{not-json}\n", encoding="utf-8"
    )

    rows, receipt = _build(tmp_path)

    assert _objective(rows)["data"]["observed"] == "unknown"
    assert rows[-1]["kind"] == "feed_health"
    assert rows[-1]["data"]["malformed"] == 1
    assert receipt["malformed"] == 1


def test_below_minimum_numeric_observed_emits_breach_with_exact_detail(tmp_path):
    department = _department(tmp_path)
    _observed(department, 79)

    rows, _ = _build(tmp_path)

    breaches = _breaches(rows)
    assert len(breaches) == 1
    assert breaches[0]["department"] == "demodept"
    assert breaches[0]["data"] == {
        "code": "OBJECTIVE_BELOW_MIN",
        "severity": "breach",
        "detail": "Service quality: observed 79 below minimum 80",
        "observed": 79,
        "setpoint": 80,
    }


def test_at_minimum_observed_emits_no_breach(tmp_path):
    department = _department(tmp_path)
    _observed(department, 80)

    rows, _ = _build(tmp_path)

    assert _breaches(rows) == []


def test_unknown_observed_emits_no_breach(tmp_path):
    department = _department(tmp_path)
    _observed(department, "unknown")

    rows, _ = _build(tmp_path)

    assert _objective(rows)["data"]["observed"] == "unknown"
    assert _breaches(rows) == []


def test_observation_older_than_48_hours_is_stale_but_still_visible(tmp_path):
    department = _department(tmp_path)
    _observed(department, 92, ts="2026-07-31T19:59:59+00:00")

    rows, _ = _build(tmp_path)

    data = _objective(rows)["data"]
    assert data["observed"] == 92
    assert data["observed_ts"] == "2026-07-31T19:59:59+00:00"
    assert data["stale"] is True


def test_board_renders_measured_headline_and_uses_it_for_bullet_value(tmp_path):
    department = _department(tmp_path)
    _observed(department, 92)
    _build(tmp_path)

    page = render_board(tmp_path / "feed.ndjson", tmp_path / "board.html")

    assert '<div class="fig num">92<span class="unit">percent</span></div>' in page
    assert '<span class="val" style="width:80%"></span>' in page
    assert "92 observed, minimum 80, target 100" in page


def test_board_appends_muted_stale_marker_to_measured_value(tmp_path):
    department = _department(tmp_path)
    _observed(department, 92, ts="2026-07-31T19:59:59+00:00")
    _build(tmp_path)

    page = render_board(tmp_path / "feed.ndjson", tmp_path / "board.html")

    assert '<span class="stale"> (stale)</span>' in page
    assert ".obj .fig .unit,.obj .fig .stale" in page


def test_breach_andon_renders_in_main_actions_zone(tmp_path):
    department = _department(tmp_path)
    _observed(department, 79)
    _build(tmp_path)

    page = render_board(tmp_path / "feed.ndjson", tmp_path / "board.html")
    main_actions = page.split('<section aria-label="Main actions">', 1)[1].split(
        '<section aria-label="Activity">', 1
    )[0]

    assert "OBJECTIVE_BELOW_MIN" in main_actions
    assert "Service quality: observed 79 below minimum 80" in main_actions
