import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory import estate_deadman


NOW = datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc)


def _write_fixture(tmp_path: Path, heartbeat_ts: datetime | str | None) -> tuple[Path, Path]:
    registry = tmp_path / "estate" / "registry.d"
    estate_state = tmp_path / "estate" / "state"
    department_state = tmp_path / "departments" / "podcast" / "state"
    registry.mkdir(parents=True)
    estate_state.mkdir(parents=True)
    department_state.mkdir(parents=True)
    (registry / "podcast.yaml").write_text(
        """entries:
  - id: podcast
    owner: owner
    surface: department
    schedule: daily
    health_check: test
    heartbeat_path: departments/podcast/state/heartbeats.jsonl
    state_dir: departments/podcast/state
    kill_switch: stop-podcast
""",
        encoding="utf-8",
    )
    (estate_state / "STATE.json").write_text(json.dumps({
        "epoch": 4,
        "last_cycle_at": NOW.isoformat(),
        "dept_epochs": {"podcast": 2},
        "open_findings": [],
        "escalations": 0,
    }), encoding="utf-8")
    (estate_state / "heartbeats.jsonl").write_text(json.dumps({
        "ts": NOW.isoformat(),
        "emitter": "estate-manager",
        "kind": "cycle",
        "payload": {"epoch": 4, "findings": 0, "escalations": 0},
    }) + "\n", encoding="utf-8")
    if heartbeat_ts is not None:
        value = heartbeat_ts.isoformat() if isinstance(heartbeat_ts, datetime) else heartbeat_ts
        (department_state / "conductor-heartbeat.json").write_text(
            json.dumps({"ts": value, "epoch": 3}) + "\n",
            encoding="utf-8",
        )
    return registry, estate_state


def _codes(report):
    return {finding["code"] for finding in report["findings"]}


def test_fresh_conductor_heartbeat_does_not_escalate(tmp_path):
    registry, estate_state = _write_fixture(tmp_path, NOW - timedelta(hours=25))

    report = estate_deadman.evaluate_deadman(registry, estate_state, now=NOW)

    assert report["alarm"] is False
    assert "conductor_heartbeat_stale" not in _codes(report)


def test_stale_conductor_heartbeat_escalates_in_deadman_outbox_format(tmp_path):
    registry, estate_state = _write_fixture(tmp_path, NOW - timedelta(hours=27))
    outbox = tmp_path / "outbox.jsonl"

    report = estate_deadman.evaluate_deadman(registry, estate_state, now=NOW)
    result = estate_deadman.raise_alarm(report, outbox)

    assert result["escalated"] is True
    assert "conductor_heartbeat_stale" in _codes(report)
    packet = json.loads(outbox.read_text(encoding="utf-8"))
    assert packet["kind"] == "escalation"
    assert packet["department"] == "estate"
    assert packet["context"]["source"] == "estate-deadman"
    assert "podcast" in packet["issue"]
    assert "conductor heartbeat stale" in packet["issue"]


def test_missing_conductor_heartbeat_is_not_adopted_and_not_checked(tmp_path):
    registry, estate_state = _write_fixture(tmp_path, None)

    report = estate_deadman.evaluate_deadman(registry, estate_state, now=NOW)

    assert report["alarm"] is False
    assert "conductor_heartbeat_stale" not in _codes(report)
