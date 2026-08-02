#!/usr/bin/env python3
"""Install the loop-factory estate units, display-only unless --apply is set."""
from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path


CANONICAL_ROOT = Path("/mnt/d_drive/repos/loop-factory")
UNIT_NAMES = (
    "loop-factory-estate.service",
    "loop-factory-estate.timer",
    "loop-factory-estate-deadman.service",
    "loop-factory-estate-deadman.timer",
)


def install_commands(repo_root: Path, user_home: Path) -> list[list[str]]:
    unit_source = repo_root / "deploy" / "systemd"
    unit_target = user_home / ".config" / "systemd" / "user"
    commands: list[list[str]] = [
        [
            "/usr/bin/env", "python3", str(repo_root / "factory" / "estate_deadman.py"),
            "--registry-dir", str(repo_root / "estate" / "registry.d"),
            "--self-test-poisoned-registry",
        ],
        ["systemd-analyze", "verify", *(str(unit_source / name) for name in UNIT_NAMES)],
        ["install", "-d", "-m", "0755", str(unit_target)],
        ["install", "-d", "-m", "0755", str(repo_root / "estate" / "state")],
        ["install", "-d", "-m", "0755", str(repo_root / "state")],
    ]
    commands.extend(
        ["install", "-m", "0644", str(unit_source / name), str(unit_target / name)]
        for name in UNIT_NAMES
    )
    commands.extend([
        ["systemctl", "--user", "daemon-reload"],
        [
            "systemctl", "--user", "enable", "--now",
            "loop-factory-estate.timer", "loop-factory-estate-deadman.timer",
        ],
        ["systemctl", "--user", "start", "loop-factory-estate.service"],
        ["systemctl", "--user", "start", "loop-factory-estate-deadman.service"],
        [
            "systemctl", "--user", "status",
            "loop-factory-estate.timer", "loop-factory-estate-deadman.timer", "--no-pager",
        ],
        [
            "systemctl", "--user", "list-timers",
            "loop-factory-estate.timer", "loop-factory-estate-deadman.timer", "--no-pager",
        ],
    ])
    return commands


def rollback_commands(user_home: Path) -> list[list[str]]:
    unit_target = user_home / ".config" / "systemd" / "user"
    return [
        [
            "systemctl", "--user", "disable", "--now",
            "loop-factory-estate.timer", "loop-factory-estate-deadman.timer",
        ],
        [
            "systemctl", "--user", "stop",
            "loop-factory-estate.service", "loop-factory-estate-deadman.service",
        ],
        ["rm", "-f", *(str(unit_target / name) for name in UNIT_NAMES)],
        ["systemctl", "--user", "daemon-reload"],
        [
            "systemctl", "--user", "reset-failed",
            "loop-factory-estate.service", "loop-factory-estate-deadman.service",
        ],
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Install uniquely named loop-factory estate user units")
    parser.add_argument("--apply", action="store_true", help="copy files and run the displayed commands")
    parser.add_argument("--rollback", action="store_true", help="show or apply removal of only these unique units")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    commands = rollback_commands(Path.home()) if args.rollback else install_commands(repo_root, Path.home())
    action = "ROLLBACK" if args.rollback else "INSTALL"
    mode = f"{action} APPLY" if args.apply else f"{action} DRY RUN (no commands executed)"
    print(mode)
    for command in commands:
        print(shlex.join(command))
    if not args.apply:
        if repo_root != CANONICAL_ROOT:
            print(f"NOTE: --apply refuses outside the canonical checkout at {CANONICAL_ROOT}.")
        suffix = " --rollback" if args.rollback else ""
        print(f"Re-run with --apply{suffix} to execute exactly the commands above.")
        return 0

    if repo_root != CANONICAL_ROOT:
        print(f"REFUSING: installer must run from {CANONICAL_ROOT}; current root is {repo_root}")
        print(f"After merge, run: cd {CANONICAL_ROOT} && python3 deploy/install_estate_watchdog.py --apply")
        return 2

    if args.rollback:
        critical_failures = []
        for command in commands:
            result = subprocess.run(command, check=False)
            is_critical = (
                command[0] == "rm"
                or command[:3] == ["systemctl", "--user", "daemon-reload"]
            )
            if result.returncode != 0 and is_critical:
                critical_failures.append((command, result.returncode))
        if critical_failures:
            for command, returncode in critical_failures:
                print(f"FAILED ({returncode}) at: {shlex.join(command)}")
            return critical_failures[0][1] or 1
        return 0

    try:
        for command in commands:
            subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"FAILED at: {shlex.join(exc.cmd)}")
        if not args.rollback:
            print(
                "Rollback: "
                f"cd {CANONICAL_ROOT} && python3 deploy/install_estate_watchdog.py --apply --rollback"
            )
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
