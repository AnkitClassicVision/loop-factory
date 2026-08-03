from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from departments.podcast.runtime import hopper_sensor


ROOT = Path(__file__).resolve().parents[3]


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _config(tmp_path: Path, **overrides: str) -> Path:
    paths = {
        "hopper_interviews_ready": str(tmp_path / "ledger.json"),
        "state_drift": str(tmp_path / "drift-*.json"),
        "unledgered_inbound": str(tmp_path / "rebuild-*.json"),
    }
    paths.update(overrides)
    return _write(tmp_path / "estate.json", {"objectives_evidence": paths})


def _ledger(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "ledger.json",
        {
            "schema": "funnel-ledger/v1",
            "people": [
                {"stage": "recorded", "kind": "interview"},
                {"stage": "recorded", "kind": "unknown"},
                {"stage": "recorded", "kind": "solo"},
                {"stage": "published", "kind": "interview"},
            ],
        },
    )


def _receipts(tmp_path: Path) -> None:
    _write(
        tmp_path / "drift-20260803.json",
        {"subcommand": "drift", "counts": {"drift": 3}},
    )
    _write(
        tmp_path / "rebuild-20260803.json",
        {"subcommand": "rebuild", "counts": {"unledgered_added": 4}},
    )


def test_each_new_objective_is_computed_from_fixture_evidence(tmp_path):
    _ledger(tmp_path)
    _receipts(tmp_path)
    values, details = hopper_sensor._additional_objectives(_config(tmp_path))
    assert values == {
        "hopper_interviews_ready": 2,
        "state_drift": 3,
        "unledgered_inbound": 4,
    }
    assert all(detail.startswith("read ") for detail in details.values())


@pytest.mark.parametrize(
    "objective",
    ["hopper_interviews_ready", "state_drift", "unledgered_inbound"],
)
def test_missing_evidence_omits_objective_instead_of_inventing_zero(
    tmp_path, objective
):
    _ledger(tmp_path)
    _receipts(tmp_path)
    missing = str(tmp_path / f"missing-{objective}-*.json")
    values, details = hopper_sensor._additional_objectives(
        _config(tmp_path, **{objective: missing})
    )
    assert objective not in values
    assert details[objective] == f"missing evidence artifact: {missing}"


def test_malformed_evidence_is_absent_with_reason_and_does_not_raise(tmp_path):
    _ledger(tmp_path)
    _receipts(tmp_path)
    (tmp_path / "drift-20260803.json").write_text("{broken", encoding="utf-8")
    values, details = hopper_sensor._additional_objectives(_config(tmp_path))
    assert "state_drift" not in values
    assert "unreadable evidence artifact" in details["state_drift"]
    assert values["hopper_interviews_ready"] == 2
    assert values["unledgered_inbound"] == 4


def test_malformed_ledger_omits_interviews_ready_but_keeps_receipt_values(tmp_path):
    (tmp_path / "ledger.json").write_text("{broken", encoding="utf-8")
    _receipts(tmp_path)
    values, details = hopper_sensor._additional_objectives(_config(tmp_path))
    assert "hopper_interviews_ready" not in values
    assert "unreadable evidence artifact" in details["hopper_interviews_ready"]
    assert values["state_drift"] == 3
    assert values["unledgered_inbound"] == 4


def test_existing_metrics_and_document_merge_are_unchanged_with_fixture_config(tmp_path):
    pipeline = tmp_path / "pipeline"
    (pipeline / "episodes").mkdir(parents=True)
    _ledger(tmp_path)
    _receipts(tmp_path)
    config = json.loads(_config(tmp_path).read_text(encoding="utf-8"))
    _write(pipeline / "estate.json", config)
    sources = tmp_path / "sources"
    _write(sources / "publish_schedule.json", {"episodes": []})
    state = tmp_path / "state"
    _write(
        state / "objectives_observed.json",
        {
            "schema": "objectives-observed/v1",
            "ts": "old",
            "values": {"foreign": 9, "hopper_depth": 88, "publish_reliability": 77},
        },
    )

    hopper_sensor.run(state, sources, pipeline, today=date(2026, 8, 3))

    values = json.loads((state / "objectives_observed.json").read_text())["values"]
    assert values == {
        "foreign": 9,
        "hopper_depth": 0,
        "hopper_interviews_ready": 2,
        "state_drift": 3,
        "unledgered_inbound": 4,
    }


def test_malformed_config_leaves_all_new_values_absent(tmp_path):
    config = tmp_path / "estate.json"
    config.write_text("[]", encoding="utf-8")
    values, details = hopper_sensor._additional_objectives(config)
    assert values == {}
    assert set(details) == {
        "hopper_interviews_ready",
        "state_drift",
        "unledgered_inbound",
    }
    assert all("unreadable objectives evidence config" in item for item in details.values())


def test_objectives_verify_fixture_breach_exits_one_with_why(tmp_path):
    observed = _write(
        tmp_path / "objectives.json",
        {
            "schema": "objectives-observed/v1",
            "ts": datetime.now(timezone.utc).isoformat(),
            "values": {
                "publish_reliability": 100,
                "hopper_depth": 6,
                "hopper_interviews_ready": 2,
                "state_drift": 1,
                "unledgered_inbound": 0,
            },
        },
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "factory.objectives_verify",
            "--name",
            "podcast",
            "--objectives-file",
            str(observed),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "WHY state_drift observed: 1 exceeds maximum 0" in result.stdout
