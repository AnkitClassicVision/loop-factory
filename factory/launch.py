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
    env = build_env(department=args.department)
    env["OE_DEPARTMENT"] = args.department
    return subprocess.run(command, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
