"""Charter loader: fail-closed validation, charter-wins threshold merge, and
the human-gate floor that a charter cannot lower."""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


CL = _load("charter_loader", "factory/charter_loader.py")
SC = _load("scaffold", "factory/scaffold.py")


VALID = """
department: t
owner: someone
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [delivery_floor, autonomy_state]
thresholds:
  weekly_touch_ceiling: 42
budget:
  weekly_ceilings:
    dollars: 7
escalation:
  human_gates: [external_send]
"""


def _write(tmp_path, text):
    p = tmp_path / "charter.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_valid_charter_loads(tmp_path):
    c = CL.load_charter(_write(tmp_path, VALID))
    assert c["department"] == "t"
    assert CL.autonomy_state(c) == "shadow"


def test_missing_charter_fails_closed(tmp_path):
    with pytest.raises(CL.CharterError):
        CL.load_charter(tmp_path / "nope.yaml")


def test_missing_required_key_fails_closed(tmp_path):
    with pytest.raises(CL.CharterError):
        CL.load_charter(_write(tmp_path, "department: t\nowner: x\nautonomy_state: shadow\n"))


def test_invalid_autonomy_state_fails_closed(tmp_path):
    bad = VALID.replace("autonomy_state: shadow", "autonomy_state: yolo")
    with pytest.raises(CL.CharterError):
        CL.load_charter(_write(tmp_path, bad))


def test_unparseable_yaml_fails_closed(tmp_path):
    with pytest.raises(CL.CharterError):
        CL.load_charter(_write(tmp_path, "a: [unclosed\nowner"))


def test_charter_thresholds_win_defaults_fill_gaps(tmp_path):
    c = CL.load_charter(_write(tmp_path, VALID))
    t = CL.thresholds(c)
    assert t["weekly_touch_ceiling"] == 42                       # charter wins
    assert t["pace_ceiling_near_frac"] == 0.9                    # default fills
    assert t["budget_ceilings"]["dollars"] == 7                  # charter wins
    assert t["budget_ceilings"]["model_calls"] == 900            # default fills


def test_human_gate_floor_cannot_be_lowered(tmp_path):
    # the charter listed only external_send; the factory floor still applies
    c = CL.load_charter(_write(tmp_path, VALID))
    gates = CL.human_gates(c)
    for required in ("external_send", "crm_write", "publish", "charter_change", "promotion"):
        assert required in gates


def test_scaffolded_template_charter_is_loadable(tmp_path):
    # F0 output must already satisfy the loader (valid YAML, required keys)
    SC.scaffold_department("demo", root=tmp_path)
    c = CL.load_charter(tmp_path / "departments" / "demo" / "charter.yaml")
    assert CL.autonomy_state(c) == "shadow"
    assert "autonomy_state" in CL.immutable_invariants(c)
