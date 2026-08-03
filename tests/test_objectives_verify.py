from __future__ import annotations

import json
from pathlib import Path

from factory.objectives_verify import main


PODCAST_OBJECTIVES = """department: podcast
owner: owner
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [autonomy_state]
setpoints:
  objectives:
    publish_reliability:
      label: "Episodes online, on time"
      setpoint: 100
      minimum: 100
      target: 100
      unit: "%"
    hopper_depth:
      label: "Recordings in the hopper"
      setpoint: 6
      minimum: 2
      target: 6
      unit: " recordings"
    state_drift:
      label: "Ledger vs evidence drift"
      setpoint: 0
      minimum: 0
      target: 0
      maximum: 0
      unit: " mismatches"
"""


def run(observed: Path, charter: Path, *flags: str) -> int:
    return main([
        "--name", "podcast",
        "--charter", str(charter),
        "--objectives-file", str(observed),
        *flags,
    ])


def charter(tmp_path: Path, text: str = PODCAST_OBJECTIVES) -> Path:
    path = tmp_path / "charter.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def observed(tmp_path: Path, values: dict, ts: str = "2099-08-03T12:00:00+00:00") -> Path:
    path = tmp_path / "observed.json"
    path.write_text(json.dumps({"schema": "objectives-observed/v1", "ts": ts, "values": values}))
    return path


def test_fully_observed_green(tmp_path, capsys):
    path = observed(tmp_path, {"publish_reliability": 100, "hopper_depth": 6, "state_drift": 0})
    assert run(path, charter(tmp_path)) == 0
    assert "OBJECTIVES_VERIFY_OK podcast" in capsys.readouterr().out


def test_absent_value_is_honest_unknown_and_fails_by_default(tmp_path, capsys):
    path = observed(tmp_path, {"publish_reliability": 100, "state_drift": 0})
    assert run(path, charter(tmp_path)) == 1
    output = capsys.readouterr().out
    assert "UNKNOWN hopper_depth" in output
    assert "WHY hopper_depth observed: absent (honest unknown)" in output


def test_absent_value_passes_when_unknown_allowed(tmp_path, capsys):
    path = observed(tmp_path, {"publish_reliability": 100, "state_drift": 0})
    assert run(path, charter(tmp_path), "--allow-unknown") == 0
    assert "UNKNOWN hopper_depth" in capsys.readouterr().out


def test_malformed_observed_file_has_clean_why(tmp_path, capsys):
    path = tmp_path / "observed.json"
    path.write_text("{not-json}\n", encoding="utf-8")
    assert run(path, charter(tmp_path)) == 1
    assert "WHY podcast objectives_observed: malformed" in capsys.readouterr().out


def test_stale_timestamp_fails_and_allow_stale_passes(tmp_path, capsys):
    path = observed(tmp_path, {"publish_reliability": 100, "hopper_depth": 6, "state_drift": 0}, "2020-01-01T00:00:00+00:00")
    charter_path = charter(tmp_path)
    assert run(path, charter_path) == 1
    assert "WHY podcast ts: stale (older than 48h)" in capsys.readouterr().out
    assert run(path, charter_path, "--allow-stale") == 0


def test_at_minimum_boundary_passes(tmp_path):
    path = observed(tmp_path, {"publish_reliability": 100, "hopper_depth": 2, "state_drift": 0})
    assert run(path, charter(tmp_path)) == 0


def test_below_minimum_prints_breach_and_fails(tmp_path, capsys):
    path = observed(tmp_path, {"publish_reliability": 100, "hopper_depth": 1, "state_drift": 0})
    assert run(path, charter(tmp_path)) == 1
    assert "OBJECTIVE_BELOW_MIN hopper_depth" in capsys.readouterr().out


def test_missing_required_charter_field_names_objective_and_field(tmp_path, capsys):
    charter_path = charter(tmp_path, PODCAST_OBJECTIVES.replace('      unit: " recordings"\n', ""))
    path = observed(tmp_path, {"publish_reliability": 100, "hopper_depth": 6, "state_drift": 0})
    assert run(path, charter_path) == 1
    assert "WHY hopper_depth unit: required field missing" in capsys.readouterr().out


def test_observed_value_is_type_checked_against_unit(tmp_path, capsys):
    path = observed(tmp_path, {"publish_reliability": 100, "hopper_depth": "six", "state_drift": 0})
    assert run(path, charter(tmp_path)) == 1
    output = capsys.readouterr().out
    assert "WHY hopper_depth observed: must be a finite number" in output
    assert " recordings" in output


def test_optional_maximum_is_honored(tmp_path, capsys):
    path = observed(tmp_path, {"publish_reliability": 100, "hopper_depth": 6, "state_drift": 1})
    assert run(path, charter(tmp_path)) == 1
    assert "WHY state_drift observed: 1 exceeds maximum 0" in capsys.readouterr().out
