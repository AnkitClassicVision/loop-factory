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

_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"

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


def _template(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _replace(text: str, **values: str) -> str:
    """Replace only declared template tokens; YAML braces remain literal."""
    for key, value in values.items():
        doubled = "{{" + key + "}}"
        if doubled in text:
            text = text.replace(doubled, value)
        else:
            text = text.replace("{" + key + "}", value)
    return text


def _shell_double_quoted(value: Path) -> str:
    """Escape a path for insertion inside a shell double-quoted string."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )


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
    charter_path = dept / "charter.yaml"
    eval_registry_path = dept / "runtime" / "eval_registry.yaml"
    runtime_node_path = dept / "runtime" / "runtime_node.py"
    engines_path = dept / "runtime" / "engines.example.yaml"
    daily_path = dept / "runtime" / f"{name}_daily.sh"

    charter_path.write_text(
        _replace(_template("charter.yaml.template"), name=name, owner=owner),
        encoding="utf-8",
    )
    (dept / "runtime" / "README.md").write_text(
        _RUNTIME_README.format(name=name), encoding="utf-8"
    )
    eval_registry_path.write_text(
        _replace(_template("eval-registry.yaml.template"), DEPARTMENT=name),
        encoding="utf-8",
    )
    runtime_node_path.write_text(
        _replace(_template("runtime-node.py.template"), DEPARTMENT=name),
        encoding="utf-8",
    )
    engines_path.write_text(_template("engines.yaml.template"), encoding="utf-8")
    daily_path.write_text(
        _replace(
            _template("department_daily.sh.template"),
            DEPARTMENT=name,
            REPO=_shell_double_quoted(root.resolve()),
        ),
        encoding="utf-8",
    )
    daily_path.chmod(0o755)

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
        "created": [
            str(charter_path),
            str(dept / "state"),
            str(dept / "runtime"),
            str(eval_registry_path),
            str(runtime_node_path),
            str(engines_path),
            str(daily_path),
        ],
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
