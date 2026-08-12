from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from factory import runrecord
from kernel import run_manifest


SOURCE_REF = "test-source-ref"
SIGNING_KEY = "test-key"


@pytest.fixture
def dept_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("LOOP_FACTORY_SOURCE_REF", SOURCE_REF)
    dept = tmp_path / "departments" / "fake"
    state = dept / "state"
    runtime = dept / "runtime"
    roster_path = runtime / "run-roster.json"
    release_dir = dept / "releases" / "abc123"
    runtime.mkdir(parents=True)
    release_dir.mkdir(parents=True)
    state.mkdir()
    entrypoint = runtime / "fake_daily.sh"
    driver = runtime / "driver.py"
    entrypoint.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    driver.write_text("print('driver')\n", encoding="utf-8")
    source_units = dept / "systemd"
    source_units.mkdir()
    (source_units / "fake-loop.service").write_text(
        f"[Service]\nExecStart=/usr/bin/python3 -m factory.run_driver --department fake --root {tmp_path}\n",
        encoding="utf-8",
    )
    (source_units / "fake-loop.timer").write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
    units = tmp_path / "systemd-user"
    units.mkdir()
    (units / "fake-loop.service").write_text(
        f"[Service]\nExecStart=/usr/bin/python3 -m factory.run_driver --department fake --root {tmp_path}\n", encoding="utf-8"
    )
    (units / "fake-loop.timer").write_text(
        "[Timer]\nOnCalendar=daily\n", encoding="utf-8"
    )
    monkeypatch.setenv("LOOP_FACTORY_SYSTEMD_DIR", str(units))
    roster = {
        "schema": "run-roster",
        "rev": 2,
        "department": "fake",
        "entrypoint": {
            "timer": "fake-loop.timer",
            "service": "fake-loop.service",
            "timer_source": "systemd/fake-loop.timer",
            "service_source": "systemd/fake-loop.service",
            "path": "runtime/fake_daily.sh",
            "driver": {"node": "last", "path": "runtime/driver.py"},
        },
        "nodes": [
            {
                "ordinal": 1,
                "node": "first",
                "required": True,
                "allowed_terminal_statuses": ["ok"],
            },
            {
                "ordinal": 2,
                "node": "optional",
                "required": False,
                "allowed_terminal_statuses": ["ok"],
            },
            {
                "ordinal": 3,
                "node": "last",
                "required": True,
                "allowed_terminal_statuses": ["ok"],
            },
        ],
    }
    roster_path.write_text(json.dumps(roster), encoding="utf-8")
    authority_path = dept / "authority-map.json"
    authority_path.write_text(json.dumps({
        "schema": "authority-map/v1",
        "department": "fake",
        "actions": [
            {"action": "observe", "owner": "factory-owner", "actor": "factory_supervisor", "authority": "observe", "proof": "signed_observation", "external_effect": False, "approval_required": False},
            {"action": "plan", "owner": "worker-owner", "actor": "direct_worker", "authority": "draft", "proof": "release_bound_proposal", "external_effect": False, "approval_required": False},
            {"action": "approve", "owner": "human-owner", "actor": "human_gate", "authority": "approve", "proof": "signed_human_decision", "external_effect": False, "approval_required": False},
            {"action": "execute", "owner": "executor-owner", "actor": "dedicated_executor", "authority": "execute", "proof": "target_readback", "external_effect": True, "approval_required": True},
            {"action": "verify", "owner": "verifier-owner", "actor": "independent_verifier", "authority": "verify", "proof": "target_readback", "external_effect": False, "approval_required": False},
        ],
    }), encoding="utf-8")
    _write_release(dept, roster_path, entrypoint, driver, authority_path)
    return dept, state


def _write_release(dept, roster_path, entrypoint, driver, authority_path):
    artifacts = []
    for path in (
        roster_path, entrypoint, driver, authority_path,
        dept / "systemd" / "fake-loop.service", dept / "systemd" / "fake-loop.timer",
    ):
        artifacts.append({
            "path": str(path.relative_to(dept)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    (dept / "releases" / "current").write_text("abc123\n", encoding="utf-8")
    (dept / "releases" / "abc123" / "manifest.json").write_text(
        json.dumps({"hash": "abc123", "source_ref": SOURCE_REF, "artifacts": artifacts}),
        encoding="utf-8",
    )


def _mint(dept, state):
    return run_manifest.mint(
        department="fake", dept_dir=dept, state_dir=state, trigger="daily"
    )


def _record(run_id, node, status="ok", ts="2026-01-01T00:00:01Z", *,
            department="fake", release=None, receipts=None, block=None,
            external_actions_taken=0):
    fields = {
        "schema": runrecord.SCHEMA,
        "rev": 2,
        "run_id": run_id,
        "department": department,
        "node": node,
        "epoch": 0,
        "ts": ts,
        "attempt": 1,
        "round": None,
        "release": release if release is not None else {"hash": "abc123", "source_ref": SOURCE_REF},
        "trigger": {"kind": "time", "id": "fake-loop.timer", "dedupe_key": run_id},
        "engine": None,
        "model": None,
        "auth_class": None,
        "usage": None,
        "cost": {"lane": "flat_subscription", "model_calls": 0},
        "duration_ms": 1,
        "status": status,
        "errors": ["expected"] if status == "error" else [],
        "artifacts": [],
        "receipts": receipts if receipts is not None else [{
            "schema": "file-sha256/v1", "path": f"proof-{node}", "sha256": "0" * 64,
        }],
        "evaluator": None,
        "approval": None,
        "external_actions_taken": external_actions_taken,
    }
    if block is not None:
        fields["block"] = block
    return runrecord.build_record(**fields)


def _rows(state, rows):
    for row in rows:
        if not isinstance(row, dict):
            continue
        for receipt in row.get("receipts", []):
            if not isinstance(receipt, dict) or receipt.get("schema") != "file-sha256/v1":
                continue
            path = receipt.get("path")
            if not isinstance(path, str) or not path or Path(path).is_absolute():
                continue
            proof = state / path
            proof.parent.mkdir(parents=True, exist_ok=True)
            proof.write_text(f"proof for {row.get('node')}\n", encoding="utf-8")
            receipt["sha256"] = hashlib.sha256(proof.read_bytes()).hexdigest()
    (state / "runs-v2.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _ok_rows(run_id):
    return [
        _record(run_id, "first", ts="2026-01-01T00:00:01Z"),
        _record(run_id, "last", ts="2026-01-01T00:00:02Z"),
    ]


def test_mint_writes_signed_release_and_driver_bound_manifest(dept_tree):
    dept, state = dept_tree
    result = _mint(dept, state)
    manifest_path = Path(result["manifest"])
    manifest = json.loads(manifest_path.read_text())
    assert result["signed"] is True
    assert manifest["schema"] == "run-manifest"
    assert manifest["rev"] == 2
    assert manifest["run_id"] == result["run_id"]
    assert manifest["release"] == {"hash": "abc123", "source_ref": SOURCE_REF}
    assert manifest["entrypoint"]["driver"]["node"] == "last"
    assert run_manifest._manifest_is_signed(manifest)
    with pytest.raises(FileExistsError):
        run_manifest._write_exclusive(manifest_path, manifest)


def test_cli_mint_requires_signed_release_bound_contract(dept_tree):
    dept, state = dept_tree
    other_state = dept / "cli-state"
    other_state.mkdir()
    proc = subprocess.run(
        [
            sys.executable, "-m", "kernel.run_manifest", "mint", "--department", "fake",
            "--dept-dir", str(dept), "--state-dir", str(other_state), "--trigger", "daily",
        ],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PYTHONPATH": os.getcwd(),
            "OE_KERNEL_SIGNING_KEY": SIGNING_KEY,
            "LOOP_FACTORY_SOURCE_REF": SOURCE_REF,
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["signed"] is True


def test_mint_refuses_roster_drift(dept_tree):
    dept, state = dept_tree
    with (dept / "runtime" / "run-roster.json").open("ab") as fh:
        fh.write(b" ")
    with pytest.raises(run_manifest.ManifestRefused, match="release_artifact_hash_mismatch"):
        _mint(dept, state)
    assert not list((state / "run-manifests").glob("*.json"))


def test_mint_refuses_missing_release_entry(dept_tree):
    dept, state = dept_tree
    release = dept / "releases" / "abc123" / "manifest.json"
    release.write_text(json.dumps({"hash": "abc123", "source_ref": SOURCE_REF, "artifacts": []}))
    with pytest.raises(run_manifest.ManifestRefused, match="release_artifact_missing"):
        _mint(dept, state)


def test_mint_refuses_duplicate_or_misdirected_installed_entrypoint(dept_tree):
    dept, state = dept_tree
    units = Path(os.environ["LOOP_FACTORY_SYSTEMD_DIR"])
    (units / "shadow-copy.service").write_text(
        f"[Service]\nExecStart=/usr/bin/python3 -m factory.run_driver --department fake --root {dept.parents[1]}\n",
        encoding="utf-8",
    )
    with pytest.raises(run_manifest.ManifestRefused, match="duplicate_entrypoint_service"):
        _mint(dept, state)
    (units / "shadow-copy.service").unlink()
    (units / "shadow.timer").write_text(
        "[Timer]\nUnit=fake-loop.service\nOnCalendar=daily\n", encoding="utf-8"
    )
    with pytest.raises(run_manifest.ManifestRefused, match="duplicate_entrypoint_timer"):
        _mint(dept, state)
    (units / "shadow.timer").unlink()
    wrapper = units / "shadow-wrapper.sh"
    wrapper.write_text(
        f"#!/usr/bin/env bash\nexec /bin/bash {dept / 'runtime' / 'fake_daily.sh'}\n",
        encoding="utf-8",
    )
    (units / "shadow-bypass.service").write_text(
        f"[Service]\nExecStart=/bin/bash {wrapper}\n",
        encoding="utf-8",
    )
    with pytest.raises(run_manifest.ManifestRefused, match="bypass_entrypoint_detected"):
        _mint(dept, state)
    (units / "shadow-bypass.service").unlink()
    (units / "fake-loop.service").write_text(
        "[Service]\nExecStart=/bin/bash /wrong/runtime/fake_daily.sh\n", encoding="utf-8"
    )
    with pytest.raises(run_manifest.ManifestRefused, match="entrypoint_service_target_mismatch"):
        _mint(dept, state)


def test_mint_refuses_unsigned_execution_contract(dept_tree, monkeypatch):
    dept, state = dept_tree
    monkeypatch.delenv("OE_KERNEL_SIGNING_KEY")
    with pytest.raises(run_manifest.ManifestRefused, match="manifest_signing_unavailable"):
        _mint(dept, state)


def test_mint_refuses_active_source_ref_mismatch(dept_tree, monkeypatch):
    dept, state = dept_tree
    monkeypatch.setenv("LOOP_FACTORY_SOURCE_REF", "wrong-ref")
    with pytest.raises(run_manifest.ManifestRefused, match="release_source_ref_mismatch"):
        _mint(dept, state)


def test_verify_green_only_when_all_required_nodes_succeed_and_driver_runs(dept_tree):
    dept, state = dept_tree
    minted = _mint(dept, state)
    _rows(state, _ok_rows(minted["run_id"]))
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=minted["run_id"])
    assert verdict["status"] == "green"
    assert verdict["reason"] == "ok"
    assert run_manifest.verify_signed_verdict(verdict)
    observation = json.loads((state / "observations.jsonl").read_text().splitlines()[-1])
    assert observation["status"] == "ok"


def test_verify_red_when_valid_rows_but_entrypoint_exits_nonzero(dept_tree):
    dept, state = dept_tree
    minted = _mint(dept, state)
    _rows(state, _ok_rows(minted["run_id"]))

    verdict = run_manifest.verify(
        dept_dir=dept,
        state_dir=state,
        run_id=minted["run_id"],
        entrypoint_exit_code=23,
    )

    assert verdict["status"] == "red"
    assert verdict["reason"] == "entrypoint_nonzero"
    assert verdict["entrypoint_exit_code"] == 23
    assert verdict["semantic_failures"] == ["entrypoint:exit_code=23"]
    assert run_manifest.verify_signed_verdict(verdict)


def test_verify_red_when_a_required_node_reports_error_even_if_present(dept_tree):
    dept, state = dept_tree
    run_id = _mint(dept, state)["run_id"]
    rows = _ok_rows(run_id)
    rows[0]["status"] = "error"
    _rows(state, rows)
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=run_id)
    assert verdict["status"] == "red"
    assert verdict["semantic_failures"] == ["first:error"]


def test_verify_allows_explicit_owned_block_with_deadline_and_next_action(dept_tree):
    dept, state = dept_tree
    roster_path = dept / "runtime" / "run-roster.json"
    roster = json.loads(roster_path.read_text())
    roster["nodes"][0]["allowed_terminal_statuses"] = ["ok", "blocked"]
    roster_path.write_text(json.dumps(roster), encoding="utf-8")
    _write_release(
        dept, roster_path, dept / "runtime" / "fake_daily.sh", dept / "runtime" / "driver.py",
        dept / "authority-map.json",
    )
    run_id = _mint(dept, state)["run_id"]
    _rows(state, [
        _record(
            run_id, "first", "blocked", "2026-01-01T00:00:01Z",
            block={
                "owner": "human-owner",
                "deadline": "2026-01-02T00:00:00Z",
                "next_action": "resolve the required input",
            },
        ),
        _record(run_id, "last", ts="2026-01-01T00:00:02Z"),
    ])
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=run_id)
    assert verdict["status"] == "green"


def test_verify_red_for_unowned_or_malformed_block(dept_tree):
    dept, state = dept_tree
    roster_path = dept / "runtime" / "run-roster.json"
    roster = json.loads(roster_path.read_text())
    roster["nodes"][0]["allowed_terminal_statuses"] = ["ok", "blocked"]
    roster_path.write_text(json.dumps(roster), encoding="utf-8")
    _write_release(
        dept, roster_path, dept / "runtime" / "fake_daily.sh", dept / "runtime" / "driver.py",
        dept / "authority-map.json",
    )
    run_id = _mint(dept, state)["run_id"]
    _rows(state, [
        {
            "run_id": run_id,
            "node": "first",
            "status": "blocked",
            "ts": "2026-01-01T00:00:01Z",
            "block": {"owner": "", "deadline": "not-a-date", "next_action": ""},
        },
        _record(run_id, "last", ts="2026-01-01T00:00:02Z"),
    ])
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=run_id)
    assert verdict["status"] == "red"
    assert verdict["malformed_records"] == ["line:1:run_record"]


def test_verify_red_unexpected_duplicate_reordered_and_malformed_receipt(dept_tree):
    dept, state = dept_tree
    run_id = _mint(dept, state)["run_id"]
    _rows(state, [
        _record(run_id, "last", ts="2026-01-01T00:00:01Z"),
        _record(run_id, "first", ts="2026-01-01T00:00:02Z"),
        _record(run_id, "first", ts="2026-01-01T00:00:03Z"),
        _record(run_id, "intruder", ts="2026-01-01T00:00:04Z"),
        {"run_id": run_id, "node": "optional", "status": "ok", "ts": "2026-01-01T00:00:05Z", "receipts": "not-a-list"},
    ])
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=run_id)
    assert verdict["status"] == "red"
    assert verdict["unexpected"] == ["intruder"]
    assert verdict["duplicates"] == ["first"]
    assert verdict["reordered"] == ["last", "first"]
    assert verdict["malformed_records"] == ["line:5:run_record"]


def test_verify_rejects_unverifiable_receipts_and_external_action_claims(dept_tree):
    dept, state = dept_tree
    run_id = _mint(dept, state)["run_id"]
    _rows(state, [
        _record(
            run_id,
            "first",
            receipts=[{"schema": "fabricated/v1", "path": "proof-first", "sha256": "0" * 64}],
        ),
        _record(run_id, "last", external_actions_taken=1),
    ])

    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=run_id)

    assert verdict["status"] == "red"
    assert sorted(verdict["semantic_failures"]) == [
        "first:proof_invalid", "last:external_action_claimed",
    ]


def test_verify_rejects_tampered_receipt_content(dept_tree):
    dept, state = dept_tree
    run_id = _mint(dept, state)["run_id"]
    _rows(state, _ok_rows(run_id))
    (state / "proof-first").write_text("tampered\n", encoding="utf-8")

    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=run_id)

    assert verdict["status"] == "red"
    assert verdict["semantic_failures"] == ["first:proof_invalid"]


def test_verify_rejects_record_release_mismatch_or_absent_proof(dept_tree):
    dept, state = dept_tree
    run_id = _mint(dept, state)["run_id"]
    _rows(state, [
        _record(run_id, "first", release={"hash": "wrong", "source_ref": SOURCE_REF}),
        _record(run_id, "last", receipts=[]),
    ])
    verdict = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=run_id)
    assert verdict["status"] == "red"
    assert sorted(verdict["semantic_failures"]) == [
        "first:release_mismatch", "last:proof_missing",
    ]


def test_signature_tamper_and_wrong_run_rows_cannot_produce_green(dept_tree):
    dept, state = dept_tree
    minted = _mint(dept, state)
    run_id = minted["run_id"]
    _rows(state, [
        {"run_id": "wrong", "node": "first", "status": "ok", "ts": "2026-01-01T00:00:01Z"},
        {"run_id": "wrong", "node": "last", "status": "ok", "ts": "2026-01-01T00:00:02Z"},
    ])
    missing = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=run_id)
    assert missing["status"] == "red"
    assert missing["missing"] == ["first", "last"]

    path = Path(minted["manifest"])
    body = json.loads(path.read_text())
    body["roster"][0]["ordinal"] = 99
    path.write_text(json.dumps(body), encoding="utf-8")
    tampered = run_manifest.verify(dept_dir=dept, state_dir=state, run_id=run_id)
    assert tampered["status"] == "red"
    assert tampered["reason"] == "manifest_signature_invalid_or_missing"


def test_forged_manifest_and_verdict_signatures_fail_verification(dept_tree):
    dept, state = dept_tree
    minted = _mint(dept, state)
    manifest = json.loads(Path(minted["manifest"]).read_text())
    manifest["signature"] = "0" * len(manifest["signature"])
    assert run_manifest._manifest_is_signed(manifest) is False

    verdict = run_manifest._sign_verdict(
        run_manifest._base_verdict(minted["run_id"], "green", "ok")
    )
    verdict["signature"] = "0" * len(verdict["signature"])
    assert run_manifest.verify_signed_verdict(verdict) is False
