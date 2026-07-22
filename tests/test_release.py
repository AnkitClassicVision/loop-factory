"""Release-pinning + calibration (P4, finishing B4).

Content-addressed release directories (spec §13.6): a release pins the exact
artifact hashes; an atomic `current` pointer flips between releases; a periodic
verifier recomputes hashes and alarms on drift (§13.10). Plus the calibration
log that records the hand-authoring cost (human-minutes, hand-patch count).
"""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("release", ROOT / "factory/release.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


REL = _load()


def _dept(tmp_path):
    d = tmp_path / "sales"
    (d / "runtime").mkdir(parents=True)
    (d / "charter.yaml").write_text("setpoints: {op: 300}\n", encoding="utf-8")
    (d / "runtime" / "nodes.py").write_text("def node(): return 1\n", encoding="utf-8")
    return d


def test_pin_creates_content_addressed_release(tmp_path):
    dept = _dept(tmp_path)
    release_root = tmp_path / "releases"
    h = REL.pin_release(dept, release_root, source_ref="gitsha123")
    assert (release_root / h / "manifest.json").exists()
    manifest = REL.load_manifest(release_root / h)
    assert manifest["hash"] == h and manifest["source_ref"] == "gitsha123"
    assert any(a["path"].endswith("charter.yaml") for a in manifest["artifacts"])


def test_identical_artifacts_same_hash(tmp_path):
    dept = _dept(tmp_path)
    h1 = REL.pin_release(dept, tmp_path / "r1", source_ref="x")
    h2 = REL.pin_release(dept, tmp_path / "r2", source_ref="x")
    assert h1 == h2  # content-addressed: same bytes -> same hash


def test_mutated_artifact_changes_hash_and_fails_verify(tmp_path):
    dept = _dept(tmp_path)
    release_root = tmp_path / "releases"
    h = REL.pin_release(dept, release_root, source_ref="x")
    assert REL.verify_release(dept, release_root / h)["ok"] is True
    # mutate a live artifact after pinning -> verify must alarm
    (dept / "charter.yaml").write_text("setpoints: {op: 999}\n", encoding="utf-8")
    result = REL.verify_release(dept, release_root / h)
    assert result["ok"] is False and any("charter.yaml" in m for m in result["mismatches"])


def test_current_pointer_flips_atomically(tmp_path):
    dept = _dept(tmp_path)
    release_root = tmp_path / "releases"
    h1 = REL.pin_release(dept, release_root, source_ref="v1")
    REL.flip_current(release_root, h1)
    assert REL.read_current(release_root) == h1
    (dept / "runtime" / "nodes.py").write_text("def node(): return 2\n", encoding="utf-8")
    h2 = REL.pin_release(dept, release_root, source_ref="v2")
    REL.flip_current(release_root, h2)
    assert REL.read_current(release_root) == h2 and h2 != h1


def test_calibration_log_records_hand_authoring_cost(tmp_path):
    log = tmp_path / "calibration.jsonl"
    REL.record_calibration(
        log, dept="sales", human_minutes=340, hand_patches=6,
        judgment_minutes=120, mechanical_minutes=220, source_ref="gitsha123",
    )
    rows = [__import__("json").loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert rows[-1]["dept"] == "sales" and rows[-1]["human_minutes"] == 340
    assert rows[-1]["hand_patches"] == 6
