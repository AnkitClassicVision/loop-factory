"""Proof tests for the read-only podcast DAG supervisory plane."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from departments.podcast.runtime import dag_supervisor


FIXTURE = Path(__file__).with_name("fixtures") / "dag-projection-sample.json"
NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


def _projection() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _write_projection(path: Path, projection: dict) -> None:
    path.write_text(json.dumps(projection), encoding="utf-8")


def _observations(state_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (state_dir / "observations.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]


def _artifact_hash(artifact: dict) -> str:
    material = {key: value for key, value in artifact.items() if key != "content_hash"}
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_healthy_fixture_validates_and_sense_writes_ok_observation(tmp_path):
    projection = _projection()
    assert dag_supervisor.validate_projection(projection, now=NOW) == []

    result = dag_supervisor.sense(FIXTURE, tmp_path / "state", now=NOW)

    assert result["status"] == "ok"
    assert result["findings"] == []
    rows = _observations(tmp_path / "state")
    assert len(rows) == 1
    assert rows[0]["sensor"] == "dag_supervisor"
    assert rows[0]["status"] == "ok"
    assert projection["dag_hash"][:12] in rows[0]["evidence"]
    assert not (tmp_path / "state" / "incident_candidates.json").exists()


def test_changed_step_without_rehash_yields_dag_hash_mismatch():
    projection = _projection()
    projection["steps"][0]["optional"] = True

    findings = dag_supervisor.validate_projection(projection, now=NOW)

    assert [row["kind"] for row in findings] == ["dag_hash_mismatch"]
    assert findings[0]["severity"] == "critical"


def test_silent_skip_is_critical_and_appended_as_consumable_candidate(tmp_path):
    projection = _projection()
    projection["episodes"][0]["audit"]["silent_skips"] = ["publish"]
    projection_path = tmp_path / "projection.json"
    _write_projection(projection_path, projection)

    result = dag_supervisor.sense(projection_path, tmp_path / "state", now=NOW)

    finding = next(row for row in result["findings"] if row["kind"] == "silent_skip")
    assert finding["severity"] == "critical"
    candidates = json.loads(
        (tmp_path / "state" / "incident_candidates.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(candidates) == 1
    assert candidates[0]["failure_class"] == "silent_skip"
    assert candidates[0]["sensor"] == "dag_supervisor"
    assert candidates[0]["subject"] == "episode-101"
    assert candidates[0]["severity"] == "critical"
    assert candidates[0]["setpoint"]
    assert candidates[0]["observed"] == finding["detail"]
    assert candidates[0]["evidence"] == [result["observation"]["evidence"]]
    assert candidates[0]["one_question"]


def test_mutated_skip_artifact_fails_independent_hash_verification():
    projection = _projection()
    artifact = {
        "schema": "episode-skip-v1",
        "step_id": "publish",
        "reason": "owner-authorized exception",
        "detail": "original detail",
    }
    artifact["content_hash"] = _artifact_hash(artifact)
    artifact["detail"] = "mutated after hashing"
    projection["episodes"][0]["skip_artifacts"]["publish"] = artifact
    assert projection["episodes"][0]["audit"]["invalid_skips"] == []

    findings = dag_supervisor.validate_projection(projection, now=NOW)

    invalid = [row for row in findings if row["kind"] == "invalid_skip_artifact"]
    assert len(invalid) == 1
    assert invalid[0]["episode_id"] == "episode-101"


def test_audit_invalid_skips_yields_invalid_skip_artifact():
    projection = _projection()
    projection["episodes"][1]["audit"]["invalid_skips"] = ["publish"]

    findings = dag_supervisor.validate_projection(projection, now=NOW)

    assert [row["kind"] for row in findings] == ["invalid_skip_artifact"]
    assert findings[0]["episode_id"] == "episode-102"
    assert findings[0]["severity"] == "critical"


def test_unreadable_episode_is_critical():
    projection = _projection()
    projection["episodes"][1]["stage"] = "unreadable"

    findings = dag_supervisor.validate_projection(projection, now=NOW)

    assert [row["kind"] for row in findings] == ["unreadable_episode"]
    assert findings[0]["episode_id"] == "episode-102"
    assert findings[0]["severity"] == "critical"


def test_projection_three_days_old_is_stale():
    projection = _projection()
    projection["generated_at"] = "2026-07-28T11:59:59Z"

    findings = dag_supervisor.validate_projection(projection, now=NOW)

    assert [row["kind"] for row in findings] == ["stale_projection"]


def test_unparseable_generated_at_fails_closed_as_stale():
    projection = _projection()
    projection["generated_at"] = "not-a-timestamp"

    findings = dag_supervisor.validate_projection(projection, now=NOW)

    assert [row["kind"] for row in findings] == ["stale_projection"]


def test_missing_projection_alarms_and_main_returns_exit_two(tmp_path):
    projection_path = tmp_path / "missing.json"
    state_dir = tmp_path / "state"

    result = dag_supervisor.sense(projection_path, state_dir, now=NOW)
    exit_code = dag_supervisor.main(
        [
            "--projection",
            str(projection_path),
            "--state-dir",
            str(tmp_path / "cli-state"),
            "--now",
            "2026-07-31T12:00:00Z",
        ]
    )

    assert result["status"] == "alarm"
    assert [row["kind"] for row in result["findings"]] == ["projection_missing"]
    assert _observations(state_dir)[0]["status"] == "alarm"
    assert _observations(state_dir)[0]["metrics"] == {"projection_missing": 1}
    assert exit_code == 2


def test_critical_findings_append_without_overwriting_existing_candidates(tmp_path):
    projection = _projection()
    projection["episodes"][0]["audit"]["silent_skips"] = ["publish"]
    projection_path = tmp_path / "projection.json"
    _write_projection(projection_path, projection)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    existing = {
        "ts": "2026-07-31T11:00:00+00:00",
        "sensor": "receipt",
        "subject": "existing",
        "failure_class": "receipt_stale",
        "severity": "high",
        "setpoint": "fresh",
        "observed": "stale",
        "evidence": ["fixture://existing"],
        "one_question": "What blocked the receipt?",
    }
    (state_dir / "incident_candidates.json").write_text(
        json.dumps([existing]), encoding="utf-8"
    )

    dag_supervisor.sense(projection_path, state_dir, now=NOW)

    candidates = json.loads(
        (state_dir / "incident_candidates.json").read_text(encoding="utf-8")
    )
    assert candidates[0] == existing
    assert candidates[1]["failure_class"] == "silent_skip"


def test_unknown_schema_short_circuits_all_other_checks():
    projection = _projection()
    projection["schema"] = "dag-projection-v2"
    projection["dag_hash"] = "false"
    projection["generated_at"] = "unparseable"
    projection["episodes"][0]["stage"] = "unreadable"
    projection["episodes"][0]["audit"]["silent_skips"] = ["publish"]

    findings = dag_supervisor.validate_projection(projection, now=NOW)

    assert len(findings) == 1
    assert findings[0]["kind"] == "schema_mismatch"
    assert findings[0]["severity"] == "critical"


def test_validation_is_deterministic_for_same_projection_and_now():
    projection = _projection()
    projection["episodes"][0]["audit"]["invalid_skips"] = ["publish"]

    first = dag_supervisor.validate_projection(copy.deepcopy(projection), now=NOW)
    second = dag_supervisor.validate_projection(copy.deepcopy(projection), now=NOW)

    assert first == second
