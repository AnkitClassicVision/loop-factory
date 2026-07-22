"""Tests for the factory-standard department manager loop (v1, shadow verb subset).

Pins the ratified §7 contract: Sense -> Compare -> Decide -> Act -> Record,
deterministic/model-free, shadow-mode Act limited to {escalate, daily_brief,
record, dispatch, bounded_retry}, and heal may NEVER touch an immutable safety
invariant. See docs/superpowers/specs/2026-07-21-department-factory-design.md §7
and the charter immutable_safety_invariants.
"""
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "factory"


def _load():
    spec = importlib.util.spec_from_file_location("manager", RUNTIME_DIR / "manager.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load()


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _iso(day):
    # deterministic timestamps: 2026-07-<day>T12:00:00+00:00
    return f"2026-07-{day:02d}T12:00:00+00:00"


NOW = _iso(21)


def _queue_row(status, day=21, **kw):
    row = {"contact_id": kw.get("contact_id", "1"), "status": status, "queued_at": _iso(day)}
    row.update(kw)
    return row


# --------------------------------------------------------------------------- #
# Sense
# --------------------------------------------------------------------------- #

def test_sense_reads_telemetry(tmp_path):
    approval = tmp_path / "approval_queue.jsonl"
    runs = tmp_path / "runs.jsonl"
    receipt = tmp_path / "sink_receipt.json"
    _write_jsonl(
        approval,
        [
            _queue_row("sent_shadow", day=20, contact_id="a"),
            _queue_row("sent_shadow", day=21, contact_id="b"),
            _queue_row("pending_approval", day=19, contact_id="c"),  # aged >1d
            _queue_row("held_recipient_mismatch", day=21, contact_id="d"),
            _queue_row("rejected", day=21, contact_id="e"),
        ],
    )
    _write_jsonl(runs, [{"status": "dispatched_shadow", "queued_at": _iso(21)}])
    receipt.write_text(json.dumps({"summary": {"carried_forward_over_1d": 1}}), encoding="utf-8")

    s = M.sense(
        state_dir=tmp_path,
        run_db_path=runs,
        approval_path=approval,
        receipt_path=receipt,
        now=NOW,
    )
    assert s["week_touches"] == 2          # two sent_shadow within the week
    assert s["pending"] == 1
    assert s["held_mismatch"] == 1
    assert s["rejected"] == 1
    assert s["carried_forward"] == 1
    assert s["last_run_ok"] is True


def test_sense_missing_files_is_safe(tmp_path):
    s = M.sense(state_dir=tmp_path, now=NOW)
    assert s["week_touches"] == 0
    assert s["pending"] == 0
    assert s["held_mismatch"] == 0
    assert s["last_run_at"] is None


# --------------------------------------------------------------------------- #
# Compare (deterministic thresholds)
# --------------------------------------------------------------------------- #

def test_compare_flags_recipient_mismatch_as_breach():
    findings = M.compare({"held_mismatch": 2, "week_touches": 5, "conversions": 0}, M.DEFAULT_THRESHOLDS)
    codes = {f["code"]: f for f in findings}
    assert "held_recipient_mismatch" in codes
    assert codes["held_recipient_mismatch"]["severity"] == "breach"


def test_compare_flags_backlog_aging_as_warn():
    findings = M.compare({"carried_forward": 3, "week_touches": 0}, M.DEFAULT_THRESHOLDS)
    codes = {f["code"]: f for f in findings}
    assert codes["backlog_aging"]["severity"] == "warn"
    assert codes["backlog_aging"]["observed"] == 3


def test_compare_flags_faux_work_breach():
    # touches over the floor with zero attributable conversion = gaming/faux-work
    findings = M.compare({"week_touches": 60, "conversions": 0}, M.DEFAULT_THRESHOLDS)
    codes = {f["code"] for f in findings}
    assert "faux_work" in codes


def test_compare_flags_pace_ceiling_near_breach():
    findings = M.compare({"week_touches": 290, "conversions": 5}, M.DEFAULT_THRESHOLDS)
    codes = {f["code"]: f for f in findings}
    assert codes["pace_ceiling_near"]["severity"] == "breach"


def test_compare_clean_state_has_no_breach():
    findings = M.compare(
        {"week_touches": 12, "conversions": 3, "held_mismatch": 0, "carried_forward": 0},
        M.DEFAULT_THRESHOLDS,
    )
    assert all(f["severity"] != "breach" for f in findings)


# --------------------------------------------------------------------------- #
# Decide (shadow verb subset + immutable-invariant guard)
# --------------------------------------------------------------------------- #

def test_decide_shadow_only_emits_whitelisted_acts():
    findings = [
        {"code": "held_recipient_mismatch", "severity": "breach", "detail": "x"},
        {"code": "backlog_aging", "severity": "warn", "detail": "y"},
    ]
    actions = M.decide(findings, autonomy_state="shadow")
    acts = {a["act"] for a in actions}
    assert acts <= M.SHADOW_ACTS
    assert "escalate" in acts        # the breach escalates
    assert "daily_brief" in acts
    assert "record" in acts


def test_decide_downgrades_gated_live_verbs_in_shadow():
    # a synthetic action that would be legal at gated-live (throttle_park) must be
    # redirected to an escalation while the department is in shadow.
    gated = [{"act": "throttle_park", "reason": "budget", "finding_code": "budget_near"}]
    out = M.gate_actions(gated, autonomy_state="shadow")
    assert all(a["act"] in M.SHADOW_ACTS for a in out)
    assert any(a["act"] == "escalate" for a in out)


def test_decide_cannot_touch_immutable_invariant():
    # heal_may_not_modify: the manager must refuse any action that mutates a floor.
    hostile = [{"act": "escalate", "target": "send_authorization", "reason": "heal"}]
    with pytest.raises(M.ImmutableInvariantError):
        M.gate_actions(hostile, autonomy_state="shadow", immutable_invariants=M.IMMUTABLE_INVARIANTS)


def test_clean_state_escalates_nothing():
    findings = [{"code": "pace_under", "severity": "info", "detail": "z"}]
    actions = M.decide(findings, autonomy_state="shadow")
    assert not any(a["act"] == "escalate" for a in actions)
    assert any(a["act"] == "daily_brief" for a in actions)


# --------------------------------------------------------------------------- #
# Act + Record (order, atomic STATE, monotonic epoch, heartbeat, brief)
# --------------------------------------------------------------------------- #

def test_act_escalates_breach_writes_state_heartbeat_brief(tmp_path):
    escalations = []
    findings = [{"code": "held_recipient_mismatch", "severity": "breach", "detail": "wrong inbox"}]
    actions = M.decide(findings, autonomy_state="shadow")
    report = M.act(
        actions,
        sensed={"week_touches": 1},
        findings=findings,
        escalate_fn=lambda issue, context=None: escalations.append(issue),
        state_path=tmp_path / "STATE.json",
        heartbeat_path=tmp_path / "heartbeats.jsonl",
        brief_path=tmp_path / "brief.md",
        run_db_path=tmp_path / "runs.jsonl",
        now=NOW,
    )
    assert len(escalations) == 1
    assert (tmp_path / "STATE.json").exists()
    assert (tmp_path / "brief.md").exists()
    hb = (tmp_path / "heartbeats.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(hb) == 1
    # record order: a manager tick card is appended to runs.db
    assert (tmp_path / "runs.jsonl").exists()
    assert report["epoch"] == 0


def test_state_epoch_is_monotonic_across_cycles(tmp_path):
    state_path = tmp_path / "STATE.json"
    common = dict(
        findings=[],
        escalate_fn=lambda issue, context=None: None,
        heartbeat_path=tmp_path / "hb.jsonl",
        brief_path=tmp_path / "brief.md",
        run_db_path=tmp_path / "runs.jsonl",
        now=NOW,
    )
    r0 = M.act(M.decide([], "shadow"), sensed={}, state_path=state_path, **common)
    r1 = M.act(M.decide([], "shadow"), sensed={}, state_path=state_path, **common)
    assert r0["epoch"] == 0
    assert r1["epoch"] == 1
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["epoch"] == 1


# --------------------------------------------------------------------------- #
# End to end
# --------------------------------------------------------------------------- #

def test_run_manager_cycle_end_to_end(tmp_path):
    approval = tmp_path / "approval_queue.jsonl"
    _write_jsonl(
        approval,
        [
            _queue_row("sent_shadow", day=21, contact_id="a"),
            _queue_row("held_recipient_mismatch", day=21, contact_id="d"),
            _queue_row("pending_approval", day=18, contact_id="c"),
        ],
    )
    escalations = []
    report = M.run_manager_cycle(
        state_dir=tmp_path,
        autonomy_state="shadow",
        approval_path=approval,
        escalate_fn=lambda issue, context=None: escalations.append(issue),
        now=NOW,
    )
    assert report["ok"] is True
    # the mismatch is a breach -> at least one escalation reached Hermes
    assert len(escalations) >= 1
    assert (tmp_path / "STATE.json").exists()
    assert (tmp_path / "MANAGER_BRIEF.md").exists()
    codes = {f["code"] for f in report["findings"]}
    assert "held_recipient_mismatch" in codes
