"""Podcast runtime kernel bridge: thin factory-standard wiring.

The ONE way the podcast department obtains its enforcement kernel. A department
never constructs gateways/signers/ledgers itself (kernel/bridge.py contract) —
it calls get_kernel(state_dir) and receives a LockService whose ledgers live
under its own state directory, with budget ceilings sourced from the department
charter (budget.weekly_ceilings, the source of truth) via factory/charter_loader.

Also exposes require_shadow(): a belt-and-suspenders guard for the shadow-first
rule (AGENTS.md #1) — a caller that requests a live action while the charter's
autonomy_state is still 'shadow' is refused before any side effect.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_DEPT_DIR = _HERE.parent.parent                  # departments/podcast
_DEPARTMENT = _DEPT_DIR.name                     # "podcast"
_REPO = _DEPT_DIR.parent.parent                  # departments/podcast -> repo root
_CHARTER_PATH = _DEPT_DIR / "charter.yaml"       # departments/podcast/charter.yaml


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


def _charter_loader():
    return _load_module("charter_loader", _REPO / "factory" / "charter_loader.py")


def _bridge():
    return _load_module("bridge", _REPO / "kernel" / "bridge.py")


def _load_charter():
    return _charter_loader().load_charter(_CHARTER_PATH, expect_department=_DEPARTMENT)


def get_kernel(state_dir):
    """Return the podcast department's enforcement kernel (a LockService).

    Budget ceilings come from the charter (budget.weekly_ceilings) via the
    factory charter loader; ledgers live under <state_dir>/kernel/.
    """
    ceilings = _charter_loader().thresholds(_load_charter())["budget_ceilings"]
    return _bridge().load_kernel(state_dir, budget_ceilings=ceilings)


def require_shadow(live: bool = False) -> None:
    """Belt-and-suspenders for the shadow-first rule: refuse a live request
    while the charter's autonomy_state is still 'shadow' (the department has not
    earned an owner-approved promotion). A no-op when no live action is requested.
    """
    if not live:
        return
    state = _charter_loader().autonomy_state(_load_charter())
    if state == "shadow":
        raise RuntimeError(
            f"{_DEPARTMENT} autonomy_state is 'shadow'; a live action is not "
            "permitted until the department earns an owner-approved promotion"
        )
