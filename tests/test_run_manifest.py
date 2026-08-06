from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kernel import run_manifest


@pytest.fixture
def dept_tree(tmp_path):
    dept = tmp_path / "departments" / "fake"
    state = dept / "state"
    roster_path = dept / "runtime" / "run-roster.json"
    release_dir = dept / "releases" / "abc123"
    roster_path.parent.mkdir(parents=True)
    release_dir.mkdir(parents=True)
    state.mkdir()
    roster = {
        "schema": "run-roster",
        "rev": 1,
        "department": "fake",
        "nodes": [
            {"ordinal": 1, "node": "first", "required": True},
            {"ordinal": 2, "node": "optional", "required": False},
            {"ordinal": 3, "node": "last", "required": True},
        ],
    }
    roster_bytes = json.dumps(roster).encode()
    roster_path.write_bytes(roster_bytes)
    (dept / "releases" / "current").write_text("abc123\n")
    (release_dir / "manifest.json").write_text(json.dumps({
        "hash": "abc123",
        "artifacts": [{
            "path": "runtime/run-roster.json",
            "sha256": hashlib.sha256(roster_bytes).hexdigest(),
        }],
    }))
    return dept, state


def _mint(dept, state):
    return run_manifest.mint(
        department="fake", dept_dir=dept, state_dir=state, trigger="daily"
    )


def _rows(state, rows):
    (state / "runs-v2.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )


def test_mint_writes_manifest_and_refuses_second_mint(dept_tree):
    dept, state = dept_tree
    result = _mint(dept, state)
    manifest_path = Path(result["manifest"])
    manifest = json.loads(manifest_path.read_text())
    assert result["signed"] is False
    assert manifest["schema"] == "run-manifest"
    assert manifest["rev"] == 1
    assert manifest["run_id"] == result["run_id"]
    assert manifest["roster_hash"] == hashlib.sha256(
        (dept / "runtime" / "run-roster.json").read_bytes()
    ).hexdigest()
    with pytest.raises(FileExistsError):
        run_manifest._write_exclusive(manifest_path, manifest)
    other_state = dept / "cli-state"
    other_state.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "kernel.run_manifest", "mint", "--department", "fake",
         "--dept-dir", str(dept), "--state-dir", str(other_state), "--trigger", "daily"],
        text=True, capture_output=True, env={**os.environ, "PYTHONPATH": os.getcwd()},
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["signed"] is False


def test_mint_refuses_roster_drift(dept_tree):
    dept, state = dept_tree
    with (dept / "runtime" / "run-roster.json").open("ab") as fh:
        fh.write(b" ")
    with pytest.raises(run_manifest.ManifestRefused, match="roster_hash_mismatch"):
        _mint(dept, state)
    assert not list((state / "run-manifests").glob("*.json"))


def test_mint_refuses_missing_release_entry(dept_tree):
    dept, state = dept_tree
    release = dept / "releases" / "abc123" / "manifest.json"
    release.write_text(json.dumps({"hash": "abc123", "artifacts": []}))
    with pytest.raises(run_manifest.ManifestRefused, match="roster_release_entry_missing"):
        _mint(dept, state)


def test_verify_green_when_all_required_nodes_ran(dept_tree):
    dept, state = dept_tree
    minted = _mint(dept, state)
    rid = minted["run_id"]
    _rows(state, [
        {"run_id": rid, "node": "first", "status": "ok", "ts": "2026-01-01T00:00:01Z"},
        {"run_id": rid, "node": "last", "status": "ok", "ts": "2026-01-01T00:00:02Z"},
    ])
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=rid)
    assert verdict["status"] == "green"
    assert "unsigned" in verdict["reason"]
    # A green verdict's observation is "ok" — it must never read as blindness
    # (compare skips ok rows; "unknown" would raise a false unverified incident).
    observation = json.loads((state / "observations.jsonl").read_text().splitlines()[-1])
    assert observation["status"] == "ok"


def test_verify_red_missing_node(dept_tree):
    dept, state = dept_tree
    rid = _mint(dept, state)["run_id"]
    _rows(state, [{"run_id": rid, "node": "first", "status": "ok", "ts": "1"}])
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=rid)
    assert verdict["status"] == "red"
    assert verdict["missing"] == ["last"]
    observation = json.loads((state / "observations.jsonl").read_text().splitlines()[-1])
    assert observation["sensor"] == "runmanifest"
    assert observation["status"] == "alarm"


def test_verify_red_unexpected_and_duplicate_and_reordered(dept_tree):
    dept, state = dept_tree
    rid = _mint(dept, state)["run_id"]
    _rows(state, [
        {"run_id": rid, "node": "last", "status": "ok", "ts": "1"},
        {"run_id": rid, "node": "first", "status": "ok", "ts": "2"},
        {"run_id": rid, "node": "first", "status": "ok", "ts": "3"},
        {"run_id": rid, "node": "intruder", "status": "ok", "ts": "4"},
    ])
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=rid)
    assert verdict["status"] == "red"
    assert verdict["unexpected"] == ["intruder"]
    assert verdict["duplicates"] == ["first"]
    assert verdict["reordered"] == ["last", "first"]


def test_verify_unknown_when_manifest_missing(dept_tree):
    dept, state = dept_tree
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id="absent")
    assert verdict["status"] == "unknown"
    assert verdict["reason"] == "manifest_missing"


def test_signature_roundtrip_and_tamper(dept_tree, monkeypatch):
    dept, state = dept_tree
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "test-key")
    minted = _mint(dept, state)
    assert minted["signed"] is True
    rid = minted["run_id"]
    _rows(state, [
        {"run_id": rid, "node": "first", "status": "ok", "ts": "1"},
        {"run_id": rid, "node": "last", "status": "ok", "ts": "2"},
    ])
    assert run_manifest.verify(dept_dir=dept, state_dir=state, run_id=rid)["status"] == "green"
    path = Path(minted["manifest"])
    body = json.loads(path.read_text())
    body["roster"][0]["ordinal"] = 99
    path.write_text(json.dumps(body))
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=rid)
    assert verdict["status"] == "red"
    assert verdict["reason"] == "signature_invalid"


def test_wrong_run_id_rows_are_ignored(dept_tree):
    dept, state = dept_tree
    rid = _mint(dept, state)["run_id"]
    _rows(state, [
        {"run_id": "wrong", "node": "first", "status": "ok", "ts": "1"},
        {"run_id": "wrong", "node": "last", "status": "ok", "ts": "2"},
    ])
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=rid)
    assert verdict["missing"] == ["first", "last"]
