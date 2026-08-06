from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from factory.charter_loader import CharterError, funnel_config
from factory.events_ledger import append_event
from factory.floor_compiler import compile_floors


NOW = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)


def _charter(dept: Path, funnel=None):
    value = {
        "department": dept.name,
        "owner": "owner",
        "autonomy_state": "shadow",
        "immutable_safety_invariants": {"heal_may_not_modify": ["charter"]},
    }
    if funnel is not None:
        value["funnel"] = funnel
    dept.mkdir(parents=True, exist_ok=True)
    (dept / "charter.yaml").write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _funnel():
    return {
        "end_goal": {"stage": "published", "per_week": 10},
        "transitions": [
            {"from": "contacted", "to": "booked", "prior_rate": 0.5, "buffer": 0.1, "lead_days": 7, "maturity_days": 14, "stock_buffer": 0.2},
            {"from": "booked", "to": "recorded", "prior_rate": 0.8, "buffer": 0.05, "lead_days": 14, "maturity_days": 7, "stock_buffer": 0.1},
            {"from": "recorded", "to": "published", "prior_rate": 0.9, "buffer": 0.0, "lead_days": 7, "maturity_days": 3, "stock_buffer": 0.0},
        ],
    }


def _setup(tmp_path, funnel=True):
    dept, state = tmp_path / "demo", tmp_path / "state"
    _charter(dept, _funnel() if funnel else None)
    state.mkdir()
    return dept, state


def test_unconfigured_without_funnel(tmp_path):
    dept, state = _setup(tmp_path, funnel=False)
    result = compile_floors(dept, state, now=NOW)
    assert result["status"] == "unconfigured"
    assert result["floors"] == {}
    assert not (dept / "floors.yaml").exists()


def test_golden_three_transition_ceil_cascade(tmp_path):
    dept, state = _setup(tmp_path)
    result = compile_floors(dept, state, now=NOW)
    # Hand calculation downstream to upstream:
    # recorded=ceil(10/.9)=12; booked=ceil(12/.8*1.05)=16;
    # contacted=ceil(16/.5*1.1)=36. Stocks: 12, 36, 44 respectively.
    assert result["floors"] == {
        "published": {"flow_per_week": 10, "stock_min": 0, "rate_used": 1.0, "rate_source": "prior"},
        "recorded": {"flow_per_week": 12, "stock_min": 12, "rate_used": 0.9, "rate_source": "prior"},
        "booked": {"flow_per_week": 16, "stock_min": 36, "rate_used": 0.8, "rate_source": "prior"},
        "contacted": {"flow_per_week": 36, "stock_min": 44, "rate_used": 0.5, "rate_source": "prior"},
    }


def _cohort(state, count, conversions, *, age_days=20):
    entered = NOW - timedelta(days=age_days)
    for number in range(count):
        subject = f"{number:016x}"
        append_event(state, subject_id=subject, from_stage="sourced", to_stage="contacted", ts=entered)
        if number < conversions:
            append_event(state, subject_id=subject, from_stage="contacted", to_stage="booked", ts=entered + timedelta(days=1))


def test_measured_rate_blends_at_30_entrants_and_10_conversions(tmp_path):
    dept, state = _setup(tmp_path)
    _cohort(state, 30, 15)
    result = compile_floors(dept, state, now=NOW)
    row = result["floors"]["contacted"]
    assert row["rate_used"] == pytest.approx(0.5)
    assert row["rate_source"] == "blended"


def test_immature_or_29_entrant_cohort_stays_on_prior(tmp_path):
    dept, state = _setup(tmp_path)
    _cohort(state, 29, 15)
    result = compile_floors(dept, state, now=NOW)
    assert result["floors"]["contacted"]["rate_source"] == "prior"


def test_twenty_percent_cap_records_each_floor_move(tmp_path):
    dept, state = _setup(tmp_path)
    first = compile_floors(dept, state, now=NOW - timedelta(days=8))
    _cohort(state, 30, 30)
    second = compile_floors(dept, state, now=NOW)
    assert second["floors"]["contacted"]["flow_per_week"] == 29  # ceil(36 * .8)
    assert {tuple(change.values()) for change in second["changes"]} >= {
        ("contacted", "flow_per_week", 36, 29),
    }
    assert first["status"] == "ok"


def test_freezes_on_objective_drift(tmp_path):
    dept, state = _setup(tmp_path)
    prior = compile_floors(dept, state, now=NOW - timedelta(days=8))
    (state / "objectives_observed.json").write_text(json.dumps({"schema": "objectives-observed/v1", "values": {"state_drift": 1, "unledgered_inbound": 0}}))
    result = compile_floors(dept, state, now=NOW)
    assert result["status"] == "frozen"
    assert result["floors"] == prior["floors"]
    assert result["changes"] == []


def test_freezes_on_newest_red_run_manifest_verdict(tmp_path):
    dept, state = _setup(tmp_path)
    compile_floors(dept, state, now=NOW - timedelta(days=8))
    manifests = state / "run-manifests"
    manifests.mkdir()
    (manifests / "run-1.json").write_text(json.dumps({"run_id": "run-1"}))
    (manifests / "run-1.verdict.json").write_text(json.dumps({"status": "red"}))
    assert compile_floors(dept, state, now=NOW)["status"] == "frozen"


def test_freezes_on_malformed_ledger_line(tmp_path):
    dept, state = _setup(tmp_path)
    compile_floors(dept, state, now=NOW - timedelta(days=8))
    (state / "events.jsonl").write_text("not-json\n", encoding="utf-8")
    assert compile_floors(dept, state, now=NOW)["status"] == "frozen"


def test_floors_yaml_has_machine_header(tmp_path):
    dept, state = _setup(tmp_path)
    compile_floors(dept, state, now=NOW)
    assert (dept / "floors.yaml").read_text().startswith("# MACHINE-WRITTEN — derived; humans set goals in charter.yaml\n")


def test_history_appends_once_per_compile(tmp_path):
    dept, state = _setup(tmp_path)
    compile_floors(dept, state, now=NOW)
    compile_floors(dept, state, now=NOW + timedelta(days=8))
    lines = (state / "floors-history.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert all("computed_at" in json.loads(line) for line in lines)


def test_funnel_config_absent_returns_none():
    assert funnel_config({}) is None


def test_funnel_config_parses_documented_shape():
    assert funnel_config({"funnel": _funnel()}) == _funnel()


@pytest.mark.parametrize("mutation", ["rate", "negative_days", "unknown"])
def test_funnel_config_rejects_invalid_values_and_unknown_keys(mutation):
    value = _funnel()
    if mutation == "rate":
        value["transitions"][0]["prior_rate"] = 1.5
    elif mutation == "negative_days":
        value["transitions"][0]["lead_days"] = -1
    else:
        value["transitions"][0]["surprise"] = True
    with pytest.raises(CharterError):
        funnel_config({"funnel": value})


def test_funnel_config_rejects_broken_or_downstream_first_chain():
    # Caught live 2026-08-06: a downstream-first list compiled silently
    # INVERTED floors. The loader must fail closed on any chain break.
    value = _funnel()
    value["transitions"] = list(reversed(value["transitions"]))
    with pytest.raises(CharterError, match="upstream-first"):
        funnel_config({"funnel": value})
    value = _funnel()
    value["transitions"][-1]["to"] = "elsewhere"
    with pytest.raises(CharterError, match="end_goal.stage"):
        funnel_config({"funnel": value})
