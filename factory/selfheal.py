"""Deterministic, propose-only self-heal ladder and escalation artifacts.

L2 and L3 deliberately produce proposals only.  Nothing in this module edits
runtime code, applies a patch, or performs an external effect.  Callers supply
``now`` so routing and artifacts are reproducible in tests and replay.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


AUTO_FIXABLE = frozenset({
    "runtime_config",
    "stale_artifact",
    "dependency_pin",
    "node_code",
})
ALWAYS_HUMAN = frozenset({"meaning", "grading", "leak_adjacent", "governance"})
RUNGS = ("L0", "L1", "L2", "L3", "L4", "L5")
PATCH_RUNGS = frozenset({"L2", "L3"})
WEEKLY_PATCH_BUDGET = 10
DEMOTION_FAILURES = 3
DEMOTION_DAYS = 7

_STATE_FILE = "selfheal_state.json"
_NODE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _require_now(now: Any) -> datetime:
    if not isinstance(now, datetime):
        raise ValueError("now must be a datetime")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _require_node(node: Any) -> str:
    if not isinstance(node, str) or not _NODE_NAME.fullmatch(node):
        raise ValueError("node must be a non-empty path-safe string")
    return node


def _require_string(mapping: dict[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _parse_iso(value: Any, field: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


def _week_start(now: datetime) -> datetime:
    utc_now = now.astimezone(timezone.utc)
    midnight = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=midnight.weekday())


def _empty_state() -> dict[str, Any]:
    return {"schema": "selfheal-state/v1", "nodes": {}}


def _new_node(now: datetime) -> dict[str, Any]:
    return {
        "rung_attempts": {rung: 0 for rung in ("L0", "L1", "L2", "L3")},
        "failed_patch_attempts": 0,
        "demoted_until": None,
        "weekly_budget": {
            "week_start": _week_start(now).isoformat(),
            "auto_patches_applied": 0,
        },
        # Progression metadata is necessary to distinguish L4 from terminal L5
        # while keeping attempt counters limited to the contract's L0..L3.
        "active_incident": None,
        "last_rung": None,
    }


def _validate_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    if state.get("schema") != "selfheal-state/v1":
        raise ValueError("state.schema must equal selfheal-state/v1")
    nodes = state.get("nodes")
    if not isinstance(nodes, dict):
        raise ValueError("state.nodes must be an object")
    for node, node_state in nodes.items():
        _require_node(node)
        if not isinstance(node_state, dict):
            raise ValueError(f"nodes.{node} must be an object")
        attempts = node_state.get("rung_attempts")
        if not isinstance(attempts, dict) or set(attempts) != {"L0", "L1", "L2", "L3"}:
            raise ValueError(f"nodes.{node}.rung_attempts must contain L0, L1, L2, L3")
        for rung, count in attempts.items():
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"nodes.{node}.rung_attempts.{rung} must be a non-negative integer")
        failures = node_state.get("failed_patch_attempts")
        if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
            raise ValueError(f"nodes.{node}.failed_patch_attempts must be a non-negative integer")
        _parse_iso(node_state.get("demoted_until"), f"nodes.{node}.demoted_until", nullable=True)
        budget = node_state.get("weekly_budget")
        if not isinstance(budget, dict):
            raise ValueError(f"nodes.{node}.weekly_budget must be an object")
        _parse_iso(budget.get("week_start"), f"nodes.{node}.weekly_budget.week_start")
        applied = budget.get("auto_patches_applied")
        if isinstance(applied, bool) or not isinstance(applied, int) or applied < 0:
            raise ValueError(
                f"nodes.{node}.weekly_budget.auto_patches_applied must be a non-negative integer"
            )
        active = node_state.get("active_incident")
        if active is not None and (not isinstance(active, str) or not active):
            raise ValueError(f"nodes.{node}.active_incident must be a string or null")
        last = node_state.get("last_rung")
        if last is not None and last not in RUNGS:
            raise ValueError(f"nodes.{node}.last_rung must be a valid rung or null")
    return state


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_state(state_dir: str | Path) -> dict[str, Any]:
    """Load and validate state, returning a fresh empty state when absent."""
    path = Path(state_dir) / _STATE_FILE
    if not path.exists():
        return _empty_state()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return _validate_state(state)
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid self-heal state at {path}: {exc}") from exc


def save_state(state_dir: str | Path, state: dict[str, Any]) -> Path:
    """Validate and atomically persist state at the fixed state filename."""
    validated = _validate_state(state)
    path = Path(state_dir) / _STATE_FILE
    _atomic_json(path, validated)
    return path


def _refresh_time_windows(node_state: dict[str, Any], now: datetime) -> None:
    current_week = _week_start(now)
    stored_week = _parse_iso(
        node_state["weekly_budget"]["week_start"], "weekly_budget.week_start"
    )
    if stored_week != current_week:
        node_state["weekly_budget"] = {
            "week_start": current_week.isoformat(),
            "auto_patches_applied": 0,
        }

    demoted_until = _parse_iso(
        node_state["demoted_until"], "demoted_until", nullable=True
    )
    if demoted_until is not None and now >= demoted_until:
        node_state["demoted_until"] = None
        node_state["failed_patch_attempts"] = 0


def _node_state(state: dict[str, Any], node: str, now: datetime) -> dict[str, Any]:
    _validate_state(state)
    nodes = state["nodes"]
    if node not in nodes:
        nodes[node] = _new_node(now)
    node_state = nodes[node]
    _refresh_time_windows(node_state, now)
    return node_state


def _is_transient(incident: dict[str, Any]) -> bool:
    transient = incident.get("transient")
    if transient is not None and not isinstance(transient, bool):
        raise ValueError("incident.transient must be a boolean")
    return bool(transient) or incident.get("incident_class") == "transient" or incident.get(
        "failure_class"
    ) == "transient"


def next_rung(
    state: dict[str, Any],
    node: str,
    incident: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, str]:
    """Select and record the next eligible rung for one incident.

    One call consumes one rung.  The caller records the rung's actual outcome
    separately, then calls again only when escalation is warranted.
    """
    now = _require_now(now)
    node = _require_node(node)
    if not isinstance(incident, dict):
        raise ValueError("incident must be an object")
    fingerprint = _require_string(incident, "incident_fingerprint")
    fix_class = _require_string(incident, "fix_class")
    candidate = incident.get("playbook_candidate")
    if candidate is not None and (not isinstance(candidate, str) or not candidate):
        raise ValueError("incident.playbook_candidate must be a non-empty string or null")

    node_state = _node_state(state, node, now)
    if node_state["active_incident"] != fingerprint:
        node_state["active_incident"] = fingerprint
        node_state["rung_attempts"] = {rung: 0 for rung in ("L0", "L1", "L2", "L3")}
        node_state["last_rung"] = None

    if fix_class in ALWAYS_HUMAN:
        node_state["last_rung"] = "L5"
        return {
            "rung": "L5",
            "reason": f"fix_class {fix_class} is always-human and cannot enter automated repair",
        }

    budget = node_state["weekly_budget"]["auto_patches_applied"]
    if budget >= WEEKLY_PATCH_BUDGET:
        if node_state["last_rung"] in {"L4", "L5"}:
            node_state["last_rung"] = "L5"
            return {"rung": "L5", "reason": "weekly auto-patch budget breach was contained at L4"}
        node_state["last_rung"] = "L4"
        return {
            "rung": "L4",
            "reason": f"weekly auto-patch budget reached {budget}/{WEEKLY_PATCH_BUDGET}",
        }

    attempts = node_state["rung_attempts"]
    if _is_transient(incident) and attempts["L0"] == 0:
        attempts["L0"] += 1
        node_state["last_rung"] = "L0"
        return {"rung": "L0", "reason": "first sight of transient-class incident; bounded retry"}

    if candidate is not None and attempts["L1"] == 0:
        attempts["L1"] += 1
        node_state["last_rung"] = "L1"
        return {"rung": "L1", "reason": f"matched deterministic playbook {candidate}"}

    demoted_until = node_state["demoted_until"]
    if fix_class in AUTO_FIXABLE and demoted_until is None:
        for rung, reason in (
            ("L2", "auto-fixable class; generate self-patch proposal only"),
            ("L3", "L2 exhausted; generate independent cross-model proposal only"),
        ):
            if attempts[rung] == 0:
                attempts[rung] += 1
                node_state["last_rung"] = rung
                return {"rung": rung, "reason": reason}

    if fix_class in AUTO_FIXABLE and demoted_until is not None:
        skip_reason = f"node is demoted until {demoted_until}; L2/L3 skipped (propose-only tripwire)"
    elif fix_class not in AUTO_FIXABLE:
        skip_reason = f"fix_class {fix_class} is not auto-fixable; L2/L3 skipped"
    else:
        skip_reason = "eligible automated rungs exhausted"

    if node_state["last_rung"] in {"L4", "L5"}:
        node_state["last_rung"] = "L5"
        return {"rung": "L5", "reason": f"L4 containment complete; {skip_reason}"}
    node_state["last_rung"] = "L4"
    return {"rung": "L4", "reason": f"contain and degrade; {skip_reason}"}


def record_patch_outcome(
    state: dict[str, Any], node: str, ok: bool, *, now: datetime
) -> dict[str, Any]:
    """Record an L2/L3 proposal outcome and enforce cumulative demotion.

    A successful proposal does not increment ``auto_patches_applied`` because
    this build is hard-floored to propose-only.  That counter is retained for
    callers operating a future owner-promoted application class.
    """
    now = _require_now(now)
    node = _require_node(node)
    if not isinstance(ok, bool):
        raise ValueError("ok must be a boolean")
    node_state = _node_state(state, node, now)
    if not ok:
        node_state["failed_patch_attempts"] += 1
        if node_state["failed_patch_attempts"] >= DEMOTION_FAILURES:
            node_state["demoted_until"] = (now + timedelta(days=DEMOTION_DAYS)).isoformat()
    return node_state


def propose_patch(
    state_dir: str | Path,
    incident: dict[str, Any],
    rung: str,
    *,
    now: datetime,
) -> Path:
    """Write one immutable, version-numbered, PROPOSE-ONLY change card."""
    now = _require_now(now)
    if not isinstance(incident, dict):
        raise ValueError("incident must be an object")
    if rung not in PATCH_RUNGS:
        raise ValueError("rung must be L2 or L3")
    node = _require_node(incident.get("node"))
    fingerprint = _require_string(incident, "incident_fingerprint")
    fix_class = _require_string(incident, "fix_class")
    if fix_class not in AUTO_FIXABLE:
        raise ValueError(f"fix_class {fix_class} is not auto-fixable")
    diagnosis = _require_string(incident, "diagnosis")
    proposed_action = _require_string(incident, "proposed_action")

    proposal_dir = Path(state_dir) / "heal_proposals"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{now.strftime('%Y%m%d')}-{node}-"
    versions = []
    for path in proposal_dir.glob(f"{prefix}*.json"):
        suffix = path.stem.removeprefix(prefix)
        if suffix.isdigit():
            versions.append(int(suffix))
    version = max(versions, default=0) + 1
    path = proposal_dir / f"{prefix}{version}.json"
    card = {
        "schema": "heal-proposal/v1",
        "rung": rung,
        "node": node,
        "incident_fingerprint": fingerprint,
        "fix_class": fix_class,
        "diagnosis": diagnosis,
        "proposed_action": proposed_action,
        "requires": "full QA + re-shadow + re-pin",
        "auto_apply": False,
        "created_at": now.isoformat(),
    }
    _atomic_json(path, card)
    return path


def build_dossier(
    state_dir: str | Path,
    node: str,
    incidents: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Build the decision-ready L5 packet without writing it."""
    now = _require_now(now)
    node = _require_node(node)
    if not isinstance(incidents, list) or not all(isinstance(item, dict) for item in incidents):
        raise ValueError("incidents must be a list of objects")
    if not isinstance(attempts, list):
        raise ValueError("attempts must be a list")
    checked_attempts = []
    for index, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            raise ValueError(f"attempts[{index}] must be an object")
        for field in ("rung", "reason", "outcome"):
            _require_string(attempt, field)
        if attempt["rung"] not in RUNGS:
            raise ValueError(f"attempts[{index}].rung must be a valid rung")
        checked_attempts.append(copy.deepcopy(attempt))

    state = load_state(state_dir)
    node_state = state["nodes"].get(node)
    current_state = copy.deepcopy(node_state) if node_state is not None else None
    recommended_action = (
        "Review the failed repair evidence and approve one bounded corrective action."
    )
    return {
        "schema": "heal-dossier/v1",
        "node": node,
        "incidents": copy.deepcopy(incidents),
        "attempts": checked_attempts,
        "current_state": current_state,
        "recommended_action": recommended_action,
        "created_at": now.isoformat(),
    }


def write_dossier(state_dir: str | Path, dossier: dict[str, Any]) -> Path:
    """Append one dossier JSON row and fsync it before returning."""
    if not isinstance(dossier, dict):
        raise ValueError("dossier must be an object")
    for field in ("schema", "node", "incidents", "attempts", "current_state", "recommended_action", "created_at"):
        if field not in dossier:
            raise ValueError(f"dossier.{field} is required")
    if dossier["schema"] != "heal-dossier/v1":
        raise ValueError("dossier.schema must equal heal-dossier/v1")
    _require_node(dossier["node"])
    _require_string(dossier, "recommended_action")
    _parse_iso(dossier["created_at"], "dossier.created_at")

    path = Path(state_dir) / "heal_dossiers.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = (json.dumps(dossier, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        written = 0
        while written < len(row):
            written += os.write(descriptor, row[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path
