"""Factory scaffold (P5, toward B8): hand-stand a new department from the factory.

Proves the factory-standard components (department manager, heal ladder,
release-pinning, human-in-the-loop) are department-agnostic: a freshly
scaffolded department is immediately watchable, healable, and pinnable. The
INTENT-specific content (charter setpoints, funnel subgraphs, node logic) is the
F1 human step (Ankit intent lock) by design and is left as a template.
"""
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SC = _load("scaffold", "factory/scaffold.py")
MGR = _load("manager", "factory/manager.py")
REL = _load("release", "factory/release.py")
EM = _load("estate_manager", "factory/estate_manager.py")


def test_scaffold_creates_standard_department(tmp_path):
    out = SC.scaffold_department("admin", root=tmp_path)
    dept = tmp_path / "departments" / "admin"
    assert (dept / "charter.yaml").exists()
    assert (dept / "state").is_dir()
    assert (dept / "runtime").is_dir()
    # the returned registry entry is factory-standard and ready for estate.yaml
    entry = out["registry_entry"]
    assert entry["id"] == "admin" and entry["surface"] == "department"
    assert entry["state_dir"].endswith("departments/admin/state")
    # the charter template flags the F1 human step, not fabricated setpoints
    charter = (dept / "charter.yaml").read_text()
    assert "F1" in charter or "intent" in charter.lower()


def test_scaffolded_dept_runs_the_standard_manager_cycle(tmp_path):
    SC.scaffold_department("admin", root=tmp_path)
    state_dir = tmp_path / "departments" / "admin" / "state"
    # the SAME factory-standard department manager runs on a brand-new department
    report = MGR.run_manager_cycle(
        state_dir=state_dir,
        autonomy_state="shadow",
        escalate_fn=lambda issue, context=None: None,
        now="2026-07-21T12:00:00+00:00",
    )
    assert report["ok"] is True
    assert (state_dir / "STATE.json").exists()
    assert (state_dir / "MANAGER_BRIEF.md").exists()


def test_scaffolded_dept_is_pinnable_and_estate_watchable(tmp_path):
    SC.scaffold_department("admin", root=tmp_path)
    dept = tmp_path / "departments" / "admin"
    # release-pinning works on the scaffolded department
    h = REL.pin_release(dept, tmp_path / "releases", source_ref="scaffold")
    assert REL.verify_release(dept, tmp_path / "releases" / h)["ok"] is True
    # the estate manager can watch it (registered-but-no-STATE-yet is drift,
    # which is the correct signal until its first manager cycle runs)
    sensed = EM.sense([{"id": "admin", "state_dir": str(dept / "state")}], now="2026-07-21T12:00:00+00:00")
    findings = EM.compare(sensed, EM.DEFAULT_THRESHOLDS, prior_epochs={})
    assert any(f["code"] == "registry_drift" for f in findings)
