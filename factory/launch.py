"""Department process launcher: the place capability confinement is APPLIED.

`kernel/capabilities.py` defines the allowlist env; this launcher is what
actually invokes it (Codex review #17: a scrubber nobody calls scrubs
nothing). It builds the clean department environment — every credential-class
variable dropped, kernel markers set — asserts the result, then execs the
department command under it.

Usage:
  python3 factory/launch.py --department demo -- python3 departments/demo/runtime/loop.py
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _capabilities():
    spec = importlib.util.spec_from_file_location(
        "capabilities", ROOT / "kernel" / "capabilities.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _charter_loader():
    spec = importlib.util.spec_from_file_location(
        "charter_loader", ROOT / "factory" / "charter_loader.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _department_capabilities(department, root=None):
    repo_root = Path(root) if root is not None else ROOT
    charter_path = repo_root / "departments" / department / "charter.yaml"
    loader = _charter_loader()
    charter = loader.load_charter(charter_path, expect_department=department)
    declared = charter.get("capabilities", ())
    if not isinstance(declared, (list, tuple)):
        raise loader.CharterError(
            f"charter capabilities must be a list: {charter_path}")
    return tuple(declared)


def build_env(base=None, *, department=None, root=None):
    capabilities = (
        _department_capabilities(department, root=root)
        if department is not None
        else ()
    )
    caps = _capabilities()
    env = caps.department_env(
        dict(base if base is not None else os.environ), capabilities=capabilities)
    caps.assert_no_ambient_credentials(env, capabilities=capabilities)
    return env


class LaunchRefused(RuntimeError):
    """Raised before execution when a capability-bearing command is untrusted."""


def _assert_capability_command(
    department: str,
    command: list[str],
    capabilities: tuple[str, ...],
    *,
    root=None,
) -> None:
    if not capabilities:
        return
    repo_root = Path(root) if root is not None else ROOT
    executable = Path(command[0])
    python_allowed = (
        executable.name == "python3"
        or re.fullmatch(r"python3\.\d+", executable.name) is not None
        or executable.resolve() == Path(sys.executable).resolve()
    )
    if not python_allowed:
        raise LaunchRefused(
            "capability-bearing departments may launch only python3 runtime nodes"
        )
    if len(command) < 2 or command[1].startswith("-"):
        raise LaunchRefused(
            "capability-bearing departments must execute a runtime script, not a Python option"
        )
    runtime_dir = (repo_root / "departments" / department / "runtime").resolve()
    script = Path(command[1])
    if not script.is_absolute():
        script = repo_root / script
    script = script.resolve()
    if not script.is_file() or not script.is_relative_to(runtime_dir):
        raise LaunchRefused(
            f"capability-bearing command script must resolve inside {runtime_dir}"
        )


def launch_command(
    department: str,
    command: list[str],
    *,
    base=None,
    root=None,
    runner=subprocess.run,
) -> int:
    capabilities = _department_capabilities(department, root=root)
    _assert_capability_command(
        department, command, capabilities, root=root
    )
    env = build_env(base, department=department, root=root)
    env["OE_DEPARTMENT"] = department
    return runner(command, env=env).returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a department command with the confined (no-credential) environment")
    parser.add_argument("--department", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="the department command, after --")
    args = parser.parse_args()
    command = [c for c in args.command if c != "--"]
    if not command:
        print("no command given", file=sys.stderr)
        return 2
    try:
        return launch_command(args.department, command)
    except LaunchRefused as exc:
        print(f"launch refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
