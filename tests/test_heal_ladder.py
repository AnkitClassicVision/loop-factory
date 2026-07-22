"""Self-heal ladder (P3, finishing B5): the deterministic heal state machine.

Bounded at every level, escalation-only, minimum observation window, terminal
`parked` state, oscillation detector, and heal may NEVER modify an immutable
safety invariant. Spec §8.
"""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("heal_ladder", ROOT / "factory/heal_ladder.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


H = _load()


def _ladder(tmp_path):
    return H.HealLadder(state_path=tmp_path / "heal.json", min_observation_s=0)


def test_first_failure_retries_at_l1(tmp_path):
    lad = _ladder(tmp_path)
    action = lad.record_failure("draft", kind="task_error", now=0.0)
    assert action["act"] == "retry" and action["level"] == "L1"


def test_l1_exhaustion_escalates_to_l2_ringer(tmp_path):
    lad = _ladder(tmp_path)
    lad.record_failure("draft", "task_error", now=0.0)   # L1 attempt 1
    lad.record_failure("draft", "task_error", now=1.0)   # L1 attempt 2 (max)
    action = lad.record_failure("draft", "task_error", now=2.0)  # -> L2
    assert action["act"] == "ringer_heal" and action["level"] == "L2"


def test_repeated_l2_escalates_to_l4_change_card(tmp_path):
    lad = _ladder(tmp_path)
    for t in range(2):
        lad.record_failure("draft", "task_error", now=float(t))  # exhaust L1
    lad.record_failure("draft", "task_error", now=2.0)  # L2 attempt 1
    lad.record_failure("draft", "task_error", now=3.0)  # L2 attempt 2 (max)
    action = lad.record_failure("draft", "task_error", now=4.0)  # -> L4
    assert action["act"] == "change_card" and action["level"] == "L4"


def test_repeated_l4_parks_terminal(tmp_path):
    lad = _ladder(tmp_path)
    for t in range(6):
        lad.record_failure("draft", "task_error", now=float(t))  # climb L1->L2->L4
    action = lad.record_failure("draft", "task_error", now=10.0)
    # eventually the node parks (terminal); further failures stay parked
    action2 = lad.record_failure("draft", "task_error", now=11.0)
    assert action2["act"] == "park" and action2["terminal"] is True


def test_success_resets_the_node(tmp_path):
    lad = _ladder(tmp_path)
    lad.record_failure("draft", "task_error", now=0.0)
    lad.record_success("draft", now=1.0)
    action = lad.record_failure("draft", "task_error", now=2.0)
    assert action["act"] == "retry" and action["level"] == "L1"  # back to the bottom


def test_oscillation_detector_parks_a_flapping_node(tmp_path):
    lad = _ladder(tmp_path)
    # flap: fail up to L2 then succeed, repeatedly — never stabilizes
    for cycle in range(4):
        base = cycle * 10
        lad.record_failure("draft", "task_error", now=base + 0.0)
        lad.record_failure("draft", "task_error", now=base + 1.0)
        lad.record_failure("draft", "task_error", now=base + 2.0)  # reaches L2
        lad.record_success("draft", now=base + 3.0)               # resets (a flap)
    action = lad.record_failure("draft", "task_error", now=100.0)
    assert action["act"] == "park" and action.get("reason") == "oscillation"


def test_min_observation_window_holds_before_escalating(tmp_path):
    lad = H.HealLadder(state_path=tmp_path / "heal.json", min_observation_s=100)
    lad.record_failure("draft", "task_error", now=0.0)  # L1 #1
    lad.record_failure("draft", "task_error", now=1.0)  # L1 #2 (max)
    # too soon to escalate to L2 (window is 100s); the ladder holds
    action = lad.record_failure("draft", "task_error", now=2.0)
    assert action["act"] == "wait"


def test_heal_may_not_modify_immutable_invariant(tmp_path):
    with pytest.raises(H.ImmutableHealError):
        H.assert_heal_target_allowed("send_authorization")
    # a benign target is fine
    H.assert_heal_target_allowed("draft_prompt_template")
