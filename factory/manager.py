"""Factory-standard department manager loop — shadow verb subset.

The second loop of every department. It does NOT draft or send; it watches the
worker loop's telemetry and keeps the lane healthy.

Cycle (each wake):  Sense -> Compare -> Decide (Act) -> Record.
  Sense    read-only, model-free: run cards, approval queue, receipt, budget,
           and release drift (live tree vs pinned release — hard rule 4).
  Compare  deterministic thresholds (no LLM).
  Decide   pick whitelisted acts only; SHADOW limits Act to
           {escalate, daily_brief, record, dispatch, bounded_retry}.
  Record   runs first, then STATE.json (atomic), then heartbeat.

Guarantees baked in here:
  * Model-free: no network, no model calls in Sense/Compare/Record.
  * Shadow gate: gated-live-only verbs (throttle/park, reorder, promotion,
    dept-request) are redirected to an escalation while in shadow.
  * Immutable safety invariants: heal may NEVER modify a floor
    (charter.immutable_safety_invariants). gate_actions raises on any attempt.
  * Single-writer: STATE.json carries a monotonically increasing epoch and is
    written atomically (temp + os.replace) so a restored/duplicate instance is
    visible and cannot silently interleave.

The department's charter.yaml is the source of truth for thresholds, invariants,
and autonomy state; load it via charter_loader. The module-level defaults exist
so the manager can watch a freshly scaffolded department before its charter is
filled in — they are factory defaults, not any department's numbers.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from factory.lockutil import records_lock
except ModuleNotFoundError:
    # Tests and loopfactory.py load factory modules directly by file path.
    _lockutil_spec = importlib.util.spec_from_file_location(
        "factory_lockutil", Path(__file__).with_name("lockutil.py")
    )
    _lockutil = importlib.util.module_from_spec(_lockutil_spec)
    _lockutil_spec.loader.exec_module(_lockutil)
    records_lock = _lockutil.records_lock


# --- factory defaults (charter.yaml is the per-department source of truth) --- #

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "weekly_touch_ceiling": 300,        # setpoints.operational (ceiling, not quota)
    "pace_ceiling_near_frac": 0.9,      # approaching the hard cap -> breach
    "faux_work_touch_floor": 50,        # kill_controller.faux_work min denominator
    "backlog_aging_min": 1,             # any carry-forward >1d surfaces
    "budget_ceilings": {                # budget.weekly_ceilings
        "model_calls": 900,
        "dollars": 40,
        "worker_minutes": 1200,
    },
    "budget_near_frac": 0.8,            # budget fail-closed: auto-stop review at 80%
}

# charter.immutable_safety_invariants.heal_may_not_modify — self-healing may
# NEVER touch these. The manager emits none of them; this list is the guard.
IMMUTABLE_INVARIANTS: frozenset[str] = frozenset(
    {
        "delivery_floor",
        "send_authorization",
        "eligibility_allowlist",
        "frequency_policy",
        "privacy_floor",
        "kill_controller",
        "circuit_breaker",
        "promotion_contract",
        "budget_ceilings",
        "identity_resolution",
        "metric_definitions",
        "gateway_mode",
        "autonomy_state",
    }
)

# Playbook whitelist and the shadow-mode Act subset.
SHADOW_ACTS: frozenset[str] = frozenset(
    {"escalate", "daily_brief", "record", "dispatch", "bounded_retry"}
)
GATED_LIVE_ONLY_ACTS: frozenset[str] = frozenset(
    {"throttle_park", "reorder_queue", "file_promotion", "emit_dept_request"}
)

_TOUCH_STATUSES = {"sent", "sent_shadow"}


class ImmutableInvariantError(RuntimeError):
    """Raised when an action would mutate a charter safety floor."""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _load_jsonl(path: Path | None) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _now(now: str | datetime | None) -> datetime:
    if isinstance(now, datetime):
        return now
    parsed = _parse_ts(now) if isinstance(now, str) else None
    return parsed or datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Sense  (read-only, model-free)
# --------------------------------------------------------------------------- #

def sense(
    state_dir,
    run_db_path=None,
    approval_path=None,
    receipt_path=None,
    touches_path=None,
    outcomes_path=None,
    budget_path=None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Read the worker loop's telemetry into a flat, comparable snapshot."""
    state_dir = Path(state_dir)
    now_dt = _now(now)
    week_start = now_dt - timedelta(days=7)

    approval_path = Path(approval_path) if approval_path else state_dir / "approval_queue.jsonl"
    queue = _load_jsonl(approval_path)

    week_touches = 0
    pending = held = rejected = carried = 0
    for row in queue:
        status = row.get("status")
        queued = _parse_ts(row.get("queued_at"))
        if status in _TOUCH_STATUSES and queued and queued >= week_start:
            week_touches += 1
        if status == "pending_approval":
            pending += 1
            if queued and (now_dt - queued) > timedelta(hours=24):
                carried += 1
        elif status == "held_recipient_mismatch":
            held += 1
        elif status == "rejected":
            rejected += 1

    runs = _load_jsonl(run_db_path) if run_db_path else _load_jsonl(state_dir / "runs.jsonl")
    last_run_at = None
    last_run_ok = True
    run_errors = 0
    if runs:
        last = runs[-1]
        last_run_at = last.get("queued_at") or last.get("timestamp")
        run_errors = sum(1 for c in runs if c.get("status") in {"halted_incomplete_context", "error"})
        last_run_ok = last.get("status") not in {"error", "halted_incomplete_context"}

    # attributable conversions come from the INDEPENDENT outcome sensor; default
    # 0 until that sensor is wired. The manager never self-scores.
    conversions = 0
    for row in _load_jsonl(outcomes_path):
        if row.get("held") or row.get("meeting_id"):
            conversions += 1

    budget_used: dict[str, Any] = {}
    budget_unreadable = False
    # Red-team operator catch: a configured ceiling with no usage feed must
    # never read as healthy zero spend. Distinguish the three honest states:
    # unconfigured (no path wired), missing (path wired, file absent), and
    # unreadable (file present, corrupt).
    budget_unconfigured = budget_path is None
    budget_missing = False
    if budget_path and Path(budget_path).exists():
        try:
            budget_used = json.loads(Path(budget_path).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            # Codex review #13: missing/corrupt cost telemetry must surface as a
            # breach, never silently read as a healthy zero-spend baseline.
            budget_used = {}
            budget_unreadable = True
    elif budget_path:
        budget_missing = True

    return {
        "now": now_dt.isoformat(),
        "week_start": week_start.isoformat(),
        "week_touches": week_touches,
        "pending": pending,
        "held_mismatch": held,
        "rejected": rejected,
        "carried_forward": carried,
        "last_run_at": last_run_at,
        "last_run_ok": last_run_ok,
        "run_errors": run_errors,
        "conversions": conversions,
        "budget_used": budget_used,
        "budget_unreadable": budget_unreadable,
        "budget_telemetry_missing": budget_missing,
        "budget_telemetry_unconfigured": budget_unconfigured,
    }


# STATE.json is a bounded human surface; the sensed escalation LIST is
# bounded only there — compare/decide/act always see every open escalation
# (review B3: >20 open escalations must not truncate into silence).
STATE_GRAPH_ESCALATION_BOUND = 20


def sense_graph_escalations(state_dir) -> dict[str, Any]:
    """Read-only, model-free replay of the runner->manager escalation bridge.

    factory/runner.py appends one row per terminal escalated/killed graph run
    to state/graph_escalations.jsonl. Rows are replayed keyed by run_id: an
    'open' row (re)raises the escalation, a 'resolved' marker clears it, so
    duplicate bridge rows from a crash-resume window collapse to one. ALL
    open escalations are returned — persistence bounding is presentation-only
    (see act). A stream that exists but cannot be parsed, or a row without a
    run_id, is unverifiable and surfaces as its own breach (deny-by-default)
    — an escalation must never vanish because its record is broken.
    """
    path = Path(state_dir) / "graph_escalations.jsonl"
    active: dict[str, dict[str, Any]] = {}
    unreadable = False
    if path.exists():
        try:
            rows = _load_jsonl(path)
        except (ValueError, OSError):
            rows = []
            unreadable = True
        for row in rows:
            run_id = row.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                unreadable = True
                continue
            marker = row.get("marker") or row.get("status")
            if marker == "resolved" or row.get("resolved") is True:
                active.pop(run_id, None)
            else:
                active[run_id] = {
                    "run_id": run_id,
                    "loop_id": row.get("loop_id"),
                    "state": row.get("state"),
                    "termination_reason": row.get("termination_reason"),
                }
    escalations = sorted(active.values(), key=lambda row: row["run_id"])
    return {
        "graph_escalations": escalations,  # ALL of them; act bounds STATE only
        "graph_escalation_count": len(escalations),
        "graph_escalations_truncated":
            len(escalations) > STATE_GRAPH_ESCALATION_BOUND,
        "graph_escalations_unreadable": unreadable,
    }


def resolve_graph_escalation(state_dir, *, department: str, run_id: str,
                             now: str | datetime | None = None) -> dict:
    """One coordinated resolution for a bridged graph escalation (review B2).

    This lives on the MANAGER because the manager owns both ledgers it must
    reconcile: it writes escalation_fingerprints.jsonl (outbox dedup) and it
    is the sole reader of graph_escalations.jsonl (the runner only reports —
    giving the runner a resolution verb would be new authority). A human
    resolves; this verb records it.

    Two ordered, fsync'd appends under the records fence, fingerprint FIRST:
    if the process dies between them the escalation stays OPEN with a freed
    fingerprint — the next cycle re-delivers (noisy but never silent). The
    reverse order could clear the sensor while the fingerprint still
    suppresses, which is exactly the reopen-into-silence defect this fixes.
    After resolution, a reopened run regains fresh fingerprint eligibility.
    """
    state_dir = Path(state_dir)
    now_iso = _now(now).isoformat()
    fingerprint = _escalation_fingerprint(
        department, "graph_run_escalated", str(run_id))
    with records_lock(state_dir):
        _append_jsonl(state_dir / "escalation_fingerprints.jsonl", {
            "department": department,
            "finding_code": "graph_run_escalated",
            "fingerprint": fingerprint,
            "marker": "resolved",
            "subject": str(run_id),
            "timestamp": now_iso,
        })
        _append_jsonl(state_dir / "graph_escalations.jsonl", {
            "department": department,
            "run_id": str(run_id),
            "marker": "resolved",
            "ts": now_iso,
        })
    return {"resolved": str(run_id), "fingerprint": fingerprint,
            "department": department}


def sense_drift(dept_dir, release_root=None) -> dict[str, Any]:
    """Read-only, model-free release-drift snapshot (graphs.check_drift).

    Deny-by-default: a check that cannot run reports drift_error rather than
    quietly reading as healthy. A releases/ dir with no `current` pin surfaces
    as not-ok with the check_drift reason. The ONLY quiet-ish outcome is a
    department that exists but has never had a releases/ dir (pre-F4), which
    reports drift_checked=False with a visible skip reason; a missing or
    unreadable department dir, or a releases path that exists but is not a
    directory, is a broken deployment and reports drift_error.
    """
    dept_dir = Path(dept_dir)
    release_root = Path(release_root) if release_root else dept_dir / "releases"

    def _error(message: str) -> dict[str, Any]:
        return {
            "drift_checked": True,
            "drift_ok": False,
            "drift_error": message,
            "drift_release": None,
            "drift_mismatch_count": 0,
            "drift_mismatches": [],
            "drift_mismatches_truncated": False,
        }

    try:
        dept_stat = os.stat(dept_dir)
    except FileNotFoundError:
        # os.stat follows symlinks, so a dangling link raises as if absent;
        # islink (lstat-based) distinguishes the broken-deployment case.
        if os.path.islink(dept_dir):
            return _error(f"department path is a dangling symlink: {dept_dir}")
        return _error(f"department directory does not exist: {dept_dir}")
    except OSError as exc:
        return _error(f"department directory unreadable: {type(exc).__name__}: {exc}")
    if not stat.S_ISDIR(dept_stat.st_mode):
        return _error(f"department path is not a directory: {dept_dir}")

    try:
        root_stat = os.stat(release_root)
    except FileNotFoundError:
        # only a genuinely absent path is pre-F4; a dangling releases symlink
        # EXISTS and is a broken deployment — it must breach, never warn.
        if os.path.islink(release_root):
            return _error(f"release root is a dangling symlink: {release_root}")
        return {
            "drift_checked": False,
            "drift_skipped_reason": "no releases directory — nothing pinned yet (pre-F4)",
        }
    except OSError as exc:
        return _error(f"release root unreadable: {type(exc).__name__}: {exc}")
    if not stat.S_ISDIR(root_stat.st_mode):
        return _error(f"release root exists but is not a directory: {release_root}")

    try:  # fail-closed: an unrunnable check (loader included) is a finding
        import importlib.util

        graphs_path = Path(__file__).resolve().parent / "graphs.py"
        spec = importlib.util.spec_from_file_location("graphs", graphs_path)
        graphs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(graphs)
        verdict = graphs.check_drift(dept_dir, release_root)
    except Exception as exc:
        return _error(f"{type(exc).__name__}: {exc}")
    mismatches = sorted(verdict.get("mismatches") or [])
    return {
        "drift_checked": True,
        "drift_ok": bool(verdict.get("ok")),
        "drift_error": None,
        "drift_release": verdict.get("current"),
        "drift_reason": verdict.get("reason"),
        "drift_mismatch_count": len(mismatches),
        "drift_mismatches": mismatches[:20],  # bounded for STATE.json
        "drift_mismatches_truncated": len(mismatches) > 20,
    }


# --------------------------------------------------------------------------- #
# Compare  (deterministic thresholds)
# --------------------------------------------------------------------------- #

def _finding(code, severity, detail, observed=None, setpoint=None) -> dict:
    return {
        "code": code,
        "severity": severity,
        "detail": detail,
        "observed": observed,
        "setpoint": setpoint,
    }


def compare(sensed: dict, thresholds: dict | None = None) -> list[dict]:
    """Turn a sensed snapshot into findings. Pure function, no side effects."""
    t = thresholds or DEFAULT_THRESHOLDS
    findings: list[dict] = []

    touches = int(sensed.get("week_touches", 0) or 0)
    conversions = int(sensed.get("conversions", 0) or 0)
    held = int(sensed.get("held_mismatch", 0) or 0)
    carried = int(sensed.get("carried_forward", 0) or 0)
    ceiling = t["weekly_touch_ceiling"]

    # breach: wrong-recipient hold (send_floor / reputation surface)
    if held > 0:
        findings.append(
            _finding("held_recipient_mismatch", "breach",
                     f"{held} draft(s) held: recipient did not match the addressed name",
                     observed=held, setpoint=0)
        )

    # breach: approaching the hard weekly ceiling
    if touches >= t["pace_ceiling_near_frac"] * ceiling:
        findings.append(
            _finding("pace_ceiling_near", "breach",
                     f"{touches} valid touches this week — near the {ceiling}/wk ceiling",
                     observed=touches, setpoint=ceiling)
        )

    # breach: faux-work / gaming — activity over the floor with zero conversion
    if touches > t["faux_work_touch_floor"] and conversions == 0:
        findings.append(
            _finding("faux_work", "breach",
                     f"{touches} touches with 0 attributable conversions — faux-work signal",
                     observed=touches, setpoint=t["faux_work_touch_floor"])
        )

    # warn: aging approvals (carry-forward backlog)
    if carried >= t["backlog_aging_min"]:
        findings.append(
            _finding("backlog_aging", "warn",
                     f"{carried} approval(s) waiting on the owner >1 day",
                     observed=carried, setpoint=0)
        )

    # breach: budget nearing a ceiling (fail-closed at 80%)
    for key, cap in t["budget_ceilings"].items():
        used = sensed.get("budget_used", {}).get(key)
        if used is not None and cap and used >= t["budget_near_frac"] * cap:
            findings.append(
                _finding(f"budget_near:{key}", "breach",
                         f"{key} at {used}/{cap} — >= {int(t['budget_near_frac']*100)}% of the weekly ceiling",
                         observed=used, setpoint=cap)
            )

    # breach: cost telemetry present but unreadable (fail-closed visibility)
    if sensed.get("budget_unreadable"):
        findings.append(
            _finding("budget_telemetry_unreadable", "breach",
                     "budget telemetry exists but could not be parsed — spend is unverifiable",
                     observed=None, setpoint=None)
        )

    # breach: a ceiling exists but its usage feed is wired and absent —
    # spend is unverifiable, which is a guard failure, not a zero.
    if t["budget_ceilings"] and sensed.get("budget_telemetry_missing"):
        findings.append(
            _finding("budget_telemetry_missing", "breach",
                     "budget ceilings are set but the usage telemetry file is "
                     "absent — spend is unverifiable (wire the producer or fix "
                     "the path)",
                     observed=None, setpoint=None)
        )
    # warn: ceilings exist and no telemetry path is wired at all — visible
    # pressure without an estate-wide alarm storm for departments that have
    # not adopted the budget feed yet.
    if t["budget_ceilings"] and sensed.get("budget_telemetry_unconfigured"):
        findings.append(
            _finding("budget_telemetry_unconfigured", "warn",
                     "budget ceilings are set but no usage telemetry path is "
                     "configured — pass --budget to the manager invocation",
                     observed=None, setpoint=None)
        )

    # release drift (hard rule 4: process change = map change + QA). The
    # manager only reports and escalates — re-pin/revert is the human-run
    # process-change runbook, never an automated act.
    if sensed.get("drift_checked"):
        if sensed.get("drift_error"):
            findings.append(
                _finding("drift_check_failed", "breach",
                         f"release drift check could not run ({sensed['drift_error']}) — "
                         "process integrity is unverifiable (deny-by-default)",
                         observed=None, setpoint=0)
            )
        elif sensed.get("drift_ok") is False:
            count = int(sensed.get("drift_mismatch_count", 0) or 0)
            if count:
                sample = ", ".join(sensed.get("drift_mismatches", [])[:5])
                findings.append(
                    _finding("release_drift", "breach",
                             f"{count} artifact(s) differ from pinned release "
                             f"{sensed.get('drift_release')} ({sample}) — process changed "
                             "without re-pin; run runbooks/process-change-qa.md",
                             observed=count, setpoint=0)
                )
            else:
                findings.append(
                    _finding("release_unpinned", "breach",
                             sensed.get("drift_reason")
                             or "releases/ exists but no current release is pinned",
                             observed=0, setpoint=1)
                )
    elif sensed.get("drift_skipped_reason"):
        findings.append(
            _finding("drift_unverifiable", "warn",
                     f"release drift not verifiable: {sensed['drift_skipped_reason']}",
                     observed=None, setpoint=None)
        )

    # breach: a graph run terminated escalated/killed — the runner bridged it
    # here and it stays open until a human resolves it. subject=run_id keeps
    # the outbox delivery fingerprint unique per run.
    for escalation in sensed.get("graph_escalations") or []:
        finding = _finding(
            "graph_run_escalated", "breach",
            f"graph run {escalation.get('run_id')} "
            f"({escalation.get('loop_id')}) terminated "
            f"{escalation.get('state')}: "
            f"{escalation.get('termination_reason')} — awaiting a human",
            observed=1, setpoint=0)
        finding["subject"] = escalation.get("run_id")
        findings.append(finding)

    # breach: the escalation bridge stream is unverifiable (fail-closed)
    if sensed.get("graph_escalations_unreadable"):
        findings.append(
            _finding("graph_escalations_unreadable", "breach",
                     "graph escalation records exist but could not be parsed "
                     "— runner escalations are unverifiable (deny-by-default)",
                     observed=None, setpoint=None)
        )

    # breach: last worker run errored
    if sensed.get("last_run_ok") is False:
        findings.append(
            _finding("run_failed", "breach", "last worker run did not complete cleanly",
                     observed=sensed.get("run_errors"), setpoint=0)
        )

    # info: under pace (expected in shadow — visibility only)
    if touches < 0.1 * ceiling:
        findings.append(
            _finding("pace_under", "info",
                     f"{touches} valid touches this week (ramp/shadow)", observed=touches, setpoint=ceiling)
        )

    return findings


# --------------------------------------------------------------------------- #
# Decide  (whitelist + shadow gate + immutable guard)
# --------------------------------------------------------------------------- #

def gate_actions(actions: Iterable[dict], autonomy_state: str,
                 immutable_invariants: frozenset[str] = IMMUTABLE_INVARIANTS) -> list[dict]:
    """Enforce the two hard rules on any proposed action list.

    1. No action may target an immutable safety invariant -> ImmutableInvariantError.
    2. In shadow, a gated-live-only verb is redirected to an escalation.
    3. A verb outside the known playbook (SHADOW_ACTS | GATED_LIVE_ONLY_ACTS) is
       redirected to an escalation at EVERY autonomy state — the manager cannot
       invent actions (Codex review #5: unknown acts must not pass at gated-live).
    """
    known = SHADOW_ACTS | GATED_LIVE_ONLY_ACTS
    gated: list[dict] = []
    for action in actions:
        target = action.get("target")
        if target and target in immutable_invariants:
            raise ImmutableInvariantError(
                f"action {action.get('act')!r} may not modify immutable invariant {target!r}"
            )
        act = action.get("act")
        if act not in known:
            gated.append({
                "act": "escalate",
                "reason": "unknown_act",
                "finding_code": action.get("finding_code"),
                "detail": f"'{act}' is not in the ratified playbook; escalating",
            })
            continue
        if autonomy_state == "shadow" and act not in SHADOW_ACTS:
            gated.append({
                "act": "escalate",
                "reason": "action_requires_gated_live",
                "finding_code": action.get("finding_code"),
                "detail": f"'{act}' is not permitted in shadow; escalating for a human decision",
            })
        else:
            gated.append(action)
    return gated


def decide(findings: list[dict], autonomy_state: str = "shadow",
           immutable_invariants: frozenset[str] = IMMUTABLE_INVARIANTS) -> list[dict]:
    """Map findings to whitelisted acts. Every breach escalates; warns ride the
    brief; a daily brief and a record are always emitted."""
    proposed: list[dict] = []
    for f in findings:
        if f.get("severity") == "breach":
            proposed.append({
                "act": "escalate",
                "reason": f["code"],
                "finding_code": f["code"],
                "subject": f.get("subject") or f["code"],
                "detail": f.get("detail", ""),
            })
    proposed.append({"act": "daily_brief", "reason": "cadence"})
    proposed.append({"act": "record", "reason": "cadence"})
    return gate_actions(proposed, autonomy_state, immutable_invariants)


# --------------------------------------------------------------------------- #
# Act + Record
# --------------------------------------------------------------------------- #

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _escalation_subject(action: dict[str, Any]) -> str:
    """Return the stable identity within a finding, excluding changing detail."""
    return str(
        action.get("subject")
        or action.get("target")
        or action.get("finding_code")
        or action.get("reason")
        or "manager"
    )


def _escalation_fingerprint(
    department: str, finding_code: str, subject: str
) -> str:
    material = f"{department}|{finding_code}|{subject}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _active_escalation_fingerprints(path: Path) -> set[str]:
    """Replay append-only delivery/resolution markers into the active set."""
    active: set[str] = set()
    for row in _load_jsonl(path):
        fingerprint = row.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            continue
        marker = row.get("marker") or row.get("status")
        if marker == "resolved" or row.get("resolved") is True:
            active.discard(fingerprint)
        elif marker == "delivered" or row.get("delivered") is True:
            active.add(fingerprint)
    return active


def _records_state_dir(state_path, heartbeat_path, run_db_path) -> Path | None:
    for candidate in (state_path, heartbeat_path, run_db_path):
        if candidate is not None:
            return Path(candidate).parent
    return None


def _render_brief(sensed, findings, actions, now_iso, epoch, department, thresholds) -> str:
    t = thresholds or DEFAULT_THRESHOLDS
    lines = [
        f"# {department} department manager brief",
        f"_generated {now_iso} · cycle epoch {epoch} · SHADOW_",
        "",
        "## Numbers",
        f"- valid touches this week: {sensed.get('week_touches', 0)} / {t['weekly_touch_ceiling']} ceiling",
        f"- approvals waiting on the owner: {sensed.get('pending', 0)} ({sensed.get('carried_forward', 0)} aged >1 day)",
        f"- held (recipient mismatch): {sensed.get('held_mismatch', 0)}",
        f"- attributable conversions: {sensed.get('conversions', 0)}",
        "",
        "## Findings",
    ]
    if findings:
        for f in findings:
            mark = {"breach": "🔴", "warn": "🟡", "info": "⚪"}.get(f["severity"], "•")
            lines.append(f"- {mark} **{f['code']}** — {f['detail']}")
    else:
        lines.append("- none — lane healthy")
    escalated = [a for a in actions if a["act"] == "escalate"]
    lines += ["", "## Sent to the owner"]
    if escalated:
        for a in escalated:
            lines.append(f"- {a.get('finding_code') or a.get('reason')}: {a.get('detail', '')}")
    else:
        lines.append("- nothing needs you right now")
    return "\n".join(lines) + "\n"


def act(
    actions: list[dict],
    *,
    sensed: dict | None = None,
    findings: list[dict] | None = None,
    escalate_fn: Callable[..., Any] | None = None,
    state_path=None,
    heartbeat_path=None,
    brief_path=None,
    run_db_path=None,
    department: str = "department",
    thresholds: dict | None = None,
    autonomy_state: str = "shadow",
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Execute the (shadow-subset) acts and record in the ratified order:
    runs first, then STATE.json (atomic, epoch++), then heartbeat."""
    sensed = sensed or {}
    findings = findings or []
    now_iso = _now(now).isoformat()

    records_dir = _records_state_dir(state_path, heartbeat_path, run_db_path)

    def _act_and_record() -> tuple[int, int, int, list[str]]:
        # Epoch read and all shared record writes are one fenced transaction.
        epoch = 0
        if state_path and Path(state_path).exists():
            try:
                epoch = int(
                    json.loads(Path(state_path).read_text(encoding="utf-8")).get(
                        "epoch", -1
                    )
                ) + 1
            except (ValueError, OSError):
                epoch = 0

        candidates = [a for a in actions if a["act"] == "escalate"]
        delivered = 0
        undelivered = 0
        suppressed: list[str] = []
        fingerprint_path = (
            records_dir / "escalation_fingerprints.jsonl"
            if records_dir is not None
            else None
        )
        active_fingerprints = (
            _active_escalation_fingerprints(fingerprint_path)
            if fingerprint_path is not None
            else set()
        )

        for action in candidates:
            finding_code = str(
                action.get("finding_code") or action.get("reason") or "unknown"
            )
            subject = _escalation_subject(action)
            fingerprint = _escalation_fingerprint(
                department, finding_code, subject
            )
            if fingerprint in active_fingerprints:
                suppressed.append(fingerprint)
                continue
            if escalate_fn is None:
                undelivered += 1
                continue

            issue = (
                f"[{department}] {finding_code}: {action.get('detail', '')}"
            ).strip()
            escalate_fn(
                issue,
                context={
                    "epoch": epoch,
                    "finding": action.get("finding_code"),
                    "fingerprint": fingerprint,
                    "subject": subject,
                },
            )
            delivered += 1
            active_fingerprints.add(fingerprint)
            if fingerprint_path is not None:
                _append_jsonl(
                    fingerprint_path,
                    {
                        "department": department,
                        "finding_code": finding_code,
                        "fingerprint": fingerprint,
                        "marker": "delivered",
                        "subject": subject,
                        "timestamp": now_iso,
                    },
                )

        notes = [
            {
                "code": "escalation_suppressed_duplicate",
                "fingerprint": fingerprint,
            }
            for fingerprint in suppressed
        ]

        # RECORD 1: runs manager tick card (append-only)
        if run_db_path is not None:
            _append_jsonl(
                Path(run_db_path),
                {
                    "node": "manager_tick",
                    "epoch": epoch,
                    "timestamp": now_iso,
                    "findings": [f["code"] for f in findings],
                    "escalations": delivered,
                    "escalations_undelivered": undelivered,
                    "notes": notes,
                },
            )

        # RECORD 2: brief (human surface)
        if brief_path is not None:
            _atomic_write(
                Path(brief_path),
                _render_brief(
                    sensed,
                    findings,
                    actions,
                    now_iso,
                    epoch,
                    department,
                    thresholds,
                ),
            )

        # RECORD 3: STATE.json (atomic, monotonic epoch). The escalation LIST
        # is bounded here and only here — a presentation limit, never a
        # processing limit (every open escalation was already compared,
        # escalated, and delivered above; the honest total + truncated flag
        # ride along).
        state_sensed = dict(sensed)
        escalation_rows = state_sensed.get("graph_escalations")
        if (isinstance(escalation_rows, list)
                and len(escalation_rows) > STATE_GRAPH_ESCALATION_BOUND):
            state_sensed["graph_escalations"] = \
                escalation_rows[:STATE_GRAPH_ESCALATION_BOUND]
        if state_path is not None:
            _atomic_write(
                Path(state_path),
                json.dumps(
                    {
                        "department": department,
                        "epoch": epoch,
                        "last_cycle_at": now_iso,
                        "autonomy_state": autonomy_state,
                        "sensed": state_sensed,
                        "open_findings": findings,
                        "escalations": delivered,
                        "escalations_undelivered": undelivered,
                        "escalations_suppressed": len(suppressed),
                    },
                    indent=2,
                )
                + "\n",
            )

        # RECORD 4: heartbeat (append)
        if heartbeat_path is not None:
            _append_jsonl(
                Path(heartbeat_path),
                {
                    "ts": now_iso,
                    "epoch": epoch,
                    "ok": undelivered == 0,
                    "findings": len(findings),
                    "escalations": delivered,
                    "escalations_undelivered": undelivered,
                    "escalations_suppressed": len(suppressed),
                },
            )
        return epoch, delivered, undelivered, suppressed

    if records_dir is None:
        epoch, escalations, escalations_undelivered, suppressed = _act_and_record()
    else:
        with records_lock(records_dir):
            epoch, escalations, escalations_undelivered, suppressed = _act_and_record()

    return {
        "epoch": epoch,
        "escalations": escalations,
        "escalations_undelivered": escalations_undelivered,
        "escalations_suppressed": len(suppressed),
        "brief_path": str(brief_path) if brief_path else None,
        "ok": True,
    }


# --------------------------------------------------------------------------- #
# Cycle
# --------------------------------------------------------------------------- #

def run_manager_cycle(
    state_dir,
    autonomy_state: str = "shadow",
    thresholds: dict | None = None,
    escalate_fn: Callable[..., Any] | None = None,
    department: str = "department",
    now: str | datetime | None = None,
    sense_fn: Callable[..., dict] | None = None,
    dept_dir=None,
    release_root=None,
    **telemetry_paths,
) -> dict[str, Any]:
    """One full Sense -> Compare -> Decide -> Act -> Record cycle.

    The default sense() implements the factory-standard outreach-shaped
    telemetry contract (approval queue, touches, conversions). A department
    with a different worker shape supplies its own sense_fn returning the same
    flat snapshot keys — the compare/decide/act discipline is what is fixed.

    When dept_dir is given, the release-drift sensor runs on every tick and
    its snapshot merges into sensed regardless of sense_fn — hard rule 4 is
    watched on the same cadence as everything else.
    """
    state_dir = Path(state_dir)
    sensed = (sense_fn or sense)(state_dir, now=now, **telemetry_paths)
    if dept_dir is not None:
        sensed.update(sense_drift(dept_dir, release_root))
    # Like drift, the runner->manager escalation bridge is watched on every
    # tick regardless of a department's custom sense_fn shape — a terminal
    # escalated/killed graph run must never depend on the worker's telemetry
    # contract to reach a human.
    sensed.update(sense_graph_escalations(state_dir))
    findings = compare(sensed, thresholds or DEFAULT_THRESHOLDS)
    actions = decide(findings, autonomy_state=autonomy_state)
    report = act(
        actions,
        sensed=sensed,
        findings=findings,
        escalate_fn=escalate_fn,
        state_path=state_dir / "STATE.json",
        heartbeat_path=state_dir / "heartbeats.jsonl",
        brief_path=state_dir / "MANAGER_BRIEF.md",
        run_db_path=state_dir / "runs.jsonl",
        department=department,
        thresholds=thresholds,
        autonomy_state=autonomy_state,
        now=now,
    )
    report.update({"sensed": sensed, "findings": findings, "actions": actions})
    return report


def _load_charter_config(repo_root: Path, department: str):
    """Charter-first config: thresholds + autonomy from the department's
    charter when it exists (source of truth); factory defaults otherwise."""
    import importlib.util

    charter_path = repo_root / "departments" / department / "charter.yaml"
    if not charter_path.exists():
        return None
    loader_path = Path(__file__).resolve().parent / "charter_loader.py"
    spec = importlib.util.spec_from_file_location("charter_loader", loader_path)
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    charter = loader.load_charter(charter_path, expect_department=department)
    return {
        "thresholds": loader.thresholds(charter),
        "autonomy_state": loader.autonomy_state(charter),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Factory-standard department manager cycle (shadow verb subset)")
    parser.add_argument("--department", required=True)
    parser.add_argument("--root", default=".", help="repo root holding departments/")
    parser.add_argument("--state-dir", default=None,
                        help="defaults to <root>/departments/<department>/state")
    parser.add_argument("--autonomy-state", default=None,
                        help="override; the charter is the source of truth when present")
    parser.add_argument("--outbox", default=None, help="human-in-the-loop outbox to escalate into")
    parser.add_argument("--budget", default=None,
                        help="usage telemetry JSON ({kind: used}) compared "
                             "against the charter's weekly ceilings; a wired "
                             "path whose file is absent is a breach")
    parser.add_argument("--resolve-graph-run", default=None, metavar="RUN_ID",
                        help="record a human resolution for a bridged graph "
                             "escalation (clears BOTH ledgers, coordinated) "
                             "instead of running a cycle")
    args = parser.parse_args()

    root = Path(args.root)
    state_dir = Path(args.state_dir) if args.state_dir else (
        root / "departments" / args.department / "state")

    if args.resolve_graph_run:
        print(json.dumps(resolve_graph_escalation(
            state_dir, department=args.department,
            run_id=args.resolve_graph_run)))
        return

    config = _load_charter_config(root, args.department)
    thresholds = config["thresholds"] if config else None
    # Codex review #5: when a charter exists it is the SOLE authority on
    # autonomy — a CLI flag must not promote past it. The flag applies only to
    # charterless (scaffold/test) departments.
    if config:
        autonomy = config["autonomy_state"]
        if args.autonomy_state and args.autonomy_state != autonomy:
            print(json.dumps({"note": "ignored --autonomy-state; the charter is authoritative"}))
    else:
        autonomy = args.autonomy_state or "shadow"

    escalate_fn = None
    if args.outbox:
        import importlib.util as _ilu

        hil_path = Path(__file__).resolve().parent / "human_in_the_loop.py"
        spec = _ilu.spec_from_file_location("human_in_the_loop", hil_path)
        hil = _ilu.module_from_spec(spec)
        spec.loader.exec_module(hil)

        def escalate_fn(issue, context=None):  # noqa: E306
            hil.escalate(args.department, issue, args.outbox, context=context)

    # dept_dir passes unconditionally: a CLI invocation whose department dir
    # cannot be resolved must surface drift_check_failed, never silently take
    # the legacy no-drift path (that path is for programmatic callers only).
    report = run_manager_cycle(
        state_dir, autonomy_state=autonomy, thresholds=thresholds,
        escalate_fn=escalate_fn, department=args.department,
        dept_dir=root / "departments" / args.department,
        budget_path=args.budget,
    )
    print(json.dumps({
        "department": args.department,
        "epoch": report["epoch"],
        "autonomy_state": autonomy,
        "charter_loaded": config is not None,
        "findings": [f["code"] for f in report["findings"]],
        "escalations": report["escalations"],
        "brief": report["brief_path"],
    }))


if __name__ == "__main__":
    main()
