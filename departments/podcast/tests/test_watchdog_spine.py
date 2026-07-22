"""Negative and contract tests for the podcast watchdog spine."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from departments.podcast.runtime import escalate_outbox
from departments.podcast.runtime import compare_charter
from departments.podcast.runtime import fingerprint_dedup
from departments.podcast.runtime import record
from departments.podcast.runtime import sense_estate


def _estate(tmp_path, timers):
    receipts = tmp_path / "receipts"
    logs = tmp_path / "logs"
    receipts.mkdir()
    logs.mkdir()
    return {
        "systemd_user_timers": timers,
        "receipts_dir": str(receipts),
        "logs_dir": str(logs),
        "channels": [],
        "vps": {"host": "test.invalid", "services": []},
    }


def _healthy_provider(item, _context):
    return {
        "sensor": item["kind"],
        "status": "ok",
        "evidence": f"fixture://{item['name']}",
        "detail": "fixture healthy",
        "metrics": {},
    }


def _candidate(ts="2026-07-22T12:00:00+00:00"):
    return {
        "ts": ts,
        "sensor": "receipt",
        "subject": "podcast-loop-health",
        "failure_class": "receipt_stale",
        "severity": "high",
        "setpoint": "receipt age <= 1560 minutes",
        "observed": 1600,
        "evidence": ["fixture://stale-receipt"],
        "one_question": "What blocked a fresh receipt?",
    }


def test_missing_fake_provider_output_emits_unknown_and_drop_fails_coverage(tmp_path):
    estate = _estate(
        tmp_path,
        [
            {"name": "unit-present", "expected_cadence": "daily", "stale_after_minutes": 1560},
            {"name": "unit-omitted", "expected_cadence": "daily", "stale_after_minutes": 1560},
        ],
    )

    def provider(item, context):
        if item["name"] == "unit-omitted":
            return None
        return _healthy_provider(item, context)

    observations = sense_estate.collect_observations(estate, provider=provider)
    omitted = next(row for row in observations if row["subject"] == "unit-omitted")
    assert omitted["status"] == "unknown"
    assert "provider returned no observation" in omitted["detail"]
    assert {row["subject"] for row in observations} == {"unit-present", "unit-omitted"}

    with pytest.raises(sense_estate.CoverageError, match="unit-omitted"):
        sense_estate.assert_inventory_coverage(estate, observations[:-1])


def test_stale_daily_receipt_produces_fail_observation(tmp_path):
    estate = _estate(
        tmp_path,
        [
            {
                "name": "podcast-loop-health",
                "expected_cadence": "daily",
                "stale_after_minutes": 1560,
            }
        ],
    )
    receipt = tmp_path / "receipts" / "podcast-loop-health.json"
    receipt.write_text("{}\n", encoding="utf-8")
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    stale = now - timedelta(hours=27)
    os.utime(receipt, (stale.timestamp(), stale.timestamp()))

    def healthy_systemctl(_unit):
        return {
            "ActiveState": "active",
            "SubState": "waiting",
            "Result": "success",
            "ExecMainStatus": "0",
        }

    observations = sense_estate.collect_observations(
        estate,
        now=now,
        systemctl_runner=healthy_systemctl,
        estate_path=tmp_path / "estate.json",
    )
    assert len(observations) == 1
    assert observations[0]["sensor"] == "receipt"
    assert observations[0]["status"] == "fail"
    assert observations[0]["metrics"]["receipt_age_minutes"] == 1620.0


def test_compare_uses_daily_limit_from_charter_not_inventory_value():
    charter = {
        "setpoints": {
            "operational": {"metric": "escalation_pings_per_day", "target": 5},
            "outcome": {
                "metric": "silent_failure_detection_latency_minutes",
                "target": 30,
            },
            "outcome_additional": [
                {"metric": "detection_latency_daily_loops_hours", "target": 25}
            ],
        }
    }
    observation = {
        "ts": "2026-07-22T12:00:00+00:00",
        "sensor": "receipt",
        "subject": "podcast-loop-health",
        "status": "ok",
        "evidence": "fixture://receipt",
        "detail": "fixture",
        "metrics": {
            "expected_cadence": "daily",
            "stale_after_minutes": 9999,
            "receipt_age_minutes": 1501,
        },
    }

    candidates = compare_charter.compare_observations([observation], charter)
    assert len(candidates) == 1
    assert candidates[0]["failure_class"] == "receipt_stale"
    assert candidates[0]["setpoint"] == "receipt age <= 1500 minutes"


def test_same_open_fingerprint_twice_is_one_incident_and_one_outbox_row(tmp_path):
    incidents, stats = fingerprint_dedup.merge_candidates([_candidate(), _candidate()])
    assert stats == {"new": 1, "deduplicated": 1, "department_defects": 0}
    assert len(incidents) == 1
    incident = next(iter(incidents.values()))
    assert incident["count"] == 2
    assert incident["state"] == "open"

    incidents_path = tmp_path / "incidents.json"
    record.atomic_write_json(incidents_path, incidents)
    outbox = tmp_path / "decisions_outbox.jsonl"
    first = escalate_outbox.escalate_new_incidents(incidents_path, outbox, shadow=True)
    second = escalate_outbox.escalate_new_incidents(incidents_path, outbox, shadow=True)

    assert first["outbox_rows"] == 1
    assert second["outbox_rows"] == 0
    rows = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["context"]["fingerprint"] == incident["fingerprint"]


def test_resolved_fingerprint_recurring_is_department_defect():
    incidents, _ = fingerprint_dedup.merge_candidates([_candidate()])
    key = next(iter(incidents))
    incidents[key]["state"] = "resolved"
    incidents[key]["escalated"] = True

    recurring, stats = fingerprint_dedup.merge_candidates(
        [_candidate("2026-07-23T12:00:00+00:00")], incidents
    )
    assert stats["department_defects"] == 1
    assert recurring[key]["state"] == "department_defect"
    assert recurring[key]["count"] == 2
    assert "resolved fingerprint recurred" in recurring[key]["one_question"]


def test_record_refuses_epoch_that_does_not_increase(tmp_path):
    first = record.write_record(tmp_path, "sense_estate", {"count": 1}, intended_epoch=0)
    assert first["epoch"] == 0
    with pytest.raises(record.EpochError, match="refusing epoch 0"):
        record.write_record(tmp_path, "compare_charter", {"count": 1}, intended_epoch=0)
    state = json.loads((tmp_path / "STATE.json").read_text(encoding="utf-8"))
    assert state["epoch"] == 0


def test_shadow_escalation_has_local_outbox_and_zero_external_effects(tmp_path):
    incidents, _ = fingerprint_dedup.merge_candidates([_candidate()])
    incidents_path = tmp_path / "incidents.json"
    outbox_path = tmp_path / "decisions_outbox.jsonl"
    record.atomic_write_json(incidents_path, incidents)

    result = escalate_outbox.escalate_new_incidents(
        incidents_path, outbox_path, shadow=True
    )

    assert result == {
        "outbox_rows": 1,
        "delivered_count": 0,
        "external_actions_taken": [],
        "shadow": True,
    }
    assert outbox_path.exists()
    assert {path.name for path in tmp_path.iterdir()} == {
        "incidents.json",
        "decisions_outbox.jsonl",
    }
