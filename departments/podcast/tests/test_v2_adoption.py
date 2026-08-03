"""Fixture-only proofs for podcast v2 sidecars and ask-return wiring."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from departments.podcast.runtime import comms_reconcile_sensor, record
from factory import runrecord


ROOT = Path(__file__).resolve().parents[3]
DAILY = ROOT / "departments/podcast/runtime/podcast_daily.sh"


def _one_valid(state_dir: Path, node: str) -> dict:
    rows = [
        json.loads(line)
        for line in (state_dir / "runs-v2.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert runrecord.validate_record(row) == row
    assert row["department"] == "podcast"
    assert row["node"] == node
    assert row["external_actions_taken"] == 0
    assert row["cost"] == {"lane": "flat_subscription", "model_calls": 0}
    assert row["artifacts"]
    return row


def test_record_stage_keeps_legacy_receipt_and_emits_valid_v2(tmp_path):
    receipt = record.write_record(
        tmp_path,
        "fixture_node",
        {"result": "ok"},
        emit_v2=True,
    )

    assert json.loads((tmp_path / "runs.jsonl").read_text(encoding="utf-8")) == receipt
    _one_valid(tmp_path, "record")


def test_comms_reconcile_stage_emits_valid_v2(tmp_path):
    tracker = tmp_path / "tracker.json"
    ledger = tmp_path / "ledger.json"
    tracker.write_text(json.dumps({
        "outbound_touch_count": 1,
        "inbound_reply_count": 1,
    }), encoding="utf-8")
    ledger.write_text(json.dumps({"referrals": [{}]}), encoding="utf-8")

    result = comms_reconcile_sensor.run_stage(tracker, ledger, tmp_path, 48)

    assert result == {"findings": []}
    assert _one_valid(tmp_path, "comms_reconcile_sensor")["status"] == "ok"


def test_rotate_stage_emits_valid_v2(tmp_path):
    observations = tmp_path / "observations.jsonl"
    observations.write_text(
        json.dumps({"sensor": "fixture", "subject": "one", "status": "ok"}) + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "departments/podcast/runtime/rotate_observations.py"),
            "--state-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["kept"] == 1
    _one_valid(tmp_path, "rotate_observations")


def test_forced_v2_append_failure_fails_comms_stage(tmp_path, monkeypatch):
    tracker = tmp_path / "tracker.json"
    ledger = tmp_path / "ledger.json"
    tracker.write_text("{}", encoding="utf-8")
    ledger.write_text('{"referrals": []}', encoding="utf-8")

    def fail_append(_state_dir, _record):
        raise OSError("fixture append failure")

    monkeypatch.setattr(runrecord, "append_record", fail_append)
    with pytest.raises(OSError, match="fixture append failure"):
        comms_reconcile_sensor.run_stage(tracker, ledger, tmp_path, 48)


def test_daily_chain_invokes_comms_and_gates_its_receipt(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "calls.log"
    stub = bin_dir / "python3"
    stub.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$PODCAST_FIXTURE_CALL_LOG\"\n"
        "case \"$*\" in\n"
        "  *runtime/comms_reconcile_sensor.py*) exit 0 ;;\n"
        "  -c*json.load*) exec /usr/bin/python3 \"$@\" ;;\n"
        "  *) printf '{}\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = dict(os.environ)
    env.update({
        "PATH": f"{bin_dir}:{env['PATH']}",
        "PODCAST_REPO_ROOT": str(ROOT),
        "PODCAST_STATE_DIR": str(tmp_path / "state"),
        "PODCAST_FIXTURE_CALL_LOG": str(call_log),
    })

    completed = subprocess.run(
        ["bash", str(DAILY)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    calls = call_log.read_text(encoding="utf-8")
    assert completed.returncode != 0
    assert "runtime/comms_reconcile_sensor.py" in calls
    assert "runtime/compare_charter.py" not in calls


def test_escalate_graph_node_declares_return_contract():
    graph = json.loads((ROOT / "departments/podcast/subgraphs.json").read_text(encoding="utf-8"))
    watchdog = next(item for item in graph["subgraphs"] if item["id"] == "SG-WATCHDOG")
    escalate = next(item for item in watchdog["nodes"] if item["id"] == "N4")

    assert escalate["emits_ask"] is True
    assert escalate["return_path"] == "runtime/comms_reconcile_sensor.py"
    assert escalate["return_sla_hours"] == 48
