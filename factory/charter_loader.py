"""Validated charter loader — the single way runtime code reads a charter.

The charter is the department's source of truth (human-owned, read-only to the
department). This loader parses it ONCE, validates the fields runtime code
depends on, and fails closed: a missing/unparseable charter or a missing
required key raises instead of silently applying a default that the owner never
approved. Factory defaults exist only for tuning knobs explicitly marked
optional.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard, not logic
    yaml = None


# Optional tuning knobs (charter may omit these; owner-approved defaults apply).
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "weekly_touch_ceiling": 300,
    "pace_ceiling_near_frac": 0.9,
    "faux_work_touch_floor": 50,
    "backlog_aging_min": 1,
    "budget_near_frac": 0.8,
}

DEFAULT_BUDGET_CEILINGS: dict[str, Any] = {
    "model_calls": 900,
    "dollars": 40,
    "worker_minutes": 1200,
}

# Required top-level keys. A charter without them is not a charter.
REQUIRED_KEYS = ("department", "owner", "autonomy_state", "immutable_safety_invariants")

VALID_AUTONOMY_STATES = ("shadow", "draft_only", "gated_live", "autonomous")


class CharterError(RuntimeError):
    """The charter is missing, unparseable, or invalid. Fail closed."""


def load_charter(path, expect_department=None) -> dict:
    """Parse + validate a charter.yaml. Raises CharterError on any problem.
    Pass expect_department (normally the directory name) so a charter cannot
    silently govern a department it does not name."""
    if yaml is None:
        raise CharterError("PyYAML is required to load charters (pip install pyyaml)")
    path = Path(path)
    if not path.exists():
        raise CharterError(f"charter not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CharterError(f"charter is not valid YAML: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CharterError(f"charter must be a mapping: {path}")
    for key in REQUIRED_KEYS:
        if key not in data:
            raise CharterError(f"charter missing required key '{key}': {path}")
    state = data.get("autonomy_state")
    if state not in VALID_AUTONOMY_STATES:
        raise CharterError(
            f"charter autonomy_state {state!r} not in {VALID_AUTONOMY_STATES}: {path}"
        )
    invariants = (data.get("immutable_safety_invariants") or {}).get("heal_may_not_modify")
    if not invariants:
        raise CharterError(f"charter immutable_safety_invariants.heal_may_not_modify empty: {path}")
    if expect_department is not None and data.get("department") != expect_department:
        raise CharterError(
            f"charter names department {data.get('department')!r} but lives in "
            f"'{expect_department}': {path}")
    return data


def thresholds(charter: dict) -> dict:
    """Manager thresholds: charter values win; factory defaults fill gaps.
    Budget ceilings ride inside the thresholds dict (manager contract)."""
    out = dict(DEFAULT_THRESHOLDS)
    out.update(charter.get("thresholds") or {})
    ceilings = dict(DEFAULT_BUDGET_CEILINGS)
    ceilings.update((charter.get("budget") or {}).get("weekly_ceilings") or {})
    out["budget_ceilings"] = ceilings
    return out


def funnel_config(charter: dict) -> dict | None:
    """Return the optional, strictly validated machine-readable funnel config."""
    if "funnel" not in charter:
        return None
    funnel = charter["funnel"]
    if not isinstance(funnel, dict) or set(funnel) != {"end_goal", "transitions"}:
        raise CharterError("charter funnel keys must be exactly end_goal and transitions")
    end_goal = funnel["end_goal"]
    if not isinstance(end_goal, dict) or set(end_goal) != {"stage", "per_week"}:
        raise CharterError("charter funnel.end_goal keys must be exactly stage and per_week")
    if not isinstance(end_goal["stage"], str) or not end_goal["stage"].strip():
        raise CharterError("charter funnel.end_goal.stage must be a non-empty string")
    per_week = end_goal["per_week"]
    if isinstance(per_week, bool) or not isinstance(per_week, int) or per_week < 0:
        raise CharterError("charter funnel.end_goal.per_week must be a non-negative integer")
    transitions = funnel["transitions"]
    if not isinstance(transitions, list) or not transitions:
        raise CharterError("charter funnel.transitions must be a non-empty list")
    expected = {"from", "to", "prior_rate", "buffer", "lead_days", "maturity_days", "stock_buffer"}
    for index, row in enumerate(transitions):
        if not isinstance(row, dict) or set(row) != expected:
            raise CharterError(f"charter funnel.transitions[{index}] has unknown or missing keys")
        for key in ("from", "to"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise CharterError(f"charter funnel.transitions[{index}].{key} must be a non-empty string")
        rate = row["prior_rate"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= 1:
            raise CharterError(f"charter funnel.transitions[{index}].prior_rate must be in (0, 1]")
        for key in ("buffer", "stock_buffer"):
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise CharterError(f"charter funnel.transitions[{index}].{key} must be non-negative")
        for key in ("lead_days", "maturity_days"):
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CharterError(f"charter funnel.transitions[{index}].{key} must be a non-negative integer")
    # ORDER CONTRACT (fail-closed): the list is upstream-first — each `to`
    # must be the next transition's `from`, and the last `to` must be the
    # end_goal stage. A broken chain would compile silently WRONG floors
    # (caught live 2026-08-06: a downstream-first list inverted the cascade).
    for index in range(len(transitions) - 1):
        if transitions[index]["to"] != transitions[index + 1]["from"]:
            raise CharterError(
                f"charter funnel.transitions[{index}].to "
                f"({transitions[index]['to']!r}) must equal "
                f"transitions[{index + 1}].from ({transitions[index + 1]['from']!r}) "
                "— the list is upstream-first and must chain without breaks")
    if transitions[-1]["to"] != end_goal["stage"]:
        raise CharterError(
            f"charter funnel.transitions[-1].to ({transitions[-1]['to']!r}) "
            f"must equal end_goal.stage ({end_goal['stage']!r})")
    return funnel


def immutable_invariants(charter: dict) -> frozenset[str]:
    return frozenset(charter["immutable_safety_invariants"]["heal_may_not_modify"])


def autonomy_state(charter: dict) -> str:
    return charter["autonomy_state"]


def engine_allowlist(charter: dict) -> frozenset[str]:
    """Return the charter-owned model engine allowlist, with no fallback."""
    try:
        value = charter["budget"]["engine_allowlist"]
    except (KeyError, TypeError) as exc:
        raise CharterError("charter missing required key 'budget.engine_allowlist'") from exc
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(engine, str) or not engine.strip() for engine in value)
    ):
        raise CharterError(
            "charter budget.engine_allowlist must be a non-empty list of engine names"
        )
    return frozenset(engine.strip() for engine in value)


def max_edit_rounds(charter: dict) -> int:
    """Return the charter-owned edit-round ceiling, with no fallback."""
    try:
        value = charter["qa_shape"]["max_edit_rounds"]
    except (KeyError, TypeError) as exc:
        raise CharterError("charter missing required key 'qa_shape.max_edit_rounds'") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CharterError(
            "charter qa_shape.max_edit_rounds must be a non-negative integer"
        )
    return value


def human_gates(charter: dict) -> frozenset[str]:
    """Action classes that always require a human decision. The factory floor
    (external effects + governance) applies even if the charter lists fewer."""
    floor = {"external_send", "crm_write", "ehr_write", "finance_write", "publish",
             "spend_over_ceiling", "charter_change", "promotion"}
    listed = set((charter.get("escalation") or {}).get("human_gates") or [])
    return frozenset(floor | listed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a department charter")
    parser.add_argument("--charter", required=True)
    args = parser.parse_args()
    charter = load_charter(args.charter)
    print(json.dumps({
        "ok": True,
        "department": charter["department"],
        "autonomy_state": autonomy_state(charter),
        "thresholds": thresholds(charter),
        "human_gates": sorted(human_gates(charter)),
    }, indent=2))


if __name__ == "__main__":
    main()
