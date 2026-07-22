"""Knowledge-graph QA layer: procedural-map lint, node↔artifact traceability,
and the process-change gate.

The factory's design-time source of truth is a pair of maps per department:
the CONCEPT map (why — human-readable, locked at F1) and the PROCEDURAL graph
(what — `procedural-graph.md` for humans, `subgraphs.json` for machines). This
module is the DETERMINISTIC control over the machine half:

1. `validate_subgraphs` — guard-matrix ordering lint over every subgraph.
2. `check_traceability` — every runtime artifact traces to a graph node, and
   every node impl exists on disk. An untraced file must be explicitly
   allowlisted with a rationale (`untraced_allowed`), never silently ignored.
3. `check_drift` — the live tree is compared to the pinned `current` release;
   a changed process (runtime code or graph) that was not re-pinned through
   the process-change runbook is flagged.

SCOPE / HONESTY: the lint checks METADATA ORDERING ONLY. It proves that
capability labels appear in the required order in a flat node list. It cannot
detect a mistagged model call, a hollow guard, or a guard bypassed on a retry
path. THE REAL SAFETY GUARANTEE IS RUNTIME MEDIATION (kernel/ gateways, which
fail closed) — this file is CI defense-in-depth that catches honest authoring
mistakes early. It never by itself qualifies a funnel for promotion.

Shared Safety Layer guard vocabulary (factory standard):
  S1 resolve_identity · S2 eligibility_gate · S3 privacy_preflight ·
  S4 send_authorization · S5 frequency_reserve · S6 kill_controller ·
  S7 circuit_breaker · S8 budget_reserve · crm_auth crm_authorization

Subgraph JSON schema (departments/<dept>/subgraphs.json):
{
  "subgraphs": [
    {
      "id": "SG-EXAMPLE",
      "not_applicable": {"S7": "rationale", ...},
      "nodes": [
        {"id": "S1", "guard": "S1"},
        {"id": "N4", "model_capable": true, "cost_incurring": true, "impl": "runtime/draft.py"},
        {"id": "N6", "external_dispatch": true},
        {"id": "N9", "crm_write": true}
      ]
    }
  ],
  "untraced_allowed": {"runtime/kernel_bridge.py": "factory-standard wiring, not a node"}
}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SEND_ORDER = ["S1", "S2", "S3", "S5", "S4"]  # required, in this relative order, before dispatch
HEALTH = {"S6", "S7"}                          # controller health present before dispatch
ALL_GUARDS = {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "crm_auth"}
SEND_ONLY = {"S4", "S5", "S6", "S7"}           # a read-only funnel may mark these not_applicable

# Files in runtime/ that never need a graph node.
_ALWAYS_UNTRACED = {"__init__.py", "README.md"}


def _guards_before(nodes, idx):
    return [n.get("guard") for n in nodes[:idx] if n.get("guard")]


def validate_subgraph(sg) -> list[str]:
    """Guard-matrix ordering lint for one subgraph. Returns failure strings."""
    fails = []
    nodes = sg.get("nodes", [])
    na = sg.get("not_applicable", {})
    sid = sg.get("id", "?")
    guards_present = {n.get("guard") for n in nodes if n.get("guard")}
    has_dispatch = any(n.get("external_dispatch") for n in nodes)
    has_crm = any(n.get("crm_write") for n in nodes)

    for i, node in enumerate(nodes):
        before = _guards_before(nodes, i)
        nid = node.get("id", f"#{i}")
        if node.get("model_capable") and "S3" not in before:
            fails.append(f"{sid}/{nid}: model-capable node not preceded by S3 privacy_preflight")
        if node.get("cost_incurring") and "S8" not in before:
            fails.append(f"{sid}/{nid}: cost-incurring node not preceded by S8 budget_reserve")
        if node.get("external_dispatch"):
            for g in SEND_ORDER:
                if g not in before:
                    fails.append(f"{sid}/{nid}: external dispatch missing prior {g}")
            positions = [before.index(g) for g in SEND_ORDER if g in before]
            if positions != sorted(positions):
                fails.append(f"{sid}/{nid}: send guards out of order (need S1,S2,S3,S5,S4)")
            if not (HEALTH & set(before)):
                fails.append(f"{sid}/{nid}: external dispatch missing fresh S6/S7 controller health")
        if node.get("crm_write") and "crm_auth" not in before:
            fails.append(f"{sid}/{nid}: CRM write not preceded by crm_authorization")

    for g in ("S1", "S2", "S3", "S8"):
        if g not in guards_present and g not in na:
            fails.append(f"{sid}: universal guard {g} neither present nor marked not_applicable")
    if has_dispatch:
        for g in SEND_ONLY:
            if g not in guards_present and g not in na:
                fails.append(f"{sid}: has dispatch but {g} neither present nor justified")
    else:
        for g in SEND_ONLY:
            if g in guards_present:
                continue
            if g not in na:
                fails.append(f"{sid}: read-only funnel must mark {g} not_applicable with rationale")
    if has_crm and "crm_auth" not in guards_present:
        fails.append(f"{sid}: has CRM write but no crm_authorization node")
    for g, reason in na.items():
        if not reason or not str(reason).strip():
            fails.append(f"{sid}: not_applicable[{g}] has empty rationale")
    return fails


def validate_subgraphs(data) -> list[str]:
    subgraphs = data.get("subgraphs", [])
    if not subgraphs:
        return ["no subgraphs defined"]
    fails: list[str] = []
    for sg in subgraphs:
        fails.extend(validate_subgraph(sg))
    return fails


def check_traceability(dept_dir) -> list[str]:
    """Every runtime artifact traces to a graph node; every declared node impl
    exists. Untraced files must be allowlisted with a rationale."""
    dept_dir = Path(dept_dir)
    sub_path = dept_dir / "subgraphs.json"
    if not sub_path.exists():
        return [f"missing {sub_path.name} — the procedural graph has no machine form"]
    try:
        data = json.loads(sub_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{sub_path.name} is not valid JSON: {exc}"]

    fails: list[str] = []
    declared_impls: set[str] = set()
    for sg in data.get("subgraphs", []):
        for node in sg.get("nodes", []):
            impl = node.get("impl")
            if not impl:
                continue
            declared_impls.add(impl)
            if not (dept_dir / impl).exists():
                fails.append(
                    f"{sg.get('id','?')}/{node.get('id','?')}: impl '{impl}' does not exist on disk")

    allowlist = data.get("untraced_allowed", {})
    for f, reason in allowlist.items():
        if not reason or not str(reason).strip():
            fails.append(f"untraced_allowed['{f}'] has empty rationale")

    runtime_dir = dept_dir / "runtime"
    if runtime_dir.is_dir():
        for path in sorted(runtime_dir.glob("*.py")):
            rel = f"runtime/{path.name}"
            if path.name in _ALWAYS_UNTRACED:
                continue
            if rel not in declared_impls and rel not in allowlist:
                fails.append(
                    f"runtime artifact '{rel}' traces to no graph node "
                    f"(add a node impl or an untraced_allowed rationale)")
    return fails


def check_drift(dept_dir, release_root) -> dict:
    """Compare the live tree to the pinned `current` release. A mismatch means
    the process changed without going through the process-change runbook
    (patch graph -> lint -> re-shadow -> re-pin)."""
    import importlib.util

    release_root = Path(release_root)
    rel_path = Path(__file__).resolve().parent / "release.py"
    spec = importlib.util.spec_from_file_location("release", rel_path)
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)

    current = release.read_current(release_root)
    if current is None:
        return {"ok": False, "reason": "no release pinned yet", "mismatches": []}
    verdict = release.verify_release(dept_dir, release_root / current)
    if verdict["ok"]:
        return {"ok": True, "current": current, "mismatches": []}
    return {
        "ok": False,
        "current": current,
        "reason": "live tree differs from the pinned release — process changed "
                  "without re-pin (run the process-change runbook)",
        "mismatches": verdict["mismatches"],
    }


def qa(dept_dir, release_root=None) -> dict:
    """The full deterministic map-QA pass for one department."""
    dept_dir = Path(dept_dir)
    result: dict = {"department": dept_dir.name, "ok": True, "lint": [], "traceability": [],
                    "drift": None}
    sub_path = dept_dir / "subgraphs.json"
    if sub_path.exists():
        try:
            data = json.loads(sub_path.read_text(encoding="utf-8"))
            result["lint"] = validate_subgraphs(data)
        except ValueError as exc:
            result["lint"] = [f"subgraphs.json is not valid JSON: {exc}"]
    else:
        result["lint"] = ["missing subgraphs.json"]
    result["traceability"] = check_traceability(dept_dir)
    if release_root is not None:
        result["drift"] = check_drift(dept_dir, release_root)
        if not result["drift"]["ok"]:
            result["ok"] = False
    if result["lint"] or result["traceability"]:
        result["ok"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Procedural-map QA: guard-matrix lint + traceability + release drift")
    parser.add_argument("--dept", required=True, help="department directory")
    parser.add_argument("--releases", default=None, help="release root (enables drift check)")
    args = parser.parse_args()
    verdict = qa(args.dept, release_root=args.releases)
    print(json.dumps(verdict, indent=2))
    if verdict["ok"]:
        print("PASS: metadata ordering + traceability only — runtime gateways enforce safety",
              file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
