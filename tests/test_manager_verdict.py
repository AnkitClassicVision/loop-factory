"""Tests for signed semantic run-manifest verdict sensing by the manager."""
from __future__ import annotations

import json

import pytest

from factory.manager import compare, run_manager_cycle, sense_manifest_verdict
from kernel import run_manifest


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "manager-test-key")


def _write_manifest(state_dir, run_id="run-1"):
    manifest_dir = state_dir / "run-manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "run-manifest",
        "rev": 2,
        "run_id": run_id,
        "created_at": "2026-01-01T00:00:00Z",
        "signature": None,
    }
    manifest["signature"] = run_manifest._local_signer().sign(
        run_manifest._canonical_without_signature(manifest)
    )
    (manifest_dir / f"{run_id}.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return manifest_dir


def _write_verdict(state_dir, *, run_id="run-1", status="green", signed=True, **diffs):
    manifest_dir = _write_manifest(state_dir, run_id)
    verdict = run_manifest._base_verdict(run_id, status, "test")
    for field in (
        "missing", "unexpected", "duplicates", "reordered",
        "semantic_failures", "malformed_records", "blocked_contract_failures",
    ):
        verdict[field] = diffs.get(field, [])
    if signed:
        run_manifest._sign_verdict(verdict)
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


def test_signed_red_verdict_becomes_breach_finding(tmp_path):
    _write_verdict(
        tmp_path,
        status="red",
        missing=["send"],
        unexpected=["extra"],
        duplicates=["sense"],
        reordered=["compare"],
        semantic_failures=["driver:error"],
        malformed_records=["line:7:receipts"],
        blocked_contract_failures=["approval"],
    )

    sensed = sense_manifest_verdict(tmp_path)
    assert sensed["manifest_verdict_status"] == "red"
    assert sensed["manifest_verdict_counts"] == {
        "missing": 1,
        "unexpected": 1,
        "duplicates": 1,
        "reordered": 1,
        "semantic_failures": 1,
        "malformed_records": 1,
        "blocked_contract_failures": 1,
    }
    finding = next(f for f in compare(sensed) if f["code"] == "runmanifest_red")
    assert finding["severity"] == "breach"
    assert finding["observed"] == sensed["manifest_verdict_counts"]


def test_unsigned_green_verdict_is_untrusted_and_becomes_breach(tmp_path):
    _write_verdict(tmp_path, status="green", signed=False)

    sensed = sense_manifest_verdict(tmp_path)
    assert sensed["manifest_verdict_status"] == "unknown"
    finding = next(f for f in compare(sensed) if f["code"] == "runmanifest_unverified")
    assert finding["severity"] == "breach"


def test_manifest_without_verdict_is_absent_breach(tmp_path):
    _write_manifest(tmp_path)

    sensed = sense_manifest_verdict(tmp_path)
    assert sensed["manifest_verdict_status"] == "absent"
    finding = next(
        f for f in compare(sensed) if f["code"] == "runmanifest_unverified"
    )
    assert finding["severity"] == "breach"


def test_malformed_verdict_is_unknown_breach(tmp_path):
    manifest_dir = _write_manifest(tmp_path)
    (manifest_dir / "run-1.verdict.json").write_text("not json", encoding="utf-8")

    sensed = sense_manifest_verdict(tmp_path)
    assert sensed["manifest_verdict_status"] == "unknown"
    finding = next(
        f for f in compare(sensed) if f["code"] == "runmanifest_unverified"
    )
    assert finding["severity"] == "breach"


def test_run_manager_cycle_merges_signed_semantic_verdict(tmp_path):
    _write_verdict(tmp_path, status="red", semantic_failures=["driver:error"])

    report = run_manager_cycle(tmp_path)

    assert report["sensed"]["manifest_verdict_status"] == "red"
    finding = next(f for f in report["findings"] if f["code"] == "runmanifest_red")
    assert finding["severity"] == "breach"
