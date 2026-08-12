"""Budget telemetry fail-closed: missing must never read as zero spend.

Red-team operator catch (loop-brain-reconcile): the 900-call ceiling loaded
with no usage feed and missing data was treated as an empty usage map.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from factory.manager import sense, compare, DEFAULT_THRESHOLDS  # noqa: E402


def test_missing_budget_file_is_flagged(tmp_path):
    sensed = sense(tmp_path, budget_path=tmp_path / "nope.json")
    assert sensed["budget_telemetry_missing"] is True
    assert sensed["budget_telemetry_unconfigured"] is False
    assert sensed["budget_used"] == {}


def test_no_budget_path_is_unconfigured_not_missing(tmp_path):
    sensed = sense(tmp_path)
    assert sensed["budget_telemetry_missing"] is False
    assert sensed["budget_telemetry_unconfigured"] is True


def test_present_budget_file_loads(tmp_path):
    p = tmp_path / "budget_used.json"
    p.write_text(json.dumps({"model_calls": 12}), encoding="utf-8")
    sensed = sense(tmp_path, budget_path=p)
    assert sensed["budget_used"] == {"model_calls": 12}
    assert sensed["budget_telemetry_missing"] is False
    assert sensed["budget_telemetry_unconfigured"] is False


def test_corrupt_budget_file_still_unreadable(tmp_path):
    p = tmp_path / "budget_used.json"
    p.write_text("{not json", encoding="utf-8")
    sensed = sense(tmp_path, budget_path=p)
    assert sensed["budget_unreadable"] is True


def test_missing_telemetry_with_ceilings_is_breach(tmp_path):
    sensed = sense(tmp_path, budget_path=tmp_path / "nope.json")
    codes = {f["code"]: f["severity"] for f in compare(sensed, DEFAULT_THRESHOLDS)}
    assert codes.get("budget_telemetry_missing") == "breach"


def test_unconfigured_telemetry_with_ceilings_is_warn(tmp_path):
    sensed = sense(tmp_path)
    codes = {f["code"]: f["severity"] for f in compare(sensed, DEFAULT_THRESHOLDS)}
    assert codes.get("budget_telemetry_unconfigured") == "warn"


def test_no_ceilings_no_budget_findings(tmp_path):
    t = dict(DEFAULT_THRESHOLDS)
    t["budget_ceilings"] = {}
    sensed = sense(tmp_path, budget_path=tmp_path / "nope.json")
    codes = [f["code"] for f in compare(sensed, t)]
    assert "budget_telemetry_missing" not in codes
    assert "budget_telemetry_unconfigured" not in codes


def test_run_manager_cycle_forwards_budget_path(tmp_path):
    from factory.manager import run_manager_cycle
    report = run_manager_cycle(tmp_path, budget_path=tmp_path / "nope.json")
    codes = [f["code"] for f in report["findings"]]
    assert "budget_telemetry_missing" in codes
