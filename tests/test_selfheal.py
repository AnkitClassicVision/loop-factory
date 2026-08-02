"""Locked propose-only self-heal ladder contract."""
import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("selfheal", ROOT / "factory/selfheal.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S = _load()
NOW = datetime(2026, 8, 2, 12, 30, tzinfo=timezone.utc)


def _incident(**changes):
    incident = {
        "node": "draft",
        "incident_fingerprint": "incident-001",
        "failure_class": "transient",
        "transient": True,
        "playbook_candidate": "restart_known_unit",
        "fix_class": "node_code",
        "diagnosis": "bounded test diagnosis",
        "proposed_action": "change the bounded runtime node",
    }
    incident.update(changes)
    return incident


def _new_state():
    return S.load_state(Path("/definitely/absent/selfheal-test-state"))


def test_rung_progression_l0_through_l5_across_repeated_incident():
    state = _new_state()
    incident = _incident()
    observed = [S.next_rung(state, "draft", incident, now=NOW)["rung"] for _ in range(7)]
    assert observed == ["L0", "L1", "L2", "L3", "L4", "L5", "L5"]


def test_non_transient_incident_starts_at_matching_l1_playbook():
    state = _new_state()
    action = S.next_rung(
        state, "draft", _incident(transient=False, failure_class="task_error"), now=NOW
    )
    assert action["rung"] == "L1"
    assert "restart_known_unit" in action["reason"]


def test_always_human_fix_class_goes_straight_to_l5_with_class_named():
    for fix_class in S.ALWAYS_HUMAN:
        state = _new_state()
        action = S.next_rung(state, "draft", _incident(fix_class=fix_class), now=NOW)
        assert action["rung"] == "L5"
        assert fix_class in action["reason"]


def test_three_cumulative_patch_failures_demote_node():
    state = _new_state()
    for offset in range(3):
        S.record_patch_outcome(state, "draft", False, now=NOW + timedelta(hours=offset))
    node_state = state["nodes"]["draft"]
    assert node_state["failed_patch_attempts"] == 3
    assert node_state["demoted_until"] is not None


def test_demoted_node_skips_l2_and_l3_with_visible_reason():
    state = _new_state()
    for offset in range(3):
        S.record_patch_outcome(state, "draft", False, now=NOW + timedelta(hours=offset))
    action = S.next_rung(
        state,
        "draft",
        _incident(transient=False, failure_class="task_error", playbook_candidate=None),
        now=NOW + timedelta(hours=4),
    )
    assert action["rung"] == "L4"
    assert "demoted until" in action["reason"]
    assert "L2/L3 skipped" in action["reason"]


def test_demotion_auto_resets_after_seven_clean_days_with_injected_time():
    state = _new_state()
    for offset in range(3):
        S.record_patch_outcome(state, "draft", False, now=NOW + timedelta(hours=offset))
    reset_at = datetime.fromisoformat(state["nodes"]["draft"]["demoted_until"])
    action = S.next_rung(
        state,
        "draft",
        _incident(transient=False, failure_class="task_error", playbook_candidate=None),
        now=reset_at,
    )
    assert action["rung"] == "L2"
    assert state["nodes"]["draft"]["demoted_until"] is None
    assert state["nodes"]["draft"]["failed_patch_attempts"] == 0


def test_weekly_budget_at_ten_jumps_to_l4_then_l5():
    state = _new_state()
    S.record_patch_outcome(state, "draft", True, now=NOW)
    state["nodes"]["draft"]["weekly_budget"]["auto_patches_applied"] = 10
    first = S.next_rung(state, "draft", _incident(), now=NOW)
    second = S.next_rung(state, "draft", _incident(), now=NOW)
    assert first["rung"] == "L4" and "10/10" in first["reason"]
    assert second["rung"] == "L5"


def test_weekly_budget_resets_on_new_utc_week():
    state = _new_state()
    S.record_patch_outcome(state, "draft", True, now=NOW)
    state["nodes"]["draft"]["weekly_budget"]["auto_patches_applied"] = 10
    later = NOW + timedelta(days=8)
    action = S.next_rung(state, "draft", _incident(), now=later)
    assert action["rung"] == "L0"
    budget = state["nodes"]["draft"]["weekly_budget"]
    assert budget["auto_patches_applied"] == 0
    assert datetime.fromisoformat(budget["week_start"]).weekday() == 0


def test_proposal_card_is_propose_only_and_inside_heal_proposals(tmp_path):
    path = S.propose_patch(tmp_path, _incident(), "L2", now=NOW)
    card = json.loads(path.read_text(encoding="utf-8"))
    assert path.parent == tmp_path / "heal_proposals"
    assert card["auto_apply"] is False
    assert card["requires"] == "full QA + re-shadow + re-pin"
    assert list(tmp_path.iterdir()) == [tmp_path / "heal_proposals"]


def test_proposal_cards_are_versioned_without_overwrite(tmp_path):
    first = S.propose_patch(tmp_path, _incident(), "L2", now=NOW)
    second = S.propose_patch(tmp_path, _incident(), "L3", now=NOW)
    assert first.name.endswith("-1.json")
    assert second.name.endswith("-2.json")
    assert first.exists() and second.exists()


def test_dossier_lists_every_attempt_and_one_recommended_action(tmp_path):
    state = S.load_state(tmp_path)
    S.next_rung(state, "draft", _incident(), now=NOW)
    S.save_state(tmp_path, state)
    attempts = [
        {"rung": "L0", "reason": "retry", "outcome": "failed"},
        {"rung": "L1", "reason": "known fix", "outcome": "failed"},
        {"rung": "L4", "reason": "contained", "outcome": "degraded"},
    ]
    dossier = S.build_dossier(tmp_path, "draft", [_incident()], attempts, now=NOW)
    assert dossier["attempts"] == attempts
    assert isinstance(dossier["recommended_action"], str)
    assert len(dossier["recommended_action"].splitlines()) == 1
    assert [key for key in dossier if key == "recommended_action"] == ["recommended_action"]


def test_write_dossier_is_append_only_jsonl_and_returns_path(tmp_path):
    dossier = S.build_dossier(tmp_path, "draft", [], [], now=NOW)
    path = S.write_dossier(tmp_path, dossier)
    S.write_dossier(tmp_path, dossier)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [dossier, dossier]


def test_state_roundtrips_through_atomic_json(tmp_path):
    state = S.load_state(tmp_path)
    S.next_rung(state, "draft", _incident(), now=NOW)
    path = S.save_state(tmp_path, state)
    assert path == tmp_path / "selfheal_state.json"
    assert S.load_state(tmp_path) == state
    assert not list(tmp_path.glob(".selfheal_state.json.*.tmp"))


def test_corrupt_state_file_raises_value_error_naming_path(tmp_path):
    path = tmp_path / "selfheal_state.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match=re.escape(str(path))):
        S.load_state(tmp_path)


def test_fixed_now_produces_deterministic_routing_and_dossier(tmp_path):
    first_state = _new_state()
    second_state = _new_state()
    first = S.next_rung(first_state, "draft", _incident(), now=NOW)
    second = S.next_rung(second_state, "draft", _incident(), now=NOW)
    assert first == second
    assert first_state == second_state
    assert S.build_dossier(tmp_path, "draft", [], [], now=NOW) == S.build_dossier(
        tmp_path, "draft", [], [], now=NOW
    )


def test_bad_fields_raise_value_errors_that_name_the_field(tmp_path):
    with pytest.raises(ValueError, match="node"):
        S.next_rung(_new_state(), "../escape", _incident(), now=NOW)
    with pytest.raises(ValueError, match="diagnosis"):
        S.propose_patch(tmp_path, _incident(diagnosis=""), "L2", now=NOW)
    with pytest.raises(ValueError, match="now"):
        S.record_patch_outcome(_new_state(), "draft", False, now=NOW.replace(tzinfo=None))
