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


def immutable_invariants(charter: dict) -> frozenset[str]:
    return frozenset(charter["immutable_safety_invariants"]["heal_may_not_modify"])


def autonomy_state(charter: dict) -> str:
    return charter["autonomy_state"]


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
