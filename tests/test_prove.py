from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory import prove, scaffold
from factory import runrecord


ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def proof_department(tmp_path):
    scaffold.scaffold_department("throwaway", root=tmp_path, owner="test-owner")
    department = tmp_path / "departments" / "throwaway"
    yield tmp_path, department
    shutil.rmtree(department)
    assert not department.exists()


def _ctx(root: Path) -> prove.ProofContext:
    department = root / "departments" / "throwaway"
    return prove.ProofContext("throwaway", root / "departments", department)


@pytest.fixture(autouse=True)
def factory_promoted_emitter(factory_record_spool, monkeypatch):
    """Run proof drills through a signed per-state test spool."""
    original = runrecord.emit_record

    def emit(state_dir, **fields):
        state_dir = Path(state_dir)
        spool = state_dir / ".factory-spool"
        if not spool.exists():
            runrecord.write_spool_marker(
                spool,
                run_id="proof-fixture-run",
                department=fields["department"],
                release=fields.get("release"),
                trigger="daily",
                state_dir=state_dir,
            )
        monkeypatch.setenv(runrecord.RECORD_SPOOL_ENV, str(spool))
        path = original(state_dir, **fields)
        rows = [
            json.loads(line)
            for line in (spool / "runs-v2.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        runrecord._append_canonical_records(state_dir, rows)
        (spool / "runs-v2.jsonl").unlink()
        return path

    monkeypatch.setattr(runrecord, "emit_record", emit)


def test_full_run_green_when_unsupported_is_explicitly_allowed(proof_department):
    root, _ = proof_department
    result = prove.run_proof(
        "throwaway", root / "departments", allow_unsupported=True, now=FIXED_NOW
    )

    assert result["pass"] is True
    assert [row["name"] for row in result["drills"]] == list(prove.STAGE11_DRILLS)
    real = [row for row in result["drills"] if not row["evidence"].startswith("unsupported:")]
    assert {row["name"] for row in real} == {
        "duplicate-trigger dedupe",
        "record-write failure blocks advancement",
        "objective breach surfaces",
        "escalation delivery",
        "receipt rebuild",
        "zero-external-effects",
    }
    assert all(row["pass"] for row in real)


def test_report_and_bundle_exist_and_name_every_drill(proof_department):
    root, _ = proof_department
    result = prove.run_proof(
        "throwaway", root / "departments", allow_unsupported=True, now=FIXED_NOW
    )

    report = Path(result["report_path"])
    bundle = Path(result["bundle_path"])
    assert report.is_file() and bundle.is_file()
    text = report.read_text(encoding="utf-8")
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    assert payload["schema"] == "proof-bundle/v1"
    assert payload["pinned_release"] == "unpinned"
    for drill in prove.STAGE11_DRILLS:
        assert drill in text


def test_exit_nonzero_when_unsupported_are_not_allowed(proof_department):
    root, _ = proof_department
    completed = subprocess.run(
        [
            sys.executable, "-m", "factory.prove", "--name", "throwaway",
            "--departments-root", str(root / "departments"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["pass"] is False


def test_duplicate_trigger_red_when_runner_does_not_dedupe(proof_department, monkeypatch):
    root, _ = proof_department
    calls = iter([
        {"run_id": "run-one", "duplicate": False},
        {"run_id": "run-two", "duplicate": False},
    ])
    monkeypatch.setattr(prove.runner, "run_graph", lambda *args, **kwargs: next(calls))
    result = prove.drill_duplicate_trigger(_ctx(root))
    assert result["pass"] is False


def test_record_write_failure_red_when_write_incorrectly_advances(proof_department, monkeypatch):
    root, _ = proof_department
    monkeypatch.setattr(prove.runrecord, "emit_record", lambda *args, **kwargs: Path("written"))
    result = prove.drill_record_write_failure(_ctx(root))
    assert result == {
        "name": "record-write failure blocks advancement",
        "pass": False,
        "evidence": "record_error=False; advanced=True",
    }


def test_objective_breach_red_when_board_feed_omits_breach(proof_department, monkeypatch):
    root, _ = proof_department

    def empty_feed(_root, *, out, **_kwargs):
        Path(out).write_text("", encoding="utf-8")
        return {"rows": 0}

    monkeypatch.setattr(prove.boardfeed, "build_feed", empty_feed)
    result = prove.drill_objective_breach(_ctx(root))
    assert result["pass"] is False
    assert "rows=0" in result["evidence"]


def test_escalation_delivery_red_when_outbox_write_is_missing(proof_department, monkeypatch):
    root, _ = proof_department

    def fake_escalate(_department, _issue, outbox, **_kwargs):
        Path(outbox).write_text("", encoding="utf-8")
        return {"escalated": True}

    monkeypatch.setattr(prove.human_in_the_loop, "escalate", fake_escalate)
    result = prove.drill_escalation_delivery(_ctx(root))
    assert result["pass"] is False
    assert "outbox_rows=0" in result["evidence"]


def test_receipt_rebuild_red_when_a_record_is_lost(proof_department, monkeypatch):
    root, _ = proof_department
    original = prove.runrecord.emit_record
    calls = 0

    def drop_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            return Path(args[0]) / "runs-v2.jsonl"
        return original(*args, **kwargs)

    monkeypatch.setattr(prove.runrecord, "emit_record", drop_second)
    result = prove.drill_receipt_rebuild(_ctx(root))
    assert result["pass"] is False
    assert "written=1" in result["evidence"]


def test_zero_external_effects_red_when_record_claims_delivery(proof_department, monkeypatch):
    root, _ = proof_department
    original = prove.runrecord.emit_record

    def force_effect(*args, **kwargs):
        kwargs["external_actions_taken"] = 1
        return original(*args, **kwargs)

    monkeypatch.setattr(prove.runrecord, "emit_record", force_effect)
    result = prove.drill_zero_external_effects(_ctx(root))
    assert result["pass"] is False
    assert "external_actions_taken=[1]" in result["evidence"]


def test_any_supported_failure_makes_run_and_cli_fail(proof_department, monkeypatch):
    root, _ = proof_department
    failing = lambda _ctx: {"name": "forced red", "pass": False, "evidence": "broken"}
    monkeypatch.setattr(prove, "DRILLS", (failing,))
    result = prove.run_proof(
        "throwaway", root / "departments", allow_unsupported=True, now=FIXED_NOW
    )
    assert result["pass"] is False
