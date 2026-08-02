"""Proof that every podcast daily-chain node emits one valid v2 run record."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from departments.podcast.runtime import compare_charter
from departments.podcast.runtime import dag_supervisor
from departments.podcast.runtime import escalate_outbox
from departments.podcast.runtime import fingerprint_dedup
from departments.podcast.runtime import manifest_sensor
from departments.podcast.runtime import pipeline_sensor
from departments.podcast.runtime import publish_verifier
from departments.podcast.runtime import sense_estate
from factory import runrecord


REPO_ROOT = Path(__file__).resolve().parents[1]
DAG_FIXTURE = (
    REPO_ROOT
    / "departments"
    / "podcast"
    / "tests"
    / "fixtures"
    / "dag-projection-sample.json"
)
DAG_NOW = datetime(2026, 7, 31, 12, tzinfo=timezone.utc)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_charter(path: Path, target: int = 1) -> None:
    path.write_text(
        f"""
department: podcast
owner: ankit
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [autonomy_state]
setpoints:
  outcome_additional:
    - metric: pipeline_guests
      target: {target}
""",
        encoding="utf-8",
    )


def _records(state_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (state_dir / "runs-v2.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]


def _assert_one_valid(state_dir: Path, node: str, status: str = "ok") -> dict:
    records = _records(state_dir)
    assert len(records) == 1
    record = records[0]
    assert runrecord.validate_record(record) == record
    assert record["department"] == "podcast"
    assert record["node"] == node
    assert record["status"] == status
    assert record["engine"] is None
    assert record["model"] is None
    assert record["auth_class"] is None
    assert record["usage"] is None
    assert record["cost"] is None
    assert record["external_actions_taken"] == 0
    return record


def _healthy_systemctl(_unit: str) -> dict[str, str]:
    return {
        "ActiveState": "active",
        "SubState": "running",
        "Result": "success",
        "ExecMainStatus": "0",
    }


def test_sense_estate_happy_path_appends_one_valid_record(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    estate_path = tmp_path / "estate.json"
    _write_json(
        estate_path,
        {
            "systemd_user_timers": [
                {
                    "name": "fixture-timer",
                    "expected_cadence": "daily",
                    "stale_after_minutes": 60,
                    "evidence": "timer_only",
                }
            ],
            "channels": [],
            "vps": {"host": "fixture.invalid", "services": []},
        },
    )

    observations = sense_estate.run_sense(
        state_dir,
        estate_path=estate_path,
        systemctl_runner=_healthy_systemctl,
    )

    assert observations[0]["status"] == "ok"
    record = _assert_one_valid(state_dir, "sense_estate")
    assert record["artifacts"] == [
        str(state_dir / "observations.jsonl"),
        str(state_dir / "runs.jsonl"),
    ]


def test_pipeline_sensor_happy_path_threads_release_into_one_record(tmp_path):
    department_dir = tmp_path / "podcast"
    state_dir = department_dir / "state"
    sources = tmp_path / "sources"
    sources.mkdir()
    charter = tmp_path / "charter.yaml"
    _write_charter(charter)
    _write_json(
        sources / "calendar.json",
        [
            {
                "guest": "Fixture Guest",
                "email": "guest@example.test",
                "event_type": "podcast recording",
            }
        ],
    )
    _write_json(
        sources / "hubspot_contacts.json",
        [
            {
                "name": "Fixture Guest",
                "email": "guest@example.test",
                "podcast_status": "scheduled",
            }
        ],
    )
    release_dir = department_dir / "releases" / "release-fixture"
    release_dir.mkdir(parents=True)
    (department_dir / "releases" / "current").write_text(
        "release-fixture\n", encoding="utf-8"
    )
    _write_json(
        release_dir / "manifest.json",
        {"hash": "hash-fixture", "source_ref": "source-fixture"},
    )

    observation = pipeline_sensor.run(state_dir, sources, charter)

    assert observation["status"] == "ok"
    record = _assert_one_valid(state_dir, "pipeline_sensor")
    assert record["release"] == {
        "hash": "hash-fixture",
        "source_ref": "source-fixture",
    }


def test_publish_verifier_happy_path_appends_one_record_without_release(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    sources = tmp_path / "sources"
    sources.mkdir()
    _write_json(sources / "publish_schedule.json", {"episodes": []})

    observations = publish_verifier.run(
        state_dir, sources, today=date(2026, 8, 2)
    )

    assert observations[0]["status"] == "ok"
    record = _assert_one_valid(state_dir, "publish_verifier")
    assert record["release"] is None


def test_manifest_sensor_happy_path_appends_one_valid_record(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    sources = tmp_path / "sources"
    sources.mkdir()
    _write_json(sources / "guest_manifests.json", {"guests": []})

    observations = manifest_sensor.run(state_dir, sources)

    assert observations[0]["status"] == "ok"
    _assert_one_valid(state_dir, "manifest_sensor")


def test_dag_supervisor_happy_path_appends_one_valid_record(tmp_path):
    state_dir = tmp_path / "podcast" / "state"

    result = dag_supervisor.sense(DAG_FIXTURE, state_dir, now=DAG_NOW)

    assert result["status"] == "ok"
    _assert_one_valid(state_dir, "dag_supervisor")


def test_compare_charter_happy_path_appends_one_valid_record(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    state_dir.mkdir(parents=True)
    charter = tmp_path / "charter.yaml"
    _write_charter(charter)
    observation = {
        "ts": "2026-08-02T12:00:00+00:00",
        "sensor": "pipeline",
        "subject": "recording-pipeline",
        "status": "ok",
        "evidence": "fixture://pipeline",
        "detail": "healthy",
        "metrics": {"count": 1},
    }
    (state_dir / "observations.jsonl").write_text(
        json.dumps(observation) + "\n", encoding="utf-8"
    )

    candidates = compare_charter.run_compare(
        state_dir, charter_path=charter
    )

    assert candidates == []
    _assert_one_valid(state_dir, "compare_charter")


def test_fingerprint_dedup_happy_path_appends_one_valid_record(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    _write_json(state_dir / "incident_candidates.json", [])

    incidents = fingerprint_dedup.run_dedup(state_dir)

    assert incidents == {}
    _assert_one_valid(state_dir, "fingerprint_dedup")


def test_escalate_outbox_happy_path_appends_one_valid_record(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    _write_json(state_dir / "incidents.json", {})

    result = escalate_outbox.run_escalate(state_dir)

    assert result["outbox_rows"] == 0
    assert result["delivered_count"] == 0
    record = _assert_one_valid(state_dir, "escalate_outbox")
    assert record["artifacts"] == [str(state_dir / "runs.jsonl")]


def test_forced_node_failure_records_error_and_preserves_nonzero_exit(tmp_path):
    state_dir = tmp_path / "podcast" / "state"

    exit_code = dag_supervisor.main(
        [
            "--projection",
            str(tmp_path / "missing-projection.json"),
            "--state-dir",
            str(state_dir),
            "--now",
            "2026-08-02T12:00:00Z",
        ]
    )

    assert exit_code == 2
    record = _assert_one_valid(state_dir, "dag_supervisor", status="error")
    assert record["errors"] == ["projection_missing"]


def test_daily_dedupe_key_contains_utc_date_and_node_name(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    sources = tmp_path / "sources"
    sources.mkdir()
    _write_json(sources / "guest_manifests.json", {"guests": []})
    before = datetime.now(timezone.utc).date().isoformat()

    manifest_sensor.run(state_dir, sources)

    after = datetime.now(timezone.utc).date().isoformat()
    record = _records(state_dir)[0]
    assert record["trigger"] == {
        "kind": "time",
        "id": "podcast-daily",
        "dedupe_key": record["trigger"]["dedupe_key"],
    }
    assert record["trigger"]["dedupe_key"] in {
        f"{before}-manifest_sensor",
        f"{after}-manifest_sensor",
    }


def test_append_failure_is_logged_and_makes_node_raise(tmp_path, monkeypatch, caplog):
    state_dir = tmp_path / "podcast" / "state"
    sources = tmp_path / "sources"
    sources.mkdir()
    _write_json(sources / "guest_manifests.json", {"guests": []})

    def fail_append(_state_dir, _record):
        raise OSError("simulated append failure")

    monkeypatch.setattr(runrecord, "append_record", fail_append)
    with pytest.raises(OSError, match="simulated append failure"):
        manifest_sensor.run(state_dir, sources)

    assert "failed to append its runs-v2 record" in caplog.text
    assert not (state_dir / "runs-v2.jsonl").exists()


def test_runs_v2_file_is_json_lines_with_one_record_per_invocation(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    sources = tmp_path / "sources"
    sources.mkdir()
    _write_json(sources / "publish_schedule.json", {"episodes": []})

    publish_verifier.run(state_dir, sources, today=date(2026, 8, 2))
    publish_verifier.run(state_dir, sources, today=date(2026, 8, 3))

    lines = (state_dir / "runs-v2.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert all(runrecord.validate_record(record) == record for record in records)
