from __future__ import annotations

import json
import threading

import pytest

from departments.social.runtime import record


def _rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_record_writes_runs_then_state_then_heartbeat(tmp_path, monkeypatch):
    observed: list[str] = []
    real_append = record._append_jsonl
    real_atomic = record.atomic_write_json

    def watch_append(path, value):
        observed.append(path.name)
        return real_append(path, value)

    def watch_atomic(path, value):
        observed.append(path.name)
        return real_atomic(path, value)

    monkeypatch.setattr(record, "_append_jsonl", watch_append)
    monkeypatch.setattr(record, "atomic_write_json", watch_atomic)

    receipt = record.write_record(
        tmp_path,
        "N7-delivery-verify",
        {"verified": True},
        intended_epoch=0,
        now="2026-07-28T12:00:00+00:00",
    )

    assert observed == ["runs.jsonl", "STATE.json", "heartbeats.jsonl"]
    assert receipt["epoch"] == 0
    assert _rows(tmp_path / "runs.jsonl") == [receipt]
    assert json.loads((tmp_path / "STATE.json").read_text())["epoch"] == 0
    assert _rows(tmp_path / "heartbeats.jsonl") == [
        {
            "epoch": 0,
            "node": "N7-delivery-verify",
            "ts": "2026-07-28T12:00:00+00:00",
        }
    ]


def test_epoch_reuse_and_skip_are_refused_without_new_rows(tmp_path):
    record.write_record(tmp_path, "N1", {"ok": True}, intended_epoch=0)
    runs_before = (tmp_path / "runs.jsonl").read_text(encoding="utf-8")
    heartbeat_before = (tmp_path / "heartbeats.jsonl").read_text(encoding="utf-8")

    with pytest.raises(record.EpochError, match="already reached"):
        record.write_record(tmp_path, "N2", {"ok": True}, intended_epoch=0)
    with pytest.raises(record.EpochError, match="would skip"):
        record.write_record(tmp_path, "N2", {"ok": True}, intended_epoch=2)

    assert (tmp_path / "runs.jsonl").read_text(encoding="utf-8") == runs_before
    assert (tmp_path / "heartbeats.jsonl").read_text(encoding="utf-8") == heartbeat_before
    assert json.loads((tmp_path / "STATE.json").read_text())["epoch"] == 0


def test_record_writer_times_out_while_records_lock_is_held(tmp_path):
    lock_acquired = threading.Event()
    release_lock = threading.Event()
    errors: list[BaseException] = []

    def hold_lock():
        try:
            with record.records_lock(tmp_path):
                lock_acquired.set()
                release_lock.wait(timeout=2)
        except BaseException as exc:
            errors.append(exc)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert lock_acquired.wait(timeout=1)
    try:
        with pytest.raises(record.RecordsLockTimeout, match="timed out acquiring"):
            record.write_record(
                tmp_path,
                "N1",
                {"ok": True},
                intended_epoch=0,
                lock_timeout=0.05,
            )
        assert not (tmp_path / "runs.jsonl").exists()
        assert not (tmp_path / "STATE.json").exists()
        assert not (tmp_path / "heartbeats.jsonl").exists()
    finally:
        release_lock.set()
        holder.join(timeout=1)

    assert not errors


def test_corrupt_state_epoch_fails_closed_before_append(tmp_path):
    (tmp_path / "STATE.json").write_text('{"epoch":"zero"}', encoding="utf-8")

    with pytest.raises(record.EpochError, match="epoch is invalid"):
        record.write_record(tmp_path, "N1", {"ok": True})

    assert not (tmp_path / "runs.jsonl").exists()
    assert not (tmp_path / "heartbeats.jsonl").exists()
