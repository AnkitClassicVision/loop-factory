"""Regression tests for manager record locking and escalation deduplication."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-02T12:00:00+00:00"


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load("manager_hardening_runtime", "factory/manager.py")
LOCK = _load("manager_hardening_lockutil", "factory/lockutil.py")


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _safe_sense(state_dir, now=None, **paths):
    return {
        "now": NOW,
        "week_touches": 10,
        "conversions": 1,
        "held_mismatch": 0,
        "carried_forward": 0,
        "budget_used": {},
        "last_run_ok": True,
    }


def _run_breach(tmp_path, delivered, *, code="run_failed", subject="worker"):
    action = {
        "act": "escalate",
        "reason": code,
        "finding_code": code,
        "subject": subject,
        "detail": "persistent breach",
    }
    finding = {
        "code": code,
        "severity": "breach",
        "detail": "persistent breach",
    }
    return M.act(
        [action, {"act": "record", "reason": "cadence"}],
        sensed={},
        findings=[finding],
        escalate_fn=lambda issue, context=None: delivered.append(context),
        state_path=tmp_path / "STATE.json",
        heartbeat_path=tmp_path / "heartbeats.jsonl",
        run_db_path=tmp_path / "runs.jsonl",
        department="podcast",
        escalation_owner="test-owner",
        now=NOW,
    )


def test_concurrent_manager_writes_from_two_threads_keep_all_100_rows(tmp_path):
    def run_fifty_cycles() -> None:
        for _ in range(50):
            M.run_manager_cycle(
                state_dir=tmp_path,
                department="podcast",
                now=NOW,
                sense_fn=_safe_sense,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_fifty_cycles) for _ in range(2)]
        for future in futures:
            future.result()

    runs = _jsonl(tmp_path / "runs.jsonl")
    heartbeats = _jsonl(tmp_path / "heartbeats.jsonl")
    assert len(runs) == 100
    assert len(heartbeats) == 100
    assert {row["epoch"] for row in runs} == set(range(100))
    assert json.loads((tmp_path / "STATE.json").read_text(encoding="utf-8"))["epoch"] == 99


def test_state_json_stays_readable_if_replace_crashes_after_temp_write(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "STATE.json"
    old_state = {"department": "podcast", "epoch": 7, "sentinel": "old"}
    state_path.write_text(json.dumps(old_state) + "\n", encoding="utf-8")

    def crash_before_replace(source, destination):
        raise RuntimeError("simulated crash before replace")

    monkeypatch.setattr(M.os, "replace", crash_before_replace)
    with pytest.raises(RuntimeError, match="simulated crash"):
        M.act(
            [{"act": "record", "reason": "cadence"}],
            state_path=state_path,
            heartbeat_path=tmp_path / "heartbeats.jsonl",
            run_db_path=tmp_path / "runs.jsonl",
            department="podcast",
            now=NOW,
        )

    assert json.loads(state_path.read_text(encoding="utf-8")) == old_state


def test_records_lock_file_is_created_and_released(tmp_path):
    lock_path = tmp_path / ".records.lock"
    with LOCK.records_lock(tmp_path):
        assert lock_path.exists()
        with lock_path.open("a+", encoding="utf-8") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    with lock_path.open("a+", encoding="utf-8") as contender:
        fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(contender.fileno(), fcntl.LOCK_UN)


def test_same_breach_across_three_cycles_escalates_once(tmp_path):
    delivered = []
    reports = [_run_breach(tmp_path, delivered) for _ in range(3)]

    assert len(delivered) == 1
    assert [report["escalations"] for report in reports] == [1, 0, 0]
    assert len(_jsonl(tmp_path / "escalation_fingerprints.jsonl")) == 1


def test_new_distinct_breach_subject_still_escalates(tmp_path):
    delivered = []
    _run_breach(tmp_path, delivered, code="run_failed", subject="worker-a")
    _run_breach(tmp_path, delivered, code="run_failed", subject="worker-b")

    assert len(delivered) == 2
    assert delivered[0]["fingerprint"] != delivered[1]["fingerprint"]


def test_suppressed_duplicate_is_noted_in_cycle_record(tmp_path):
    delivered = []
    _run_breach(tmp_path, delivered)
    _run_breach(tmp_path, delivered)

    last_cycle = _jsonl(tmp_path / "runs.jsonl")[-1]
    assert last_cycle["notes"] == [
        {
            "code": "escalation_suppressed_duplicate",
            "fingerprint": delivered[0]["fingerprint"],
        }
    ]


def test_resolved_then_recurring_breach_escalates_again(tmp_path):
    delivered = []
    _run_breach(tmp_path, delivered)
    fingerprint = delivered[0]["fingerprint"]
    fingerprint_path = tmp_path / "escalation_fingerprints.jsonl"

    with LOCK.records_lock(tmp_path):
        with fingerprint_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "marker": "resolved",
                        "timestamp": NOW,
                    }
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())

    report = _run_breach(tmp_path, delivered)
    assert len(delivered) == 2
    assert report["escalations"] == 1
    assert [row["marker"] for row in _jsonl(fingerprint_path)] == [
        "delivered",
        "resolved",
        "delivered",
    ]


@pytest.mark.parametrize("poison", [-1, "not-a-number", float("nan"), float("inf")])
def test_compare_refuses_poisoned_count_instead_of_coercing_or_bypassing(poison):
    findings = M.compare(
        {"week_touches": poison, "conversions": 0, "held_mismatch": 0, "carried_forward": 0},
        M.DEFAULT_THRESHOLDS,
    )

    assert any(f["code"] == "count_invalid:week_touches" for f in findings)
    assert not any(f["code"] == "pace_ceiling_near" for f in findings)


@pytest.mark.parametrize("poison", [-1, "not-a-number", float("nan"), float("inf")])
def test_compare_refuses_poisoned_budget_instead_of_treating_it_as_safe(poison):
    findings = M.compare(
        {"week_touches": 0, "budget_used": {"dollars": poison}},
        {**M.DEFAULT_THRESHOLDS, "budget_ceilings": {"dollars": 40}},
    )

    assert any(f["code"] == "budget_invalid:dollars" for f in findings)
    assert not any(f["code"] == "budget_near:dollars" for f in findings)


def test_compare_refuses_poisoned_safety_threshold():
    findings = M.compare(
        {"week_touches": 0, "conversions": 0},
        {**M.DEFAULT_THRESHOLDS, "weekly_touch_ceiling": -1},
    )

    assert any(f["code"] == "threshold_invalid:weekly_touch_ceiling" for f in findings)


def test_manager_cycle_records_poisoned_count_as_a_breach(tmp_path):
    def poisoned_sense(state_dir, now=None, **paths):
        return {
            "week_touches": float("nan"),
            "conversions": 0,
            "held_mismatch": 0,
            "carried_forward": 0,
            "budget_used": {},
            "last_run_ok": True,
        }

    report = M.run_manager_cycle(tmp_path, sense_fn=poisoned_sense, now=NOW)

    assert any(f["code"] == "count_invalid:week_touches" for f in report["findings"])
    recorded = _jsonl(tmp_path / "runs.jsonl")[-1]
    assert "count_invalid:week_touches" in recorded["findings"]
