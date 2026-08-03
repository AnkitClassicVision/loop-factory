import json
import subprocess
import sys
from pathlib import Path

from departments.podcast.runtime import comms_reconcile_sensor


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/comms_reconcile"
SCRIPT = ROOT / "departments/podcast/runtime/comms_reconcile_sensor.py"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_replies_without_harvest_yield_one_open_loop():
    result = comms_reconcile_sensor.reconcile(
        _load("tracker_57_16_0.json"), _load("referrals_empty.json")
    )
    open_loops = [row for row in result["findings"] if row["code"] == "open_loop"]
    assert len(open_loops) == 1
    assert open_loops[0]["replied"] == 16
    assert open_loops[0]["harvested"] == 0


def test_healthy_pair_has_no_open_loop():
    result = comms_reconcile_sensor.reconcile(
        _load("tracker_healthy.json"), _load("referrals_present.json")
    )
    assert not [row for row in result["findings"] if row["code"] == "open_loop"]


def test_missing_count_is_unknown_not_zero():
    tracker = {"schema": "obe.referral-touch-report.v1", "summary": {"inbound_reply_count": 0}}
    result = comms_reconcile_sensor.reconcile(tracker, _load("referrals_empty.json"))
    assert any(row["code"] == "count_missing" for row in result["findings"])
    assert not [row for row in result["findings"] if row["code"] == "open_loop"]


def test_unreadable_ledger_is_finding_and_exit_zero(tmp_path):
    missing = tmp_path / "missing-ledger.json"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--tracker", str(FIXTURES / "tracker_57_16_0.json"),
         "--ledger", str(missing)],
        check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0
    output = json.loads(completed.stdout)
    assert output["findings"][0]["code"] == "input_unreadable"
