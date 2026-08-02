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
# Drift sensor (hard rule 4: process change = map change + QA — on cadence)
# --------------------------------------------------------------------------- #

def _load_release():
    spec = importlib.util.spec_from_file_location("release", RUNTIME_DIR / "release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REL = _load_release()


def _pinned_dept(tmp_path):
    """A minimal department with one runtime artifact, pinned and flipped."""
    dept = tmp_path / "dept"
    (dept / "runtime").mkdir(parents=True)
    (dept / "state").mkdir()
    (dept / "runtime" / "node.py").write_text("def node(): return 1\n", encoding="utf-8")
    releases = dept / "releases"
    h = REL.pin_release(dept, releases, source_ref="testsha")
    REL.flip_current(releases, h)
    return dept


def test_cycle_with_clean_release_has_no_drift_finding(tmp_path):
    dept = _pinned_dept(tmp_path)
    report = M.run_manager_cycle(state_dir=dept / "state", dept_dir=dept, now=NOW)
    codes = {f["code"] for f in report["findings"]}
    assert report["sensed"]["drift_checked"] is True
    assert report["sensed"]["drift_ok"] is True
    assert not codes & {"release_drift", "release_unpinned", "drift_check_failed"}


def test_drifted_release_is_breach_and_escalates(tmp_path):
    dept = _pinned_dept(tmp_path)
    # edit the process WITHOUT re-pinning: exactly the hard-rule-4 violation
    (dept / "runtime" / "node.py").write_text("def node(): return 2\n", encoding="utf-8")
    escalations = []
    report = M.run_manager_cycle(
        state_dir=dept / "state", dept_dir=dept, now=NOW,
        escalate_fn=lambda issue, context=None: escalations.append(issue),
    )
    codes = {f["code"]: f for f in report["findings"]}
    assert codes["release_drift"]["severity"] == "breach"
    assert codes["release_drift"]["observed"] == 1
    assert any("release_drift" in e for e in escalations)
    # the alarm lands in the durable records: STATE + heartbeat
    state = json.loads((dept / "state" / "STATE.json").read_text(encoding="utf-8"))
    assert "release_drift" in [f["code"] for f in state["open_findings"]]
    assert (dept / "state" / "heartbeats.jsonl").exists()


def test_drift_breach_names_mismatched_artifact_and_runbook(tmp_path):
    dept = _pinned_dept(tmp_path)
    (dept / "runtime" / "node.py").write_text("def node(): return 3\n", encoding="utf-8")
    report = M.run_manager_cycle(state_dir=dept / "state", dept_dir=dept, now=NOW)
    detail = next(f for f in report["findings"] if f["code"] == "release_drift")["detail"]
    assert "runtime/node.py" in detail
    assert "process-change-qa" in detail


def test_releases_dir_without_pin_is_breach(tmp_path):
    dept = tmp_path / "dept"
    (dept / "runtime").mkdir(parents=True)
    (dept / "state").mkdir()
    (dept / "releases").mkdir()
    report = M.run_manager_cycle(state_dir=dept / "state", dept_dir=dept, now=NOW)
    codes = {f["code"]: f for f in report["findings"]}
    assert codes["release_unpinned"]["severity"] == "breach"


def test_no_releases_dir_is_visible_warn_not_breach(tmp_path):
    # pre-F4 department: drift is unverifiable — surface it, don't alarm
    dept = tmp_path / "dept"
    (dept / "runtime").mkdir(parents=True)
    (dept / "state").mkdir()
    escalations = []
    report = M.run_manager_cycle(
        state_dir=dept / "state", dept_dir=dept, now=NOW,
        escalate_fn=lambda issue, context=None: escalations.append(issue),
    )
    codes = {f["code"]: f for f in report["findings"]}
    assert codes["drift_unverifiable"]["severity"] == "warn"
    assert escalations == []


def test_corrupt_manifest_fails_closed_as_breach(tmp_path):
    # deny-by-default: a drift check that cannot run must alarm, never pass
    dept = _pinned_dept(tmp_path)
    current = (dept / "releases" / "current").read_text(encoding="utf-8").strip()
    (dept / "releases" / current / "manifest.json").write_text("not json", encoding="utf-8")
    report = M.run_manager_cycle(state_dir=dept / "state", dept_dir=dept, now=NOW)
    codes = {f["code"]: f for f in report["findings"]}
    assert codes["drift_check_failed"]["severity"] == "breach"


def test_drift_alarm_never_remediates(tmp_path):
    # report-and-alarm only: no re-pin, no revert, no file writes in the dept
    dept = _pinned_dept(tmp_path)
    drifted = "def node(): return 2\n"
    (dept / "runtime" / "node.py").write_text(drifted, encoding="utf-8")
    current_before = (dept / "releases" / "current").read_text(encoding="utf-8")
    release_dirs_before = sorted(p.name for p in (dept / "releases").iterdir())
    report = M.run_manager_cycle(state_dir=dept / "state", dept_dir=dept, now=NOW)
    assert (dept / "runtime" / "node.py").read_text(encoding="utf-8") == drifted
    assert (dept / "releases" / "current").read_text(encoding="utf-8") == current_before
    assert sorted(p.name for p in (dept / "releases").iterdir()) == release_dirs_before
    assert all(a["act"] in M.SHADOW_ACTS for a in report["actions"])


def test_cycle_without_dept_dir_is_unchanged(tmp_path):
    # legacy callers that pass no dept_dir get no drift keys and no drift findings
    report = M.run_manager_cycle(state_dir=tmp_path, now=NOW)
    assert "drift_checked" not in report["sensed"]
    codes = {f["code"] for f in report["findings"]}
    assert not codes & {"release_drift", "release_unpinned", "drift_check_failed",
                        "drift_unverifiable"}


def test_compare_flags_release_drift_from_sensed_keys():
    findings = M.compare(
        {"week_touches": 0, "drift_checked": True, "drift_ok": False,
         "drift_release": "abc123", "drift_mismatch_count": 2,
         "drift_mismatches": ["runtime/a.py", "charter.yaml"]},
        M.DEFAULT_THRESHOLDS,
    )
    codes = {f["code"]: f for f in findings}
    assert codes["release_drift"]["severity"] == "breach"
    assert codes["release_drift"]["observed"] == 2


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
