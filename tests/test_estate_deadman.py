from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from factory import estate_deadman


NOW = datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc)


def _write_registry(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "demo.yaml").write_text(
        """entries:
  - id: demo
    owner: owner
    surface: department
    schedule: daily
    health_check: test
    heartbeat_path: departments/demo/state/heartbeats.jsonl
    kill_switch: stop-demo
""",
        encoding="utf-8",
    )


def _write_estate_state(path: Path, *, when: datetime = NOW, epoch: int = 4) -> None:
    path.mkdir(parents=True)
    (path / "STATE.json").write_text(json.dumps({
        "epoch": epoch,
        "last_cycle_at": when.isoformat(),
        "dept_epochs": {"demo": 2},
        "open_findings": [],
        "escalations": 0,
    }), encoding="utf-8")
    (path / "heartbeats.jsonl").write_text(json.dumps({
        "ts": when.isoformat(),
        "emitter": "estate-manager",
        "kind": "cycle",
        "payload": {"epoch": epoch, "findings": 0, "escalations": 0},
    }) + "\n", encoding="utf-8")


def _report(tmp_path: Path, **kwargs):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state, **kwargs)
    return estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)


def _codes(report):
    return {finding["code"] for finding in report["findings"]}


def test_fresh_consistent_heartbeat_is_healthy(tmp_path):
    report = _report(tmp_path)
    assert report["ok"] is True
    assert report["alarm"] is False


def test_stale_heartbeat_and_state_alarm(tmp_path):
    report = _report(tmp_path, when=NOW - timedelta(hours=2))
    assert report["alarm"] is True
    assert {"estate_heartbeat_stale", "estate_state_stale"} <= _codes(report)


def test_false_green_state_with_unreadable_heartbeat_alarms(tmp_path):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state)
    (state / "heartbeats.jsonl").write_text("not-json\n", encoding="utf-8")

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)

    assert report["alarm"] is True
    assert "estate_heartbeat_unreadable" in _codes(report)


def test_false_green_state_with_non_utf8_heartbeat_alarms(tmp_path):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state)
    (state / "heartbeats.jsonl").write_bytes(b"\xff\xfe")

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)

    assert report["alarm"] is True
    assert "estate_heartbeat_unreadable" in _codes(report)


def test_malformed_earlier_heartbeat_row_does_not_override_valid_last_row(tmp_path):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state)
    valid_last = (state / "heartbeats.jsonl").read_text(encoding="utf-8")
    (state / "heartbeats.jsonl").write_text("not-json\n" + valid_last, encoding="utf-8")

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)

    assert report["ok"] is True


@pytest.mark.parametrize(
    "invalid_row",
    [
        {},
        {
            "ts": NOW.isoformat(),
            "emitter": "other",
            "kind": "cycle",
            "payload": {"epoch": 1, "findings": 0, "escalations": 0},
        },
        {
            "ts": NOW.isoformat(),
            "emitter": "estate-manager",
            "kind": "cycle",
            "payload": {"epoch": True, "findings": 0, "escalations": 0},
        },
    ],
)
def test_schema_invalid_earlier_heartbeat_object_is_ignored_when_last_is_valid(tmp_path, invalid_row):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state)
    valid_last = (state / "heartbeats.jsonl").read_text(encoding="utf-8")
    (state / "heartbeats.jsonl").write_text(
        json.dumps(invalid_row) + "\n" + valid_last,
        encoding="utf-8",
    )

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)

    assert report["ok"] is True


def test_non_utf8_earlier_heartbeat_is_ignored_when_last_is_valid(tmp_path):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state)
    valid_last = (state / "heartbeats.jsonl").read_bytes()
    (state / "heartbeats.jsonl").write_bytes(b"\xff\xfe\n" + valid_last)

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)

    assert report["ok"] is True


def test_epoch_mismatch_alarms(tmp_path):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state, epoch=4)
    heartbeat = json.loads((state / "heartbeats.jsonl").read_text(encoding="utf-8"))
    heartbeat["payload"]["epoch"] = 3
    (state / "heartbeats.jsonl").write_text(json.dumps(heartbeat) + "\n", encoding="utf-8")

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)

    assert "estate_epoch_mismatch" in _codes(report)


def test_same_epoch_with_different_cycle_timestamps_alarms(tmp_path):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state, epoch=4)
    heartbeat = json.loads((state / "heartbeats.jsonl").read_text(encoding="utf-8"))
    heartbeat["ts"] = (NOW - timedelta(minutes=5)).isoformat()
    (state / "heartbeats.jsonl").write_text(json.dumps(heartbeat) + "\n", encoding="utf-8")

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)

    assert "estate_timestamp_mismatch" in _codes(report)


@pytest.mark.parametrize(
    "state_updates",
    [
        {"epoch": True},
        {"dept_epochs": []},
        {"open_findings": {}},
        {"escalations": True},
    ],
)
def test_invalid_state_schema_alarms(tmp_path, state_updates):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state)
    state_row = json.loads((state / "STATE.json").read_text(encoding="utf-8"))
    state_row.update(state_updates)
    (state / "STATE.json").write_text(json.dumps(state_row), encoding="utf-8")

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)

    assert "estate_state_schema_invalid" in _codes(report)


@pytest.mark.parametrize(
    ("state_findings", "state_escalations", "heartbeat_findings", "heartbeat_escalations"),
    [
        ([{"code": "dead_manager"}], 1, 0, 1),
        ([], 1, 0, 0),
    ],
)
def test_final_heartbeat_counters_must_match_state(
    tmp_path,
    state_findings,
    state_escalations,
    heartbeat_findings,
    heartbeat_escalations,
):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state)
    state_row = json.loads((state / "STATE.json").read_text(encoding="utf-8"))
    state_row["open_findings"] = state_findings
    state_row["escalations"] = state_escalations
    (state / "STATE.json").write_text(json.dumps(state_row), encoding="utf-8")
    heartbeat = json.loads((state / "heartbeats.jsonl").read_text(encoding="utf-8"))
    heartbeat["payload"]["findings"] = heartbeat_findings
    heartbeat["payload"]["escalations"] = heartbeat_escalations
    (state / "heartbeats.jsonl").write_text(json.dumps(heartbeat) + "\n", encoding="utf-8")

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)

    assert "estate_counter_mismatch" in _codes(report)


def test_boolean_epochs_alarm_instead_of_passing_as_integers(tmp_path):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    _write_registry(registry)
    _write_estate_state(state, epoch=True)

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW, max_age_seconds=3600)

    assert "estate_heartbeat_unreadable" in _codes(report)


def test_future_timestamp_alarms(tmp_path):
    report = _report(tmp_path, when=NOW + timedelta(minutes=10))
    assert {"estate_heartbeat_future", "estate_state_future"} <= _codes(report)


def test_invalid_or_empty_registry_alarms(tmp_path):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    registry.mkdir()
    _write_estate_state(state)
    empty = estate_deadman.evaluate_deadman(registry, state, now=NOW)
    assert "estate_registry_empty" in _codes(empty)
    (registry / "bad.yaml").write_text("poison\n", encoding="utf-8")
    invalid = estate_deadman.evaluate_deadman(registry, state, now=NOW)
    assert "estate_registry_unreadable" in _codes(invalid)


def test_registry_decode_failure_becomes_alarm(tmp_path):
    registry = tmp_path / "registry.d"
    state = tmp_path / "state"
    registry.mkdir()
    _write_estate_state(state)
    (registry / "bad.yaml").write_bytes(b"\xff\xfe")

    report = estate_deadman.evaluate_deadman(registry, state, now=NOW)

    assert "estate_registry_unreadable" in _codes(report)


def test_alarm_uses_factory_outbox_shape(tmp_path):
    report = _report(tmp_path, when=NOW - timedelta(hours=2))
    outbox = tmp_path / "outbox.jsonl"

    result = estate_deadman.raise_alarm(report, outbox)

    assert result["escalated"] is True
    packet = json.loads(outbox.read_text(encoding="utf-8"))
    assert packet["kind"] == "escalation"
    assert packet["department"] == "estate"
    assert packet["context"]["source"] == "estate-deadman"
    assert "estate_heartbeat_stale" in packet["context"]["finding_codes"]


def test_unchanged_alarm_is_capped_to_once_per_six_hours(tmp_path):
    report = _report(tmp_path, when=NOW - timedelta(hours=2))
    outbox = tmp_path / "outbox.jsonl"
    alarm_state = tmp_path / "estate-deadman" / "alarm_state.json"

    first = estate_deadman.raise_alarm_with_cooldown(
        report, outbox, alarm_state, now=NOW, cooldown_seconds=6 * 3600,
    )
    second = estate_deadman.raise_alarm_with_cooldown(
        report, outbox, alarm_state, now=NOW + timedelta(hours=1), cooldown_seconds=6 * 3600,
    )
    third = estate_deadman.raise_alarm_with_cooldown(
        report, outbox, alarm_state, now=NOW + timedelta(hours=6), cooldown_seconds=6 * 3600,
    )

    assert first["alarmed"] is True
    assert second["suppressed"] is True
    assert third["alarmed"] is True
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 2
    assert alarm_state.parent.name == "estate-deadman"


def test_overlapping_unchanged_alarms_append_only_once(tmp_path, monkeypatch):
    report = _report(tmp_path, when=NOW - timedelta(hours=2))
    outbox = tmp_path / "outbox.jsonl"
    alarm_state = tmp_path / "estate-deadman" / "alarm_state.json"
    start = threading.Barrier(2)
    real_raise_alarm = estate_deadman.raise_alarm

    def slow_raise_alarm(*args, **kwargs):
        time.sleep(0.05)
        return real_raise_alarm(*args, **kwargs)

    def invoke():
        start.wait()
        return estate_deadman.raise_alarm_with_cooldown(
            report,
            outbox,
            alarm_state,
            now=NOW,
            cooldown_seconds=6 * 3600,
        )

    monkeypatch.setattr(estate_deadman, "raise_alarm", slow_raise_alarm)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result() for future in (pool.submit(invoke), pool.submit(invoke))]

    assert sum(outcome["alarmed"] for outcome in outcomes) == 1
    assert sum(outcome["suppressed"] for outcome in outcomes) == 1
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 1


def test_alarm_and_healthy_state_writes_are_serialized(tmp_path, monkeypatch):
    report = _report(tmp_path, when=NOW - timedelta(hours=2))
    outbox = tmp_path / "outbox.jsonl"
    alarm_state = tmp_path / "estate-deadman" / "alarm_state.json"
    start = threading.Barrier(2)
    real_atomic_write = estate_deadman._atomic_write_json
    active_writes = 0
    max_active_writes = 0
    counter_lock = threading.Lock()

    def observed_atomic_write(*args, **kwargs):
        nonlocal active_writes, max_active_writes
        with counter_lock:
            active_writes += 1
            max_active_writes = max(max_active_writes, active_writes)
        try:
            time.sleep(0.05)
            return real_atomic_write(*args, **kwargs)
        finally:
            with counter_lock:
                active_writes -= 1

    def alarm():
        start.wait()
        estate_deadman.raise_alarm_with_cooldown(report, outbox, alarm_state, now=NOW)

    def healthy():
        start.wait()
        estate_deadman.record_healthy(alarm_state, now=NOW)

    monkeypatch.setattr(estate_deadman, "_atomic_write_json", observed_atomic_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in (pool.submit(alarm), pool.submit(healthy)):
            future.result()

    assert max_active_writes == 1
    state = json.loads(alarm_state.read_text(encoding="utf-8"))
    assert state["finding_codes"] in ([], sorted(_codes(report)))
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 1


def test_changed_finding_set_bypasses_alarm_cooldown(tmp_path):
    report = _report(tmp_path, when=NOW - timedelta(hours=2))
    outbox = tmp_path / "outbox.jsonl"
    alarm_state = tmp_path / "estate-deadman" / "alarm_state.json"
    estate_deadman.raise_alarm_with_cooldown(report, outbox, alarm_state, now=NOW)
    changed = {**report, "findings": [*report["findings"], {
        "code": "estate_registry_unreadable",
        "detail": "test",
    }]}

    outcome = estate_deadman.raise_alarm_with_cooldown(
        changed,
        outbox,
        alarm_state,
        now=NOW + timedelta(minutes=5),
    )

    assert outcome["alarmed"] is True
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 2


def test_healthy_record_resets_cooldown_for_a_recurrence(tmp_path):
    report = _report(tmp_path, when=NOW - timedelta(hours=2))
    outbox = tmp_path / "outbox.jsonl"
    alarm_state = tmp_path / "estate-deadman" / "alarm_state.json"
    estate_deadman.raise_alarm_with_cooldown(report, outbox, alarm_state, now=NOW)
    estate_deadman.record_healthy(alarm_state, now=NOW + timedelta(minutes=10))

    outcome = estate_deadman.raise_alarm_with_cooldown(
        report,
        outbox,
        alarm_state,
        now=NOW + timedelta(minutes=20),
    )

    assert outcome["alarmed"] is True
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 2


def test_registry_module_load_failure_becomes_alarm_finding(tmp_path, monkeypatch):
    state = tmp_path / "state"
    _write_estate_state(state)

    def fail_load(*_args):
        raise SyntaxError("corrupt module")

    monkeypatch.setattr(estate_deadman, "_load_module", fail_load)
    report = estate_deadman.evaluate_deadman(tmp_path / "registry.d", state, now=NOW)

    assert "estate_registry_unreadable" in _codes(report)


def test_main_attempts_synthetic_alarm_before_internal_error_exit(tmp_path, monkeypatch):
    captured = []

    def fail_evaluation(*_args, **_kwargs):
        raise RuntimeError("deadman infrastructure failed")

    def capture_alarm(report, *_args, **_kwargs):
        captured.append(report)
        return {"alarmed": True, "suppressed": False, "finding_codes": ["deadman_internal_error"]}

    monkeypatch.setattr(estate_deadman, "evaluate_deadman", fail_evaluation)
    monkeypatch.setattr(estate_deadman, "raise_alarm_with_cooldown", capture_alarm)
    monkeypatch.setattr(
        "sys.argv",
        [
            "estate_deadman.py",
            "--outbox", str(tmp_path / "outbox.jsonl"),
            "--alarm-state", str(tmp_path / "deadman" / "alarm_state.json"),
        ],
    )

    assert estate_deadman.main() == 2
    assert captured[0]["findings"][0]["code"] == "deadman_internal_error"


def test_poisoned_registry_self_test_only_mutates_copy(tmp_path):
    registry = tmp_path / "registry.d"
    _write_registry(registry)
    original = (registry / "demo.yaml").read_text(encoding="utf-8")

    report = estate_deadman.poisoned_registry_self_test(registry)

    assert "estate_registry_unreadable" in _codes(report)
    assert (registry / "demo.yaml").read_text(encoding="utf-8") == original


def test_invalid_threshold_refuses_to_evaluate(tmp_path):
    with pytest.raises(ValueError):
        estate_deadman.evaluate_deadman(tmp_path, tmp_path, max_age_seconds=0)
