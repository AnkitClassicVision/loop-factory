#!/usr/bin/env python3
"""Estate manager v1 (B6) — the watchdog that watches the department managers.

The second tier above a department manager. It does not run any department; it
reads the raw estate stores (each department's STATE.json + heartbeats) and the
registry, detects dead / stalled / drifting managers, renders a Control Room
from the raw stores with a staleness banner, and escalates breaches to Hermes.
Model-free. Spec §7.2-7.4, §13.12 (alive-not-working), §13.13 (registry drift).

Cycle: Sense -> Compare -> Decide -> Act -> Record (runs.db-style tick ->
STATE.json atomic epoch -> heartbeat), mirroring the department manager. The
estate manager may park a department; unpark is a human-only D10 decision.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_THRESHOLDS: dict[str, Any] = {
    "manager_max_age_s": 26 * 3600,  # daily cadence + slack; older => dead
    "control_room_slack_s": 2 * 3600,
}


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _now_dt(now):
    if isinstance(now, datetime):
        return now
    return _parse_ts(now) or datetime.now(timezone.utc)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Sense
# --------------------------------------------------------------------------- #

def sense(departments, now, actual_dept_ids=None) -> dict:
    """Read each registered department manager's STATE.json (raw store)."""
    now_dt = _now_dt(now)
    rows = []
    registered_ids = []
    for dept in departments:
        dept_id = dept["id"]
        registered_ids.append(dept_id)
        state_dir = Path(dept["state_dir"])
        state = _read_json(state_dir / "STATE.json")
        if state is None:
            rows.append({"id": dept_id, "state_dir": str(state_dir), "present": False})
            continue
        rows.append({
            "id": dept_id,
            "state_dir": str(state_dir),
            "present": True,
            "last_cycle_at": state.get("last_cycle_at"),
            "epoch": state.get("epoch"),
            "escalations": state.get("escalations", 0),
            "open_findings": state.get("open_findings", []),
        })
    return {
        "now": now_dt.isoformat(),
        "departments": rows,
        "registered_ids": registered_ids,
        "actual_dept_ids": list(actual_dept_ids) if actual_dept_ids is not None else registered_ids,
    }


# --------------------------------------------------------------------------- #
# Compare
# --------------------------------------------------------------------------- #

def _finding(code, severity, detail, dept=None):
    return {"code": code, "severity": severity, "detail": detail, "dept": dept}


def compare(sensed, thresholds=None, prior_epochs=None) -> list[dict]:
    t = thresholds or DEFAULT_THRESHOLDS
    prior_epochs = prior_epochs or {}
    now_dt = _now_dt(sensed.get("now"))
    findings: list[dict] = []

    for row in sensed["departments"]:
        dept_id = row["id"]
        if not row.get("present"):
            findings.append(_finding(
                "registry_drift", "breach",
                f"registered department '{dept_id}' has no STATE.json on disk", dept=dept_id))
            continue
        last = _parse_ts(row.get("last_cycle_at"))
        if last is None or (now_dt - last).total_seconds() > t["manager_max_age_s"]:
            findings.append(_finding(
                "dead_manager", "breach",
                f"'{dept_id}' manager last cycled {row.get('last_cycle_at')} (stale > {t['manager_max_age_s']//3600}h)",
                dept=dept_id))
            continue
        prior = prior_epochs.get(dept_id)
        if prior is not None and row.get("epoch") is not None and row["epoch"] <= prior:
            findings.append(_finding(
                "alive_not_working", "breach",
                f"'{dept_id}' manager heartbeating but epoch not advancing ({prior} -> {row['epoch']})",
                dept=dept_id))

    # registry-vs-reality: a department present in reality but not registered
    registered = set(sensed["registered_ids"])
    for actual in sensed.get("actual_dept_ids", []):
        if actual not in registered:
            findings.append(_finding(
                "registry_drift", "breach",
                f"department '{actual}' exists in reality but is not registered", dept=actual))

    return findings


# --------------------------------------------------------------------------- #
# Decide (shadow: escalate breaches, brief, record — never unpark)
# --------------------------------------------------------------------------- #

def decide(findings, autonomy_state="shadow") -> list[dict]:
    actions = []
    for f in findings:
        if f.get("severity") == "breach":
            actions.append({"act": "escalate", "reason": f["code"], "detail": f["detail"], "dept": f.get("dept")})
    actions.append({"act": "control_room", "reason": "cadence"})
    actions.append({"act": "estate_brief", "reason": "cadence"})
    actions.append({"act": "record", "reason": "cadence"})
    return actions


# --------------------------------------------------------------------------- #
# Control Room (rendered from the raw stores)
# --------------------------------------------------------------------------- #

def render_control_room(sensed, findings, generated_at, stale=False) -> str:
    banner = (
        '<div style="background:#b00;color:#fff;padding:8px;font-weight:bold">'
        'STALE — do not act on this page</div>'
    ) if stale else ""
    rows = []
    for d in sensed["departments"]:
        status = "present" if d.get("present") else "MISSING"
        rows.append(
            f"<tr><td>{d['id']}</td><td>{status}</td><td>{d.get('epoch','-')}</td>"
            f"<td>{d.get('last_cycle_at','-')}</td><td>{d.get('escalations',0)}</td></tr>"
        )
    finding_items = "".join(
        f"<li><b>{f['code']}</b> ({f['severity']}) — {f['detail']}</li>" for f in findings
    ) or "<li>none — estate healthy</li>"
    return (
        "<html><head><title>Estate Control Room</title></head><body>"
        f"{banner}"
        "<h1>Estate Control Room</h1>"
        f"<p>generated {generated_at}</p>"
        "<table border=1><tr><th>department</th><th>status</th><th>epoch</th>"
        "<th>last cycle</th><th>escalations</th></tr>"
        f"{''.join(rows)}</table>"
        f"<h2>Findings</h2><ul>{finding_items}</ul>"
        "</body></html>"
    )


# --------------------------------------------------------------------------- #
# Act + Record
# --------------------------------------------------------------------------- #

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _render_brief(sensed, findings, now_iso, epoch) -> str:
    lines = [
        "# Estate manager brief",
        f"_generated {now_iso} · cycle epoch {epoch}_",
        "",
        "## Departments watched",
    ]
    for d in sensed["departments"]:
        state = "present" if d.get("present") else "MISSING"
        lines.append(f"- {d['id']}: {state}, epoch {d.get('epoch','-')}, last cycle {d.get('last_cycle_at','-')}")
    lines += ["", "## Findings"]
    if findings:
        for f in findings:
            mark = {"breach": "🔴", "warn": "🟡", "info": "⚪"}.get(f["severity"], "•")
            lines.append(f"- {mark} **{f['code']}** — {f['detail']}")
    else:
        lines.append("- none — estate healthy")
    return "\n".join(lines) + "\n"


def run_estate_cycle(departments, estate_state_dir, escalate_fn=None, now=None,
                     thresholds=None, actual_dept_ids=None) -> dict:
    """One Sense -> Compare -> Decide -> Act -> Record estate cycle."""
    estate_state_dir = Path(estate_state_dir)
    now_iso = _now_dt(now).isoformat()

    prior = _read_json(estate_state_dir / "STATE.json") or {}
    epoch = int(prior.get("epoch", -1)) + 1
    prior_epochs = prior.get("dept_epochs", {})

    sensed = sense(departments, now=now, actual_dept_ids=actual_dept_ids)
    findings = compare(sensed, thresholds or DEFAULT_THRESHOLDS, prior_epochs=prior_epochs)
    actions = decide(findings, autonomy_state="shadow")

    escalations = [a for a in actions if a["act"] == "escalate"]
    for a in escalations:
        if escalate_fn is not None:
            escalate_fn(f"[estate] {a['reason']}: {a['detail']}", context={"epoch": epoch, "dept": a.get("dept")})

    # RECORD 1: estate tick (append-only)
    tick_path = estate_state_dir / "runs.jsonl"
    tick_path.parent.mkdir(parents=True, exist_ok=True)
    with tick_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "node": "estate_tick", "epoch": epoch, "timestamp": now_iso,
            "findings": [f["code"] for f in findings], "escalations": len(escalations),
        }) + "\n")

    # RECORD 2: Control Room (from raw stores) + brief
    _atomic_write(estate_state_dir / "control_room.html",
                  render_control_room(sensed, findings, generated_at=now_iso, stale=False))
    _atomic_write(estate_state_dir / "ESTATE_BRIEF.md",
                  _render_brief(sensed, findings, now_iso, epoch))

    # RECORD 3: STATE.json (atomic, monotonic epoch, remembers dept epochs)
    dept_epochs = {r["id"]: r.get("epoch") for r in sensed["departments"] if r.get("present")}
    _atomic_write(estate_state_dir / "STATE.json", json.dumps({
        "epoch": epoch,
        "last_cycle_at": now_iso,
        "dept_epochs": dept_epochs,
        "open_findings": findings,
        "escalations": len(escalations),
    }, indent=2) + "\n")

    # RECORD 4: heartbeat
    hb = estate_state_dir / "heartbeats.jsonl"
    with hb.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": now_iso, "emitter": "estate-manager", "kind": "cycle",
            "payload": {"epoch": epoch, "findings": len(findings), "escalations": len(escalations)},
        }) + "\n")

    return {
        "epoch": epoch,
        "sensed": sensed,
        "findings": findings,
        "escalations": len(escalations),
        "ok": True,
    }


def _departments_from_registry(registry_dir):
    """Load department entries from the estate registry (surface == 'department').

    Each such entry supplies a state_dir (explicit key, or the parent dir of its
    heartbeat_path)."""
    import importlib.util
    reg_path = Path(__file__).resolve().parent / "estate_registry.py"
    spec = importlib.util.spec_from_file_location("estate_registry", reg_path)
    reg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reg)
    departments = []
    for entry in reg.load_registry(registry_dir):
        if entry.get("surface") != "department":
            continue
        state_dir = entry.get("state_dir") or str(Path(entry["heartbeat_path"]).parent)
        departments.append({"id": entry["id"], "state_dir": state_dir})
    return departments


def main() -> None:
    parser = argparse.ArgumentParser(description="Estate manager cycle (watchdog over department managers)")
    parser.add_argument("--registry-dir", default="estate/registry.d")
    parser.add_argument("--estate-state-dir", default="estate/state")
    parser.add_argument("--outbox", default=None)
    args = parser.parse_args()

    escalate_fn = None
    if args.outbox:
        import importlib.util as _ilu
        hil_path = Path(__file__).resolve().parent / "human_in_the_loop.py"
        spec = _ilu.spec_from_file_location("human_in_the_loop", hil_path)
        hil = _ilu.module_from_spec(spec)
        spec.loader.exec_module(hil)

        def escalate_fn(issue, context=None):  # noqa: E306
            hil.escalate("estate", issue, args.outbox, context=context)

    departments = _departments_from_registry(args.registry_dir)
    report = run_estate_cycle(departments, args.estate_state_dir, escalate_fn=escalate_fn)
    print(json.dumps({
        "epoch": report["epoch"],
        "watched": len(departments),
        "findings": [f["code"] for f in report["findings"]],
        "escalations": report["escalations"],
    }))


if __name__ == "__main__":
    main()
