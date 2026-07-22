"""Factory scaffold (F0): hand-stand a new department.

Creates the standard department skeleton and wires it to every factory-standard
component (department manager, heal ladder, release-pinning, human-in-the-loop
bridge, runtime kernel). What it does NOT do — deliberately — is invent the
department's intent: the charter is a template that names the F1 human step
(the owner's intent lock), because the setpoints, funnels, and node logic are
the domain-specific judgment the factory interview exists to capture. The
scaffold makes everything around that human step mechanical.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Safe slug: filesystem-, YAML-, and systemd-unit-safe. Rejects path traversal,
# quotes, spaces, and shell metacharacters by construction (Codex review #18).
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")


_CHARTER_TEMPLATE = """# {name} department charter — TEMPLATE (F0 scaffold)
#
# F1 INTENT LOCK REQUIRED (human step, owner): the fields below are placeholders.
# Standing this department means running the intent interview
# (interview/INTERVIEW.md) and replacing every TODO with the department's real
# intent. Nothing here is fabricated for you — the factory captures the owner's
# judgment, it does not guess it.
#
# This charter is a GOVERNANCE FILE: human-owned, READ-ONLY to the department at
# every autonomy level. Heals and managers may never edit it.
department: {name}
version: v0.1
status: F0_scaffold_awaiting_intent_lock
owner: {owner}
mission: TODO_F1

autonomy_state: shadow   # every department starts in shadow (earned-autonomy ladder)

setpoints:
  operational: {{metric: TODO_define_in_F1, target: 0}}
  outcome: {{metric: TODO_define_in_F1, target: TBD_MEASURE_IN_SHADOW}}

# Deterministic manager thresholds. The manager loads THESE (charter is the
# source of truth); factory defaults apply only when a key is absent.
thresholds:
  weekly_touch_ceiling: 300
  pace_ceiling_near_frac: 0.9
  faux_work_touch_floor: 50
  backlog_aging_min: 1
  budget_near_frac: 0.8

budget:
  weekly_ceilings:
    model_calls: 900
    dollars: 40
    worker_minutes: 1200

# Safety floors inherited from the factory standard; MUST NOT be weakened by a
# heal. Fill department-specific funnels/subgraphs during F2 after intent lock.
immutable_safety_invariants:
  heal_may_not_modify:
    - delivery_floor
    - send_authorization
    - eligibility_allowlist
    - frequency_policy
    - privacy_floor
    - kill_controller
    - circuit_breaker
    - promotion_contract
    - budget_ceilings
    - identity_resolution
    - metric_definitions
    - gateway_mode
    - autonomy_state

escalation:
  default: fully_automated_with_escalation   # run without a human until a gate or breach
  human_gates:      # action classes that ALWAYS require a human decision
    - external_send
    - crm_write
    - publish
    - spend_over_ceiling
    - charter_change
    - promotion

kill_if: []          # TODO F1: kill (not pause) conditions in the owner's words

funnels:
  entries: []        # TODO F1/F2: governed subgraphs (see templates/subgraphs.json.tmpl)

memory:
  local: departments/{name}/state
  backends: []       # optional durable backends (s3, open_brain); local is always on
"""

_RUNTIME_README = """# {name} runtime

This department uses the FACTORY-STANDARD components (no per-department copies):
- department manager loop: factory/manager.py (run with --department {name})
- self-heal ladder: factory/heal_ladder.py
- human-in-the-loop bridge: factory/human_in_the_loop.py
- estate watchdog: factory/estate_manager.py
- runtime enforcement kernel: kernel/ (wire via a thin kernel bridge)
- release-pinning: factory/release.py

F1 (human, owner): run the intent interview (interview/INTERVIEW.md), lock the
intent, then author the charter setpoints + funnel subgraphs. F2-F4 then govern
and hand-author the runtime nodes from the procedural graph, shadow, and pin a
release. Department-SPECIFIC node code lives here; factory machinery does not.
"""


def scaffold_department(name: str, root=".", owner: str = "owner") -> dict:
    """Create the standard department skeleton. Returns a summary including a
    factory-standard registry entry, which is also persisted to
    estate/registry.d/<name>.yaml when that directory exists.

    Refuses an invalid name and refuses to overwrite an existing charter —
    the charter is a human governance file once F1 has touched it."""
    if not _NAME_RE.match(name):
        raise ValueError(
            f"invalid department name {name!r}: use a lowercase slug "
            "(letters, digits, '-', '_'; 2-41 chars)")
    if not _NAME_RE.match(owner) and not re.match(r"^[A-Za-z][A-Za-z0-9 ._-]{0,60}$", owner):
        raise ValueError(f"invalid owner {owner!r}")
    root = Path(root)
    dept = root / "departments" / name
    if (dept / "charter.yaml").exists():
        raise FileExistsError(
            f"department '{name}' already has a charter — refusing to overwrite "
            "a governance file (delete it deliberately if you really mean to)")
    (dept / "state").mkdir(parents=True, exist_ok=True)
    (dept / "runtime").mkdir(parents=True, exist_ok=True)
    (dept / "interview").mkdir(parents=True, exist_ok=True)
    (dept / "knowledge").mkdir(parents=True, exist_ok=True)
    (dept / "charter.yaml").write_text(
        _CHARTER_TEMPLATE.format(name=name, owner=owner), encoding="utf-8"
    )
    (dept / "runtime" / "README.md").write_text(
        _RUNTIME_README.format(name=name), encoding="utf-8"
    )

    registry_entry = {
        "id": name,
        "owner": owner,
        "surface": "department",
        "schedule": "TODO_F1",
        "health_check": f"test -f departments/{name}/state/STATE.json",
        "heartbeat_path": f"departments/{name}/state/heartbeats.jsonl",
        "state_dir": f"departments/{name}/state",
        "kill_switch": f"systemctl --user disable --now {name}-loop.timer",
    }

    # Persist the registry partition so the estate watchdog actually sees the
    # new department (Codex review #18: a returned-but-unpersisted entry is a
    # registration that never happens). One file per department, refuse clobber.
    registry_dir = root / "estate" / "registry.d"
    registry_file = None
    if registry_dir.is_dir() and not (registry_dir / f"{name}.yaml").exists():
        lines = ["entries:", f"  - id: {name}"]
        for key in ("owner", "surface", "schedule", "health_check",
                    "heartbeat_path", "state_dir", "kill_switch"):
            lines.append(f"    {key}: {json.dumps(registry_entry[key])}")
        (registry_dir / f"{name}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        registry_file = str(registry_dir / f"{name}.yaml")

    return {
        "department": name,
        "created": [str(dept / "charter.yaml"), str(dept / "state"), str(dept / "runtime")],
        "registry_entry": registry_entry,
        "registry_file": registry_file,
        "next_human_step": (
            "F1 intent interview + intent lock (owner), then F2 charter setpoints + funnels"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scaffold a new factory-standard department (F0)")
    parser.add_argument("--name", required=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--owner", default="owner")
    args = parser.parse_args()
    print(json.dumps(scaffold_department(args.name, args.root, owner=args.owner), indent=2))


if __name__ == "__main__":
    main()
