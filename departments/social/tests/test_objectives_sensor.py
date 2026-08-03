from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from departments.social.runtime import objectives_sensor


REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)


def _state(root: Path) -> Path:
    state = root / "departments" / "social" / "state"
    state.mkdir(parents=True)
    return state


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_platform_verified_delivery_pct_from_persisted_zernio(tmp_path):
    state = _state(tmp_path)
    _jsonl(state / "zernio_analytics.jsonl", [
        {"metric": "platform_verified", "value": 1.0},
        {"metric": "platform_verified", "value": 1.0},
        {"metric": "platform_verified", "value": 0.0},
        {"metric": "platform_verified", "value": 1.0},
    ])
    observed, _ = objectives_sensor.observe(tmp_path, now=NOW)
    assert observed["values"]["platform_verified_delivery_pct"] == 75.0


def test_posting_volume_counts_only_current_iso_week(tmp_path):
    state = _state(tmp_path)
    current = NOW.timestamp()
    previous = (NOW - timedelta(days=8)).timestamp()
    _jsonl(state / "kernel" / "frequency.jsonl", [
        {"person": "surface:a", "org": "surface:a", "now": current},
        {"person": "surface:b", "org": "surface:b", "now": current - 86400},
        {"person": "surface:c", "org": "surface:c", "now": previous},
    ])
    observed, _ = objectives_sensor.observe(tmp_path, now=NOW)
    assert observed["values"]["posting_volume_week"] == 2


def test_quarantine_backlog_counts_strictly_older_than_seven_days(tmp_path):
    state = _state(tmp_path)
    qdir = state / "quarantine"
    qdir.mkdir()
    for name, stamp in (
        ("aged.json", NOW - timedelta(days=8)),
        ("boundary.json", NOW - timedelta(days=7)),
        ("fresh.json", NOW - timedelta(hours=3)),
    ):
        (qdir / name).write_text(json.dumps({"ts": stamp.isoformat()}), encoding="utf-8")
    observed, _ = objectives_sensor.observe(tmp_path, now=NOW)
    assert observed["values"]["quarantine_backlog_aged"] == 1


def test_each_missing_objective_source_is_omitted_not_zero(tmp_path):
    _state(tmp_path)
    observed, _ = objectives_sensor.observe(tmp_path, now=NOW)
    assert observed["values"] == {}


def test_each_unreadable_objective_source_is_omitted(tmp_path):
    state = _state(tmp_path)
    (state / "zernio_analytics.jsonl").write_text("not-json\n", encoding="utf-8")
    (state / "kernel").mkdir()
    (state / "kernel" / "frequency.jsonl").write_text("{}\n", encoding="utf-8")
    (state / "quarantine").mkdir()
    (state / "quarantine" / "bad.json").write_text("{}", encoding="utf-8")
    observed, _ = objectives_sensor.observe(tmp_path, now=NOW)
    assert observed["values"] == {}


def test_atomic_write_replaces_output_and_leaves_no_tmp(tmp_path, monkeypatch):
    state = _state(tmp_path)
    (state / "quarantine").mkdir()
    monkeypatch.setattr(objectives_sensor, "_utc_now", lambda: NOW)
    assert objectives_sensor.main(["--root", str(tmp_path)]) == 0
    output = json.loads((state / "objectives_observed.json").read_text(encoding="utf-8"))
    assert output["schema"] == "objectives-observed/v1"
    assert output["values"] == {"quarantine_backlog_aged": 0}
    assert list(state.glob(".objectives_observed.json.*.tmp")) == []


def test_baselines_only_observable_and_never_in_objectives(tmp_path):
    state = _state(tmp_path)
    _jsonl(state / "call_joins.jsonl", [
        {"metric": "discovery_calls_booked", "value": 3.0},
        {"metric": "discovery_calls_booked_by_source", "value": 2.0},
    ])
    _jsonl(state / "zernio_analytics.jsonl", [
        {"metric": "platform_verified", "value": 1.0},
        {"metric": "impressions", "value": 20.0},
        {"metric": "engagement_rate_per_surface", "value": 4.5},
    ])
    observed, baselines = objectives_sensor.observe(tmp_path, now=NOW)
    assert {row["metric"] for row in baselines} == {
        "discovery_calls_booked", "impressions", "engagement_rate_per_surface"
    }
    assert "discovery_calls_booked" not in observed["values"]
    assert "impressions" not in observed["values"]


def _gate(tmp_path: Path, values: dict, *extra: str) -> subprocess.CompletedProcess[str]:
    observed = tmp_path / "observed.json"
    observed.write_text(json.dumps({
        "schema": "objectives-observed/v1", "ts": NOW.isoformat(), "values": values,
    }), encoding="utf-8")
    return subprocess.run([
        sys.executable, "-m", "factory.objectives_verify", "--name", "social",
        "--charter", str(REPO / "departments/social/charter.yaml"),
        "--objectives-file", str(observed), "--allow-stale", *extra,
    ], cwd=REPO, text=True, capture_output=True, check=False)


def test_objectives_gate_green_breach_and_unknown_modes(tmp_path):
    green = _gate(tmp_path, {
        "platform_verified_delivery_pct": 100,
        "posting_volume_week": 4,
        "quarantine_backlog_aged": 0,
    })
    assert green.returncode == 0
    breach = _gate(tmp_path, {
        "platform_verified_delivery_pct": 100,
        "posting_volume_week": 4,
        "quarantine_backlog_aged": 1,
    })
    assert breach.returncode == 1
    assert "WHY quarantine_backlog_aged observed: 1 exceeds maximum 0" in breach.stdout
    absent = _gate(tmp_path, {})
    allowed = _gate(tmp_path, {}, "--allow-unknown")
    assert absent.returncode == 1
    assert "observed: absent (honest unknown)" in absent.stdout
    assert allowed.returncode == 0

