from pathlib import Path

import pytest

from factory import evalregistry


ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = {"block_critical": 1, "block_major": 3, "revise_major": 1}
VALID = """
draft_qa:
  tier1: [schema, provenance]
  tier2:
    required: true
    cross_model: true
    golden_set: golden/draft.yaml
    verdict_thresholds:
      block_critical: 1
      block_major: 3
      revise_major: 1
script_sensor:
  tier1: [source_readable]
"""


def _write(tmp_path, text=VALID):
    path = tmp_path / "eval_registry.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _defect(severity):
    return {"code": "fixture", "severity": severity, "detail": "fixture defect"}


def test_valid_registry_loads(tmp_path):
    registry = evalregistry.load_registry(_write(tmp_path))
    assert registry["draft_qa"]["tier1"] == ["schema", "provenance"]
    assert registry["draft_qa"]["tier2"]["cross_model"] is True


def test_bad_severity_threshold_shape_names_field(tmp_path):
    bad = VALID.replace(
        "verdict_thresholds:\n      block_critical: 1\n      block_major: 3\n      revise_major: 1",
        "verdict_thresholds: nope",
    )
    with pytest.raises(ValueError, match=r"draft_qa\.tier2\.verdict_thresholds"):
        evalregistry.load_registry(_write(tmp_path, bad))


def test_resolve_defaults_for_tier1_only_class(tmp_path):
    config = evalregistry.resolve(evalregistry.load_registry(_write(tmp_path)), "script_sensor")
    assert config == {"tier1": ["source_readable"]}
    assert evalregistry.gating(config, golden_set_passed=True) == "none"


def test_resolve_unknown_class_defaults_to_empty_tier1(tmp_path):
    config = evalregistry.resolve(evalregistry.load_registry(_write(tmp_path)), "unknown")
    assert config == {"tier1": []}


def test_one_critical_defect_blocks():
    assert evalregistry.classify_defects([_defect("critical")], THRESHOLDS) == "block"


def test_majors_at_block_threshold_block():
    assert evalregistry.classify_defects([_defect("major")] * 3, THRESHOLDS) == "block"


def test_majors_within_revise_band_revise():
    assert evalregistry.classify_defects([_defect("major")] * 2, THRESHOLDS) == "revise"


def test_all_minor_defects_allow():
    assert evalregistry.classify_defects([_defect("minor")] * 8, THRESHOLDS) == "allow"


def test_unknown_severity_is_treated_as_major():
    assert evalregistry.classify_defects([_defect("mystery")], THRESHOLDS) == "revise"


def test_gating_is_advisory_without_passing_golden_set(tmp_path):
    config = evalregistry.resolve(evalregistry.load_registry(_write(tmp_path)), "draft_qa")
    assert evalregistry.gating(config, golden_set_passed=False) == "advisory"


def test_gating_stays_advisory_when_golden_set_path_is_absent(tmp_path):
    config = evalregistry.resolve(evalregistry.load_registry(_write(tmp_path)), "draft_qa")
    config["tier2"]["golden_set"] = None
    assert evalregistry.gating(config, golden_set_passed=True) == "advisory"


def test_gating_activates_after_golden_set_passes(tmp_path):
    config = evalregistry.resolve(evalregistry.load_registry(_write(tmp_path)), "draft_qa")
    assert evalregistry.gating(config, golden_set_passed=True) == "gating"


def test_real_department_registries_load_cleanly():
    social = evalregistry.load_registry(
        ROOT / "departments/social/runtime/eval_registry.yaml"
    )
    podcast = evalregistry.load_registry(
        ROOT / "departments/podcast/runtime/eval_registry.yaml"
    )
    assert evalregistry.gating(social["draft_qa"], False) == "advisory"
    assert all("tier2" not in config for config in podcast.values())
