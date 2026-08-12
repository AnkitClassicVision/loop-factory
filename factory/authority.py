"""Validate the one-way Factory authority map for a department.

The map is intentionally data-only: it assigns exactly one owner and one proof
contract to every permission class without granting runtime authority itself.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA = "authority-map/v1"
ACTIONS = ("observe", "plan", "approve", "execute", "verify")
_EXPECTED = {
    "observe": {
        "actor": "factory_supervisor",
        "authority": "observe",
        "external_effect": False,
        "proof": "signed_observation",
    },
    "plan": {
        "actor": "direct_worker",
        "authority": "draft",
        "external_effect": False,
        "proof": "release_bound_proposal",
    },
    "approve": {
        "actor": "human_gate",
        "authority": "approve",
        "external_effect": False,
        "proof": "signed_human_decision",
    },
    "execute": {
        "actor": "dedicated_executor",
        "authority": "execute",
        "external_effect": True,
        "proof": "target_readback",
    },
    "verify": {
        "actor": "independent_verifier",
        "authority": "verify",
        "external_effect": False,
        "proof": "target_readback",
    },
}


class AuthorityMapError(ValueError):
    """An authority assignment is malformed, ambiguous, or unsafe."""


def _require_nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthorityMapError(f"{field} must be a nonempty string")
    return value.strip()


def validate(value: Any, *, department: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "department", "actions"}:
        raise AuthorityMapError("authority map must contain exactly schema, department, actions")
    if value.get("schema") != SCHEMA or value.get("department") != department:
        raise AuthorityMapError("authority map schema or department is invalid")
    actions = value.get("actions")
    if not isinstance(actions, list) or len(actions) != len(ACTIONS):
        raise AuthorityMapError("authority map must assign each required action exactly once")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(actions):
        field = f"actions[{index}]"
        if not isinstance(entry, dict) or set(entry) != {
            "action", "owner", "actor", "authority", "proof", "external_effect", "approval_required"
        }:
            raise AuthorityMapError(f"{field} has an invalid shape")
        action = _require_nonempty(entry.get("action"), f"{field}.action")
        owner = _require_nonempty(entry.get("owner"), f"{field}.owner")
        if action not in _EXPECTED or action in seen:
            raise AuthorityMapError(f"{field}.action is unknown or duplicated")
        expected = _EXPECTED[action]
        for key in ("actor", "authority", "proof", "external_effect"):
            if entry.get(key) != expected[key]:
                raise AuthorityMapError(f"{field}.{key} violates the {action} boundary")
        if not isinstance(entry["approval_required"], bool):
            raise AuthorityMapError(f"{field}.approval_required must be boolean")
        if action == "execute" and entry["approval_required"] is not True:
            raise AuthorityMapError("execute requires an explicit human approval")
        if action != "execute" and entry["approval_required"] is not False:
            raise AuthorityMapError(f"{action} must not claim approval authority")
        seen.add(action)
        normalized.append({
            "action": action,
            "owner": owner,
            "actor": expected["actor"],
            "authority": expected["authority"],
            "proof": expected["proof"],
            "external_effect": expected["external_effect"],
            "approval_required": entry["approval_required"],
        })
    if set(seen) != set(ACTIONS):
        raise AuthorityMapError("authority map has a missing required action")
    return {"schema": SCHEMA, "department": department, "actions": normalized}


def load(path: str | Path, *, department: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise AuthorityMapError(f"authority map unreadable: {exc}") from exc
    return validate(value, department=department)
