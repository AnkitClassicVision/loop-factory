"""Fixture-only coverage for social QA severity verdict wiring."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "departments" / "social" / "runtime" / "eval_registry.yaml"


def _load_qa():
    path = ROOT / "departments" / "social" / "runtime" / "qa_post.py"
    spec = importlib.util.spec_from_file_location("test_qa_verdict_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(defects):
    return {"pass": not defects, "defects": defects, "engine": "fixture"}


def _apply(defects, registry=REGISTRY):
    report = _report(defects)
    _load_qa()._apply_evaluator_policy(report, registry)
    return report


def _defect(severity):
    return {"code": f"fixture_{severity}", "detail": "fixture", "severity": severity}


def test_critical_defect_yields_block_and_preserves_failed_pass():
    report = _apply([_defect("critical")])
    assert report["verdict"] == "block"
    assert report["pass"] is False


def test_three_major_defects_yield_block_and_preserve_failed_pass():
    report = _apply([_defect("major") for _ in range(3)])
    assert report["verdict"] == "block"
    assert report["pass"] is False


def test_one_major_defect_yields_revise_and_preserves_failed_pass():
    report = _apply([_defect("major")])
    assert report["verdict"] == "revise"
    assert report["pass"] is False


def test_clean_draft_yields_allow_and_preserves_passing_pass():
    report = _apply([])
    assert report["verdict"] == "allow"
    assert report["pass"] is True


def test_real_social_registry_is_advisory_and_evaluator_carries_policy():
    report = _apply([])
    assert report["gating"] == "advisory"
    assert report["evaluator"] == {
        "pass": True, "verdict": "allow", "gating": "advisory",
    }


def test_registry_absence_degrades_to_binary_pass_and_notes_absence(tmp_path):
    defects = [_defect("critical")]
    report = _apply(defects, tmp_path / "missing-eval-registry.yaml")
    assert report == {
        "pass": False,
        "defects": defects,
        "engine": "fixture",
        "registry": "absent",
    }


def test_model_defects_default_to_major_but_preserve_named_severity():
    module = _load_qa()
    defects = module._model_defects(
        '{"defects":['
        '{"code":"defaulted","detail":"fixture"},'
        '{"code":"named","detail":"fixture","severity":"critical"}'
        ']}'
    )
    assert [defect["severity"] for defect in defects] == ["major", "critical"]
