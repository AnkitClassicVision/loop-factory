from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from departments.podcast.runtime import hopper_sensor
from factory import runrecord
from tests.record_fixture import promote_factory_records


pytestmark = pytest.mark.usefixtures("factory_record_spool")


REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_SCRIPT = REPO_ROOT / "departments/podcast/runtime/podcast_daily.sh"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _sources(tmp_path: Path, episodes: list[dict] | None = None) -> Path:
    path = tmp_path / "sources"
    _write_json(path / "publish_schedule.json", {"episodes": episodes or []})
    return path


def _episode(
    pipeline_repo: Path,
    episode_id: str,
    *,
    publish_date: str | None,
    recorded: bool,
    media: bool,
    published: bool = False,
) -> Path:
    episode_dir = pipeline_repo / "episodes" / episode_id
    timestamps = {}
    if recorded:
        timestamps["recording-done"] = "2026-08-01T12:00:00+00:00"
    if published:
        timestamps["published"] = "2026-08-01T13:00:00+00:00"
    _write_json(
        episode_dir / "episode.json",
        {
            "stage": "published" if published else "publish-queued",
            "publish_date": publish_date,
            "stage_timestamps": timestamps,
            "publish_status": (
                {"transistor": {"status": "published"}} if published else None
            ),
        },
    )
    if media:
        media_path = episode_dir / "raw" / "recording.webm"
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(b"recorded-media")
    return episode_dir


def _run(
    tmp_path: Path,
    pipeline_repo: Path,
    *,
    today: date = date(2026, 8, 2),
) -> tuple[Path, dict]:
    state_dir = tmp_path / "department" / "state"
    observation = hopper_sensor.run(
        state_dir,
        _sources(tmp_path),
        pipeline_repo,
        today=today,
    )
    promote_factory_records(state_dir)
    document = json.loads(
        (state_dir / "objectives_observed.json").read_text(encoding="utf-8")
    )
    return state_dir, {"observation": observation, "document": document}


def test_hopper_counts_only_future_scheduled_recorded_unpublished_media(tmp_path):
    pipeline = tmp_path / "pipeline"
    _episode(
        pipeline,
        "banked-interview",
        publish_date="2026-08-06",
        recorded=True,
        media=True,
    )
    _episode(
        pipeline,
        "banked-solo",
        publish_date="2026-08-13",
        recorded=True,
        media=True,
    )
    _episode(
        pipeline,
        "not-recorded",
        publish_date="2026-08-20",
        recorded=False,
        media=False,
    )
    _episode(
        pipeline,
        "already-published",
        publish_date="2026-08-06",
        recorded=True,
        media=True,
        published=True,
    )
    _episode(
        pipeline,
        "stale-overdue",
        publish_date="2026-07-30",
        recorded=True,
        media=True,
    )
    _episode(
        pipeline,
        "unscheduled-wip",
        publish_date=None,
        recorded=True,
        media=True,
    )

    _, result = _run(tmp_path, pipeline)

    assert result["document"]["values"]["hopper_depth"] == 2
    assert result["observation"]["metrics"]["hopper"]["counted_episode_ids"] == [
        "banked-interview",
        "banked-solo",
    ]


def test_zero_recorded_unpublished_is_a_real_observed_zero(tmp_path):
    pipeline = tmp_path / "pipeline"
    _episode(
        pipeline,
        "future-not-recorded",
        publish_date="2026-08-13",
        recorded=False,
        media=False,
    )

    _, result = _run(tmp_path, pipeline)

    assert result["document"]["values"]["hopper_depth"] == 0
    assert result["observation"]["status"] == "ok"


def test_missing_episode_source_emits_no_hopper_depth(tmp_path):
    pipeline = tmp_path / "missing-pipeline"

    _, result = _run(tmp_path, pipeline)

    assert "hopper_depth" not in result["document"]["values"]
    assert result["observation"]["status"] == "unknown"


def test_recording_receipt_without_media_is_not_counted(tmp_path):
    pipeline = tmp_path / "pipeline"
    _episode(
        pipeline,
        "receipt-only",
        publish_date="2026-08-13",
        recorded=True,
        media=False,
    )

    _, result = _run(tmp_path, pipeline)

    assert result["document"]["values"]["hopper_depth"] == 0


def test_objectives_document_is_atomic_schema_valid_and_preserves_foreign_values(
    tmp_path,
):
    pipeline = tmp_path / "pipeline"
    (pipeline / "episodes").mkdir(parents=True)
    state_dir = tmp_path / "department" / "state"
    _write_json(
        state_dir / "objectives_observed.json",
        {
            "schema": "objectives-observed/v1",
            "ts": "old",
            "values": {
                "foreign_metric": 17,
                "hopper_depth": 99,
                "publish_reliability": 42,
            },
        },
    )

    hopper_sensor.run(
        state_dir,
        _sources(tmp_path),
        pipeline,
        today=date(2026, 8, 2),
    )

    document = json.loads(
        (state_dir / "objectives_observed.json").read_text(encoding="utf-8")
    )
    assert set(document) == {"schema", "ts", "values"}
    assert document["schema"] == "objectives-observed/v1"
    assert document["values"] == {"foreign_metric": 17, "hopper_depth": 0}
    assert not list(state_dir.glob(".objectives_observed.json.*.tmp"))


def test_observation_row_is_appended(tmp_path):
    pipeline = tmp_path / "pipeline"
    (pipeline / "episodes").mkdir(parents=True)

    state_dir, result = _run(tmp_path, pipeline)

    rows = [
        json.loads(line)
        for line in (state_dir / "observations.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert rows == [result["observation"]]
    assert rows[0]["sensor"] == "hopper"


def test_one_valid_runs_v2_record_per_invocation_with_daily_dedupe_key(tmp_path):
    pipeline = tmp_path / "pipeline"
    (pipeline / "episodes").mkdir(parents=True)
    state_dir, _ = _run(tmp_path, pipeline)

    rows = [
        json.loads(line)
        for line in (state_dir / "runs-v2.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert len(rows) == 1
    record = rows[0]
    assert runrecord.validate_record(record) == record
    assert record["node"] == "hopper_sensor"
    assert record["trigger"]["dedupe_key"] == (
        f"{datetime.now(timezone.utc).date().isoformat()}-hopper_sensor"
    )
    assert record["external_actions_taken"] == 0


def test_forced_failure_appends_error_record_and_cli_exits_nonzero(tmp_path):
    pipeline = tmp_path / "pipeline"
    (pipeline / "episodes").mkdir(parents=True)
    state_dir = tmp_path / "department" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "objectives_observed.json").write_text(
        "{broken", encoding="utf-8"
    )
    sources = _sources(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "departments/podcast/runtime/hopper_sensor.py"),
            "--state-dir",
            str(state_dir),
            "--sources",
            str(sources),
            "--pipeline-repo",
            str(pipeline),
            "--shadow",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    promote_factory_records(state_dir)
    records = [
        json.loads(line)
        for line in (state_dir / "runs-v2.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert len(records) == 1
    assert records[0]["status"] == "error"
    assert records[0]["errors"] == ["JSONDecodeError"]


def test_same_day_publish_verifier_evidence_computes_reliability(tmp_path):
    today = datetime.now(timezone.utc).date()
    pipeline = tmp_path / "pipeline"
    (pipeline / "episodes").mkdir(parents=True)
    state_dir = tmp_path / "department" / "state"
    sources = _sources(
        tmp_path,
        [
            {"episode_id": "live", "publish_date": today.isoformat()},
            {"episode_id": "late", "publish_date": today.isoformat()},
        ],
    )
    rows = [
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "sensor": "publishday",
            "subject": "live",
            "status": "ok",
        },
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "sensor": "publishday",
            "subject": "late",
            "status": "fail",
        },
    ]
    state_dir.mkdir(parents=True)
    (state_dir / "observations.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    hopper_sensor.run(state_dir, sources, pipeline, today=today)

    document = json.loads(
        (state_dir / "objectives_observed.json").read_text(encoding="utf-8")
    )
    assert document["values"]["publish_reliability"] == 50.0


def test_daily_script_wires_hopper_after_manifest_before_dag_and_compare():
    lines = DAILY_SCRIPT.read_text(encoding="utf-8").splitlines()
    manifest = next(
        index for index, line in enumerate(lines) if "runtime/manifest_sensor.py" in line
    )
    hopper = next(
        index for index, line in enumerate(lines) if "runtime/hopper_sensor.py" in line
    )
    dag = next(
        index for index, line in enumerate(lines) if "runtime/dag_supervisor.py" in line
    )
    compare = next(
        index for index, line in enumerate(lines) if "runtime/compare_charter.py" in line
    )

    assert hopper == manifest + 1
    assert manifest < hopper < dag < compare
    assert "factory/launch.py" in lines[hopper]
    assert "--shadow" in lines[hopper]
    assert '--sources "${SOURCES}"' in lines[hopper]


def test_validate_podcast_passes_with_hopper_node_traced():
    result = subprocess.run(
        [sys.executable, "loopfactory.py", "validate", "--name", "podcast"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
