"""Tests for advisory run-manifest verdict sensing by the manager."""
from __future__ import annotations

import json

from factory.manager import compare, run_manager_cycle, sense_manifest_verdict


def _write_manifest(state_dir, run_id="run-1"):
    manifest_dir = state_dir / "run-manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps({"schema": "run-manifest", "rev": 1, "run_id": run_id}),
        encoding="utf-8",
    )
    return manifest_dir


def _write_verdict(state_dir, *, run_id="run-1", status="green", **diffs):
    manifest_dir = _write_manifest(state_dir, run_id)
    verdict = {
        "schema": "run-verdict",
        "rev": 1,
        "run_id": run_id,
        "status": status,
        "missing": diffs.get("missing", []),
        "unexpected": diffs.get("unexpected", []),
        "duplicates": diffs.get("duplicates", []),
        "reordered": diffs.get("reordered", []),
        "reason": "test",
        "checked_at": "2026-08-05T12:00:00+00:00",
        "signature": None,
    }
    (manifest_dir / f"{run_id}.verdict.json").write_text(
        json.dumps(verdict), encoding="utf-8"
    )


def test_no_adoption_dir_is_none_and_silent(tmp_path):
    sensed = sense_manifest_verdict(tmp_path)

    assert sensed == {
        "manifest_verdict_status": "none",
        "manifest_verdict_counts": {},
    }
    assert not [f for f in compare(sensed) if f["code"].startswith("runmanifest_")]


def test_red_verdict_becomes_warn_finding(tmp_path):
    _write_verdict(
        tmp_path,
        status="red",
        missing=["send"],
        unexpected=["extra"],
        duplicates=["sense"],
        reordered=["compare"],
    )

    sensed = sense_manifest_verdict(tmp_path)
    assert sensed["manifest_verdict_status"] == "red"
    assert sensed["manifest_verdict_counts"] == {
        "missing": 1,
        "unexpected": 1,
        "duplicates": 1,
        "reordered": 1,
    }
    finding = next(f for f in compare(sensed) if f["code"] == "runmanifest_red")
    assert finding["severity"] == "warn"
    assert finding["observed"] == sensed["manifest_verdict_counts"]


def test_manifest_without_verdict_is_absent_warn(tmp_path):
    _write_manifest(tmp_path)

    sensed = sense_manifest_verdict(tmp_path)
    assert sensed["manifest_verdict_status"] == "absent"
    finding = next(
        f for f in compare(sensed) if f["code"] == "runmanifest_unverified"
    )
    assert finding["severity"] == "warn"


def test_unknown_verdict_warns(tmp_path):
    manifest_dir = _write_manifest(tmp_path)
    (manifest_dir / "run-1.verdict.json").write_text("not json", encoding="utf-8")

    sensed = sense_manifest_verdict(tmp_path)
    assert sensed["manifest_verdict_status"] == "unknown"
    finding = next(
        f for f in compare(sensed) if f["code"] == "runmanifest_unverified"
    )
    assert finding["severity"] == "warn"


def test_run_manager_cycle_merges_verdict(tmp_path):
    _write_verdict(tmp_path, status="red", missing=["compare"])

    report = run_manager_cycle(tmp_path)

    assert report["sensed"]["manifest_verdict_status"] == "red"
    finding = next(f for f in report["findings"] if f["code"] == "runmanifest_red")
    assert finding["severity"] == "warn"
