"""Adversarial integrity tests for podcast incident and record persistence."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from departments.podcast.runtime import escalate_outbox
from departments.podcast.runtime import fingerprint_dedup
from departments.podcast.runtime import record


POISONED_FIXTURES = Path(__file__).with_name("fixtures") / "poisoned"


def _candidate(
    *,
    ts: str = "2026-07-22T12:00:00+00:00",
    failure_class: str = "receipt_stale",
    severity: str = "high",
) -> dict:
    return {
        "ts": ts,
        "sensor": "receipt",
        "subject": "podcast-loop-health",
        "failure_class": failure_class,
        "severity": severity,
        "setpoint": "receipt is current",
        "observed": failure_class,
        "evidence": [f"fixture://{failure_class}"],
        "one_question": "What blocked a healthy receipt?",
    }


def _jsonl_rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_newer_lower_severity_never_downgrades_open_incident():
    incidents, _ = fingerprint_dedup.merge_candidates([_candidate()])
    key = next(iter(incidents))

    incidents, _ = fingerprint_dedup.merge_candidates(
        [
            _candidate(
                ts="2026-07-22T12:05:00+00:00",
                failure_class="receipt_unknown",
                severity="med",
            )
        ],
        incidents,
    )

    assert incidents[key]["state"] == "open"
    assert incidents[key]["failure_class"] == "receipt_unknown"
    assert incidents[key]["severity"] == "high"


def test_missing_candidates_freezes_resolution_then_present_empty_resumes(
    tmp_path, capsys
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    incidents, _ = fingerprint_dedup.merge_candidates([_candidate()])
    key = next(iter(incidents))
    incidents[key]["consecutive_healthy"] = 2
    record.atomic_write_json(state_dir / "incidents.json", incidents)
    (state_dir / "observations.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-07-23T12:00:00+00:00",
                "sensor": "receipt",
                "subject": "podcast-loop-health",
                "status": "ok",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    frozen = fingerprint_dedup.run_dedup(state_dir)

    assert frozen[key]["state"] == "open"
    assert frozen[key]["consecutive_healthy"] == 2
    assert "evidence unavailable" in capsys.readouterr().err
    receipt_rows = _jsonl_rows(state_dir / "runs.jsonl")
    assert receipt_rows[-1]["payload_summary"]["evidence_available"] is False
    assert "froze all incident health accrual" in receipt_rows[-1]["payload_summary"][
        "note"
    ]

    record.atomic_write_json(state_dir / "incident_candidates.json", [])
    resumed = fingerprint_dedup.run_dedup(state_dir)

    assert resumed[key]["state"] == "resolved"
    assert resumed[key]["consecutive_healthy"] == 3


def test_poisoned_resolved_recurrence_becomes_defect_and_escalates_once(tmp_path):
    poisoned = json.loads(
        (POISONED_FIXTURES / "resolved_fingerprint_recurs.json").read_text(
            encoding="utf-8"
        )
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    fixture_incident = next(iter(poisoned["incidents"].values()))
    assert poisoned["new_observation"]["fingerprint"] == fixture_incident["fingerprint"]

    sensor = "receipt"
    subject = "referral-flywheel"
    key = fingerprint_dedup.fingerprint(sensor, subject)
    resolved = {
        **fixture_incident,
        "fingerprint": key,
        "sensor": sensor,
        "subject": subject,
        "severity": "high",
        "evidence": ["fixture://resolved_fingerprint_recurs"],
        "escalated": True,
        "escalated_at": "2023-07-15T08:31:00Z",
    }
    record.atomic_write_json(state_dir / "incidents.json", {key: resolved})
    record.atomic_write_json(
        state_dir / "incident_candidates.json",
        [
            {
                "ts": "2023-07-16T08:30:00Z",
                "sensor": sensor,
                "subject": subject,
                "failure_class": fixture_incident["failure_class"],
                "severity": "high",
                "setpoint": fixture_incident["setpoint"],
                "observed": fixture_incident["observed"],
                "evidence": ["fixture://recurrence"],
                "one_question": fixture_incident["one_question"],
            }
        ],
    )

    deduplicated = fingerprint_dedup.run_dedup(state_dir)
    first = escalate_outbox.run_escalate(state_dir)
    second = escalate_outbox.run_escalate(state_dir)

    assert deduplicated[key]["state"] == "department_defect"
    assert deduplicated[key]["escalated_defect"] is False
    assert first["outbox_rows"] == 1
    assert second["outbox_rows"] == 0
    outbox_rows = _jsonl_rows(state_dir / "decisions_outbox.jsonl")
    defect_rows = [
        row
        for row in outbox_rows
        if row["context"].get("escalation_marker") == "department_defect:1"
    ]
    assert len(defect_rows) == 1
    persisted = json.loads((state_dir / "incidents.json").read_text(encoding="utf-8"))
    assert persisted[key]["escalated"] is True
    assert persisted[key]["escalated_defect"] is True


def test_record_writer_times_out_while_shared_lock_is_held(tmp_path):
    lock_acquired = threading.Event()
    release_lock = threading.Event()
    thread_errors: list[BaseException] = []

    def hold_lock() -> None:
        try:
            with record.records_lock(tmp_path):
                lock_acquired.set()
                release_lock.wait(timeout=2)
        except BaseException as exc:  # pragma: no cover - relayed to main thread
            thread_errors.append(exc)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert lock_acquired.wait(timeout=1)
    try:
        with pytest.raises(record.RecordsLockTimeout, match="timed out acquiring"):
            record.write_record(
                tmp_path,
                "sense_estate",
                {"count": 1},
                intended_epoch=0,
                lock_timeout=0.05,
            )
        assert not (tmp_path / "runs.jsonl").exists()
        assert not (tmp_path / "STATE.json").exists()
        assert not (tmp_path / "heartbeat").exists()
    finally:
        release_lock.set()
        holder.join(timeout=1)

    assert not thread_errors
    receipt = record.write_record(
        tmp_path, "sense_estate", {"count": 1}, intended_epoch=0
    )
    assert receipt["epoch"] == 0


def test_outbox_append_survives_state_write_crash_without_duplicate(
    tmp_path, monkeypatch
):
    incidents, _ = fingerprint_dedup.merge_candidates([_candidate()])
    key = next(iter(incidents))
    incidents_path = tmp_path / "incidents.json"
    outbox_path = tmp_path / "decisions_outbox.jsonl"
    record.atomic_write_json(incidents_path, incidents)
    real_atomic_write = record.atomic_write_json

    def fail_incident_update(path, value):
        if Path(path) == incidents_path:
            raise OSError("simulated crash after outbox append")
        return real_atomic_write(path, value)

    monkeypatch.setattr(record, "atomic_write_json", fail_incident_update)
    with pytest.raises(OSError, match="simulated crash"):
        escalate_outbox.escalate_new_incidents(incidents_path, outbox_path)

    assert len(_jsonl_rows(outbox_path)) == 1
    unchanged = json.loads(incidents_path.read_text(encoding="utf-8"))
    assert unchanged[key]["escalated"] is False

    monkeypatch.setattr(record, "atomic_write_json", real_atomic_write)
    retry = escalate_outbox.escalate_new_incidents(incidents_path, outbox_path)

    assert retry["outbox_rows"] == 0
    assert len(_jsonl_rows(outbox_path)) == 1
    repaired = json.loads(incidents_path.read_text(encoding="utf-8"))
    assert repaired[key]["escalated"] is True


def test_forged_foreign_outbox_marker_does_not_suppress_escalation(
    tmp_path, capsys
):
    incidents, _ = fingerprint_dedup.merge_candidates([_candidate()])
    key = next(iter(incidents))
    incidents_path = tmp_path / "incidents.json"
    outbox_path = tmp_path / "decisions_outbox.jsonl"
    record.atomic_write_json(incidents_path, incidents)
    forged = {
        "kind": "approval",
        "department": "foreign",
        "issue": "receipt_stale: What blocked a healthy receipt?",
        "context": {
            "fingerprint": key,
            "incident_state": "open",
            "escalation_marker": "open",
            "evidence": ["fixture://forged"],
            "one_question": "What blocked a healthy receipt?",
        },
        "ts": "2026-07-22T12:01:00+00:00",
    }
    malformed = {
        "kind": "escalation",
        "department": "podcast",
        "context": {"fingerprint": key, "escalation_marker": "open"},
    }
    outbox_path.write_text(
        json.dumps(forged) + "\n" + json.dumps(malformed) + "\n",
        encoding="utf-8",
    )

    result = escalate_outbox.escalate_new_incidents(incidents_path, outbox_path)

    assert result["outbox_rows"] == 1
    rows = _jsonl_rows(outbox_path)
    assert len(rows) == 3
    assert rows[-1]["kind"] == "escalation"
    assert rows[-1]["department"] == "podcast"
    assert rows[-1]["context"]["fingerprint"] == key
    assert "ignored malformed escalation outbox row 2" in capsys.readouterr().err
