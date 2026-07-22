"""Factory-standard department manager loop — shadow verb subset.

The second loop of every department. It does NOT draft or send; it watches the
worker loop's telemetry and keeps the lane healthy.

Cycle (each wake):  Sense -> Compare -> Decide (Act) -> Record.
  Sense    read-only, model-free: run cards, approval queue, receipt, budget.
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
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


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
    if budget_path and Path(budget_path).exists():
        try:
            budget_used = json.loads(Path(budget_path).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            budget_used = {}

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
    2. In shadow, a gated-live-only verb is redirected to an escalation; any verb
       outside the shadow subset that is not otherwise known is also redirected.
    """
    gated: list[dict] = []
    for action in actions:
        target = action.get("target")
        if target and target in immutable_invariants:
            raise ImmutableInvariantError(
                f"action {action.get('act')!r} may not modify immutable invariant {target!r}"
            )
        act = action.get("act")
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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

    # epoch from prior STATE (single-writer fencing)
    epoch = 0
    if state_path and Path(state_path).exists():
        try:
            epoch = int(json.loads(Path(state_path).read_text(encoding="utf-8")).get("epoch", -1)) + 1
        except (ValueError, OSError):
            epoch = 0

    escalations = [a for a in actions if a["act"] == "escalate"]
    for a in escalations:
        if escalate_fn is not None:
            issue = f"[{department}] {a.get('finding_code') or a.get('reason')}: {a.get('detail', '')}".strip()
            escalate_fn(issue, context={"epoch": epoch, "finding": a.get("finding_code")})

    # RECORD 1: runs manager tick card (append-only)
    if run_db_path is not None:
        run_db_path = Path(run_db_path)
        run_db_path.parent.mkdir(parents=True, exist_ok=True)
        with run_db_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "node": "manager_tick",
                "epoch": epoch,
                "timestamp": now_iso,
                "findings": [f["code"] for f in findings],
                "escalations": len(escalations),
            }) + "\n")

    # RECORD 2: brief (human surface)
    if brief_path is not None:
        _atomic_write(Path(brief_path), _render_brief(
            sensed, findings, actions, now_iso, epoch, department, thresholds))

    # RECORD 3: STATE.json (atomic, monotonic epoch)
    if state_path is not None:
        _atomic_write(Path(state_path), json.dumps({
            "department": department,
            "epoch": epoch,
            "last_cycle_at": now_iso,
            "autonomy_state": autonomy_state,
            "sensed": sensed,
            "open_findings": findings,
            "escalations": len(escalations),
        }, indent=2) + "\n")

    # RECORD 4: heartbeat (append)
    if heartbeat_path is not None:
        heartbeat_path = Path(heartbeat_path)
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        with heartbeat_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": now_iso,
                "epoch": epoch,
                "ok": True,
                "findings": len(findings),
                "escalations": len(escalations),
            }) + "\n")

    return {
        "epoch": epoch,
        "escalations": len(escalations),
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
    **telemetry_paths,
) -> dict[str, Any]:
    """One full Sense -> Compare -> Decide -> Act -> Record cycle."""
    state_dir = Path(state_dir)
    sensed = sense(state_dir, now=now, **telemetry_paths)
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
    charter = loader.load_charter(charter_path)
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
    args = parser.parse_args()

    root = Path(args.root)
    state_dir = Path(args.state_dir) if args.state_dir else (
        root / "departments" / args.department / "state")

    config = _load_charter_config(root, args.department)
    thresholds = config["thresholds"] if config else None
    autonomy = args.autonomy_state or (config["autonomy_state"] if config else "shadow")

    escalate_fn = None
    if args.outbox:
        import importlib.util as _ilu

        hil_path = Path(__file__).resolve().parent / "human_in_the_loop.py"
        spec = _ilu.spec_from_file_location("human_in_the_loop", hil_path)
        hil = _ilu.module_from_spec(spec)
        spec.loader.exec_module(hil)

        def escalate_fn(issue, context=None):  # noqa: E306
            hil.escalate(args.department, issue, args.outbox, context=context)

    report = run_manager_cycle(
        state_dir, autonomy_state=autonomy, thresholds=thresholds,
        escalate_fn=escalate_fn, department=args.department,
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
