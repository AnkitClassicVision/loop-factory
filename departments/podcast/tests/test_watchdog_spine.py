"""Negative and contract tests for the podcast watchdog spine."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from departments.podcast.runtime import escalate_outbox
from departments.podcast.runtime import compare_charter
from departments.podcast.runtime import fingerprint_dedup
from departments.podcast.runtime import record
from departments.podcast.runtime import sense_estate


POISONED_FIXTURES = Path(__file__).with_name("fixtures") / "poisoned"


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


def _observation(status="ok", ts="2026-07-22T12:00:00+00:00"):
    return {
        "ts": ts,
        "sensor": "receipt",
        "subject": "podcast-loop-health",
        "status": status,
    }


def _healthy_systemctl(_unit):
    return {
        "ActiveState": "active",
        "SubState": "waiting",
        "Result": "success",
        "ExecMainStatus": "0",
    }


def _charter():
    return {
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


def test_empty_estate_inventory_exits_nonzero_with_clear_error(
    tmp_path, monkeypatch, capsys
):
    estate_path = tmp_path / "estate.json"
    estate_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sense_estate.py",
            "--estate",
            str(estate_path),
            "--state-dir",
            str(tmp_path / "state"),
            "--shadow",
        ],
    )

    with pytest.raises(SystemExit) as raised:
        sense_estate.main()

    assert raised.value.code != 0
    assert "estate inventory contains no units" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("content", "error_text"),
    [(None, "unreadable"), ("{", "malformed")],
)
def test_unreadable_and_malformed_estate_inventory_fail_closed(
    tmp_path, content, error_text
):
    estate_path = tmp_path / "estate.json"
    if content is not None:
        estate_path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=error_text):
        sense_estate.load_estate(estate_path)


def test_stale_daily_receipt_produces_fail_observation(tmp_path):
    poisoned = json.loads(
        (POISONED_FIXTURES / "stale_receipt.json").read_text(encoding="utf-8")
    )
    estate = _estate(
        tmp_path,
        [
            {
                "name": "podcast-loop-health",
                "expected_cadence": "daily",
                "stale_after_minutes": 1560,
                "receipt_glob": "health-*.md",
            }
        ],
    )
    receipt = tmp_path / "receipts" / "health-2026-07-22.md"
    receipt.write_text("{}\n", encoding="utf-8")
    now = datetime.fromisoformat(poisoned["now"])
    stale = datetime.fromisoformat(poisoned["receipt"]["ts"])
    os.utime(receipt, (stale.timestamp(), stale.timestamp()))

    observations = sense_estate.collect_observations(
        estate,
        now=now,
        systemctl_runner=_healthy_systemctl,
        estate_path=tmp_path / "estate.json",
    )
    assert len(observations) == 1
    assert observations[0]["sensor"] == "receipt"
    assert observations[0]["status"] == "fail"
    assert observations[0]["metrics"]["receipt_age_minutes"] == 1620.0


def test_log_read_error_is_unknown_with_exception_detail(tmp_path, monkeypatch):
    estate = _estate(
        tmp_path,
        [
            {
                "name": "podcast-prep-sweep",
                "expected_cadence": "15min",
                "stale_after_minutes": 30,
                "log_glob": "prep-sweep-*.log",
            }
        ],
    )
    log_path = tmp_path / "logs" / "prep-sweep-current.log"
    log_path.write_text("healthy\n", encoding="utf-8")
    real_open = Path.open

    def permission_lost(path, *args, **kwargs):
        if path == log_path:
            raise PermissionError("fixture permission loss")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", permission_lost)
    observation = sense_estate.collect_observations(
        estate, systemctl_runner=_healthy_systemctl
    )[0]

    assert observation["sensor"] == "log"
    assert observation["status"] == "unknown"
    assert "fixture permission loss" in observation["detail"]
    assert observation["metrics"]["log_read_errors"] == 1


def test_hollow_receipt_fails_as_receipt_hollow(tmp_path):
    estate = _estate(
        tmp_path,
        [
            {
                "name": "podcast-loop-health",
                "expected_cadence": "daily",
                "stale_after_minutes": 1560,
                "receipt_glob": "health-*.md",
            }
        ],
    )
    receipt = tmp_path / "receipts" / "health-2026-07-22.md"
    receipt.write_text(" \n\t", encoding="utf-8")

    observation = sense_estate.collect_observations(
        estate, systemctl_runner=_healthy_systemctl
    )[0]
    candidate = compare_charter.compare_observations([observation], _charter())[0]

    # forged_receipt.json targets kernel-signed receipts and is out of scope for
    # these Markdown checks; tests/test_kernel_receipts.py consumes signature
    # forgeries through the kernel verifier.
    assert observation["status"] == "fail"
    assert observation["metrics"]["failure_hint"] == "receipt_hollow"
    assert "receipt_hollow" in observation["detail"]
    assert candidate["failure_class"] == "receipt_hollow"
    assert candidate["severity"] == "high"


def test_shadow_channel_is_unknown_until_reachability_is_checked(tmp_path):
    estate = {
        "systemd_user_timers": [],
        "channels": [
            {
                "name": "telegram-escalation",
                "via": "telegram",
                "shadow_check": "config_presence",
            }
        ],
        "vps": {"host": "test.invalid", "services": []},
    }

    observation = sense_estate.collect_observations(
        estate, estate_path=tmp_path / "estate.json", shadow=True
    )[0]

    assert observation["status"] == "unknown"
    assert observation["detail"] == "config present; reachability unchecked in shadow"
    assert observation["metrics"]["reachability_checked"] is False


def test_receipt_glob_matches_short_loop_receipt_name(tmp_path):
    estate = _estate(
        tmp_path,
        [
            {
                "name": "podcast-loop-health",
                "expected_cadence": "daily",
                "stale_after_minutes": 1560,
                "receipt_glob": "health-*.md",
            }
        ],
    )
    receipt = tmp_path / "receipts" / "health-2026-07-22.md"
    receipt.write_text("healthy\n", encoding="utf-8")
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    os.utime(receipt, (now.timestamp(), now.timestamp()))

    observations = sense_estate.collect_observations(
        estate, now=now, systemctl_runner=_healthy_systemctl
    )

    assert observations[0]["status"] == "ok"
    assert observations[0]["metrics"]["receipt_path"] == str(receipt)


def test_no_matching_artifact_is_unknown_not_observed_stale_failure(tmp_path):
    estate = _estate(
        tmp_path,
        [
            {
                "name": "podcast-loop-health",
                "expected_cadence": "daily",
                "stale_after_minutes": 1560,
                "receipt_glob": "health-*.md",
            }
        ],
    )

    observation = sense_estate.collect_observations(
        estate, systemctl_runner=_healthy_systemctl
    )[0]
    candidate = compare_charter.compare_observations([observation], _charter())[0]

    assert observation["status"] == "unknown"
    assert "no receipt matched configured evidence" in observation["detail"]
    assert candidate["failure_class"] == "receipt_unknown"
    assert candidate["severity"] == "med"


def test_service_failure_overrides_successful_timer(tmp_path):
    estate = _estate(
        tmp_path,
        [
            {
                "name": "podcast-prep-sweep",
                "expected_cadence": "15min",
                "stale_after_minutes": 30,
                "evidence": "timer_only",
            }
        ],
    )
    probed = []

    def systemctl_runner(unit):
        probed.append(unit)
        state = _healthy_systemctl(unit)
        if unit.endswith(".service"):
            state = {**state, "ActiveState": "failed", "Result": "exit-code", "ExecMainStatus": "1"}
        return state

    observation = sense_estate.collect_observations(
        estate, systemctl_runner=systemctl_runner
    )[0]

    assert probed == ["podcast-prep-sweep.timer", "podcast-prep-sweep.service"]
    assert observation["sensor"] == "timer"
    assert observation["status"] == "fail"
    assert "timer_failed" in observation["detail"]
    assert observation["metrics"]["service_result"] == "exit-code"
    candidate = compare_charter.compare_observations([observation], _charter())[0]
    assert candidate["failure_class"] == "timer_failed"
    assert candidate["severity"] == "high"


def test_sweeper_idle_day_does_not_produce_receipt_stale(tmp_path):
    estate = _estate(
        tmp_path,
        [
            {
                "name": "obe-scheduled-intent-sweeper",
                "expected_cadence": "daily",
                "stale_after_minutes": 60,
                "evidence": "timer_only",
            }
        ],
    )
    ledger = tmp_path / "logs" / "scheduled-intent-sweeper-ledger.jsonl"
    ledger.write_text("{}\n", encoding="utf-8")
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    modified = now - timedelta(days=7)
    os.utime(ledger, (modified.timestamp(), modified.timestamp()))

    observation = sense_estate.collect_observations(
        estate, now=now, systemctl_runner=_healthy_systemctl
    )[0]

    assert observation["status"] == "ok"
    assert "receipt_age_minutes" not in observation["metrics"]
    assert compare_charter.compare_observations([observation], _charter()) == []


def test_timer_only_skips_artifact_freshness(tmp_path):
    estate = _estate(
        tmp_path,
        [
            {
                "name": "podcast-context-watch",
                "expected_cadence": "hourly",
                "stale_after_minutes": 120,
                "evidence": "timer_only",
            }
        ],
    )

    observation = sense_estate.collect_observations(
        estate, systemctl_runner=_healthy_systemctl
    )[0]

    assert observation["status"] == "ok"
    assert "receipt_age_minutes" not in observation["metrics"]


def test_missing_evidence_spec_is_unknown_and_compares_as_med(tmp_path):
    estate = _estate(
        tmp_path,
        [{"name": "unit-with-gap", "expected_cadence": "daily", "stale_after_minutes": 60}],
    )

    observation = sense_estate.collect_observations(
        estate, systemctl_runner=_healthy_systemctl
    )[0]
    candidates = compare_charter.compare_observations([observation], _charter())

    assert observation["status"] == "unknown"
    assert "missing evidence spec" in observation["detail"]
    assert candidates[0]["failure_class"] == "timer_unknown"
    assert candidates[0]["severity"] == "med"


@pytest.mark.parametrize("sensor", ["channel", "vps"])
def test_unknown_channel_and_vps_severity_is_med(sensor):
    observation = {
        "ts": "2026-07-22T12:00:00+00:00",
        "sensor": sensor,
        "subject": f"fixture-{sensor}",
        "status": "unknown",
        "evidence": "fixture://unknown",
        "detail": "probe unavailable",
        "metrics": {},
    }

    candidates = compare_charter.compare_observations([observation], _charter())

    assert candidates[0]["severity"] == "med"


def test_default_estate_has_one_explicit_evidence_spec_per_timer():
    estate = sense_estate.load_estate()
    timers = {row["name"]: row for row in estate["systemd_user_timers"]}
    expected = {
        "podcast-loop-health": ("receipt_glob", "health-*.md"),
        "podcast-loop-guest-acquisition": ("receipt_glob", "guest-acquisition-*.md"),
        "podcast-loop-referral-flywheel": ("receipt_glob", "referral-flywheel-*.md"),
        "podcast-loop-booking-readiness": ("receipt_glob", "booking-readiness-*.md"),
        "podcast-loop-production-publish": ("receipt_glob", "production-publish-*.md"),
        "podcast-loop-sales-handoff": ("receipt_glob", "sales-handoff-*.md"),
        "podcast-loop-proof-improvement": ("receipt_glob", "proof-improvement-*.md"),
        "podcast-prep-sweep": ("log_glob", "prep-sweep-*.log"),
        "podcast-context-watch": ("evidence", "timer_only"),
        "obe-scheduled-intent-sweeper": ("evidence", "timer_only"),
        "obe-draft-bridge": ("ledger_path", "send-approval-bridge-ledger.jsonl"),
        "obe-approved-send-executor": (
            "ledger_path",
            "send-approval-bridge-ledger.jsonl",
        ),
    }

    assert set(timers) == set(expected)
    for name, (key, value) in expected.items():
        configured = [
            field
            for field in ("receipt_glob", "log_glob", "ledger_path", "evidence")
            if field in timers[name]
        ]
        assert configured == [key]
        assert timers[name][key] == value


def test_compare_uses_daily_limit_from_charter_not_inventory_value():
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

    candidates = compare_charter.compare_observations([observation], _charter())
    assert len(candidates) == 1
    assert candidates[0]["failure_class"] == "receipt_stale"
    assert candidates[0]["setpoint"] == "receipt age <= 1500 minutes"


def test_compare_keeps_failure_from_other_sensor_with_same_subject():
    observations = [
        {
            "ts": "2026-07-22T12:00:00+00:00",
            "sensor": "publishday",
            "subject": "episode-7",
            "status": "fail",
            "evidence": "fixture://missing-publish",
            "detail": "publish evidence missing",
            "metrics": {},
        },
        {
            "ts": "2026-07-22T12:05:00+00:00",
            "sensor": "manifest",
            "subject": "episode-7",
            "status": "ok",
            "evidence": "fixture://complete-manifest",
            "detail": "manifest complete",
            "metrics": {},
        },
    ]

    candidates = compare_charter.compare_observations(observations, _charter())

    assert len(candidates) == 1
    assert candidates[0]["sensor"] == "publishday"
    assert candidates[0]["failure_class"] == "publish_missing"


def test_poisoned_unit_missing_fails_sense_inventory_coverage():
    poisoned = json.loads(
        (POISONED_FIXTURES / "unit_missing.json").read_text(encoding="utf-8")
    )
    estate = {
        "systemd_user_timers": [
            {"name": name} for name in poisoned["inventory"]
        ],
        "channels": [],
        "vps": {"services": []},
    }

    with pytest.raises(sense_estate.CoverageError, match="referral-flywheel.timer"):
        sense_estate.assert_inventory_coverage(estate, poisoned["observations"])


def test_missing_observations_emits_evidence_missing_candidate(tmp_path):
    missing_path = tmp_path / "observations.jsonl"

    candidates = compare_charter.run_compare(tmp_path)

    assert len(candidates) == 1
    assert candidates[0]["failure_class"] == "evidence_missing"
    assert candidates[0]["severity"] == "high"
    assert candidates[0]["evidence"] == [str(missing_path)]
    assert "missing" in candidates[0]["observed"]


def test_unreadable_observations_emits_evidence_missing_candidate(
    tmp_path, monkeypatch
):
    observations_path = tmp_path / "observations.jsonl"
    observations_path.write_text('{"sensor":"timer"}\n', encoding="utf-8")
    real_read_text = Path.read_text

    def permission_lost(path, *args, **kwargs):
        if path == observations_path:
            raise PermissionError("fixture observations permission loss")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", permission_lost)
    candidates = compare_charter.run_compare(tmp_path)

    assert candidates[0]["failure_class"] == "evidence_missing"
    assert candidates[0]["severity"] == "high"
    assert candidates[0]["evidence"] == [str(observations_path)]
    assert "fixture observations permission loss" in candidates[0]["observed"]


@pytest.mark.parametrize(
    ("sensor", "status", "failure_class", "severity"),
    [
        ("pipeline", "fail", "pipeline_below_target", "high"),
        ("pipeline", "warn", "pipeline_warn", "med"),
        ("pipeline", "unknown", "pipeline_unknown", "med"),
        ("publishday", "fail", "publish_missing", "high"),
        ("publishday", "unknown", "publish_unknown", "med"),
        ("manifest", "fail", "manifest_incomplete", "high"),
        ("manifest", "warn", "manifest_gap", "med"),
        ("manifest", "unknown", "manifest_unknown", "med"),
    ],
)
def test_independent_sensor_transitions_are_enumerated(
    sensor, status, failure_class, severity
):
    observation = {
        "ts": "2026-07-22T12:00:00+00:00",
        "sensor": sensor,
        "subject": f"fixture-{sensor}",
        "status": status,
        "evidence": "fixture://sensor",
        "detail": "fixture",
        "metrics": {},
    }

    candidate = compare_charter.compare_observations([observation], _charter())[0]

    assert candidate["failure_class"] == failure_class
    assert candidate["severity"] == severity


@pytest.mark.parametrize("sensor", ["pipeline", "publishday", "manifest"])
def test_independent_sensor_ok_has_no_candidate(sensor):
    observation = {
        "ts": "2026-07-22T12:00:00+00:00",
        "sensor": sensor,
        "subject": f"fixture-{sensor}",
        "status": "ok",
        "evidence": "fixture://sensor",
        "detail": "fixture",
        "metrics": {},
    }

    assert compare_charter.compare_observations([observation], _charter()) == []


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


def test_failure_class_changes_merge_into_one_stable_incident():
    unknown = {
        **_candidate("2026-07-22T12:00:00+00:00"),
        "sensor": "timer",
        "subject": "obe-scheduled-intent-sweeper",
        "failure_class": "timer_unknown",
        "severity": "med",
        "observed": {"Result": None},
        "evidence": ["fixture://confined"],
    }
    failed = {
        **unknown,
        "ts": "2026-07-22T12:05:00+00:00",
        "failure_class": "timer_failed",
        "severity": "high",
        "observed": {"Result": "exit-code"},
        "evidence": ["fixture://unconfined"],
    }

    incidents, stats = fingerprint_dedup.merge_candidates([unknown, failed])

    assert len(incidents) == 1
    incident = next(iter(incidents.values()))
    assert stats == {"new": 1, "deduplicated": 1, "department_defects": 0}
    assert incident["fingerprint"] == fingerprint_dedup.fingerprint(
        "timer", "obe-scheduled-intent-sweeper"
    )
    assert incident["failure_class"] == "timer_failed"
    assert incident["severity"] == "high"
    assert incident["observed"] == {"Result": "exit-code"}
    assert incident["evidence"] == ["fixture://confined", "fixture://unconfined"]


def test_old_fingerprint_variants_collapse_idempotently_on_load(tmp_path):
    sensor = "timer"
    subject = "obe-scheduled-intent-sweeper"
    older = {
        "fingerprint": hashlib.sha256(
            f"{sensor}|{subject}|timer_unknown".encode("utf-8")
        ).hexdigest()[:12],
        "failure_class": "timer_unknown",
        "first_seen": "2026-07-20T12:00:00+00:00",
        "last_seen": "2026-07-21T12:00:00+00:00",
        "state": "department_defect",
        "severity": "critical",
        "setpoint": "timer healthy",
        "observed": {"Result": None},
        "evidence": ["fixture://older"],
        "one_question": "Repair?",
        "count": 2,
        "escalated": True,
        "escalated_at": "2026-07-20T12:01:00+00:00",
    }
    newer = {
        **older,
        "fingerprint": hashlib.sha256(
            f"{sensor}|{subject}|timer_failed".encode("utf-8")
        ).hexdigest()[:12],
        "failure_class": "timer_failed",
        "first_seen": "2026-07-21T13:00:00+00:00",
        "last_seen": "2026-07-22T12:00:00+00:00",
        "state": "open",
        "severity": "high",
        "observed": {"Result": "exit-code"},
        "evidence": ["fixture://newer"],
        "count": 3,
        "escalated": False,
        "escalated_at": None,
    }
    path = tmp_path / "incidents.json"
    record.atomic_write_json(path, {older["fingerprint"]: older, newer["fingerprint"]: newer})

    first = fingerprint_dedup.load_incidents(path)
    after_first = path.read_bytes()
    second = fingerprint_dedup.load_incidents(path)

    assert first == second
    assert path.read_bytes() == after_first
    assert len(first) == 1
    incident = next(iter(first.values()))
    assert incident["fingerprint"] == fingerprint_dedup.fingerprint(sensor, subject)
    assert incident["sensor"] == sensor
    assert incident["subject"] == subject
    assert incident["first_seen"] == "2026-07-20T12:00:00+00:00"
    assert incident["last_seen"] == "2026-07-22T12:00:00+00:00"
    assert incident["failure_class"] == "timer_failed"
    assert incident["severity"] == "critical"
    assert incident["state"] == "department_defect"
    assert incident["evidence"] == ["fixture://older", "fixture://newer"]
    assert incident["count"] == 5
    assert incident["escalated"] is True


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


def test_three_healthy_cycles_resolve_open_incident_and_preserve_escalated():
    incidents, _ = fingerprint_dedup.merge_candidates([_candidate()])
    key = next(iter(incidents))
    incidents[key]["escalated"] = True
    incidents[key]["escalated_at"] = "2026-07-22T12:01:00+00:00"

    for day in range(23, 26):
        ts = f"2026-07-{day}T12:00:00+00:00"
        incidents, _ = fingerprint_dedup.merge_candidates(
            [], incidents, observations=[_observation(ts=ts)]
        )

    assert incidents[key]["state"] == "resolved"
    assert incidents[key]["consecutive_healthy"] == 3
    assert incidents[key]["resolved_at"] == "2026-07-25T12:00:00+00:00"
    assert incidents[key]["resolution"] == "observed_healthy_3_cycles"
    assert incidents[key]["escalated"] is True
    assert incidents[key]["escalated_at"] == "2026-07-22T12:01:00+00:00"


def test_candidate_resets_healthy_counter_before_resolution():
    incidents, _ = fingerprint_dedup.merge_candidates([_candidate()])
    key = next(iter(incidents))
    incidents, _ = fingerprint_dedup.merge_candidates(
        [], incidents, observations=[_observation(ts="2026-07-23T12:00:00+00:00")]
    )
    incidents, _ = fingerprint_dedup.merge_candidates(
        [], incidents, observations=[_observation(ts="2026-07-23T12:00:00+00:00")]
    )
    assert incidents[key]["consecutive_healthy"] == 1
    incidents, _ = fingerprint_dedup.merge_candidates(
        [_candidate("2026-07-24T12:00:00+00:00")],
        incidents,
        observations=[_observation("fail", "2026-07-24T12:00:00+00:00")],
    )
    for day in (25, 26):
        incidents, _ = fingerprint_dedup.merge_candidates(
            [], incidents, observations=[_observation(ts=f"2026-07-{day}T12:00:00+00:00")]
        )
    assert incidents[key]["state"] == "open"
    assert incidents[key]["consecutive_healthy"] == 2

    incidents, _ = fingerprint_dedup.merge_candidates(
        [], incidents, observations=[_observation(ts="2026-07-27T12:00:00+00:00")]
    )
    assert incidents[key]["state"] == "resolved"


def test_auto_resolved_fingerprint_recurring_becomes_department_defect():
    incidents, _ = fingerprint_dedup.merge_candidates([_candidate()])
    key = next(iter(incidents))
    for day in range(23, 26):
        incidents, _ = fingerprint_dedup.merge_candidates(
            [], incidents, observations=[_observation(ts=f"2026-07-{day}T12:00:00+00:00")]
        )

    incidents, stats = fingerprint_dedup.merge_candidates(
        [_candidate("2026-07-26T12:00:00+00:00")], incidents
    )
    assert stats["department_defects"] == 1
    assert incidents[key]["state"] == "department_defect"
    assert incidents[key]["consecutive_healthy"] == 0


def test_subject_without_current_observation_does_not_accrue_health():
    incidents, _ = fingerprint_dedup.merge_candidates([_candidate()])
    key = next(iter(incidents))
    other = {**_observation(), "sensor": "timer", "subject": "another-loop"}

    incidents, _ = fingerprint_dedup.merge_candidates([], incidents, observations=[other])

    assert incidents[key]["state"] == "open"
    assert incidents[key]["consecutive_healthy"] == 0


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
        ".records.lock",
        "incidents.json",
        "decisions_outbox.jsonl",
    }
