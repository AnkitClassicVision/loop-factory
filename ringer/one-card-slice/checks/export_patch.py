#!/usr/bin/env python3
"""Stage a lane's owned files, refuse out-of-scope edits, export the patch.

Prints WHY it fails. Retry-idempotent: computes the changed set from the union
of staged and unstaged paths. Allows harness-owned worker.log and untracked
scratch outside the repo contract (untracked files are ignored, not staged).

Usage: export_patch.py --worktree W --patch OUT --owned PATH [--owned PATH ...]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--owned", action="append", required=True)
    args = parser.parse_args()
    worktree = args.worktree.resolve()

    add = git(worktree, "add", "--", *args.owned)
    if add.returncode != 0:
        print(f"export_patch: FAIL git add: {add.stderr.strip()}")
        return 1
    failures = []
    for line in git(worktree, "status", "--porcelain").stdout.splitlines():
        code, path = line[:2], line[3:].strip('"')
        if code == "??":
            continue
        if path not in args.owned:
            failures.append(f"out-of-scope change: {path} (this lane owns {args.owned})")
    if failures:
        print("export_patch: FAIL")
        for item in failures:
            print(f"  {item}")
        return 1
    diff = git(worktree, "diff", "--cached", "--binary", "--", *args.owned)
    if not diff.stdout.strip():
        print("export_patch: FAIL nothing staged; no owned file was created or edited")
        return 1
    args.patch.parent.mkdir(parents=True, exist_ok=True)
    args.patch.write_text(diff.stdout, encoding="utf-8")
    print(f"export_patch: PASS {args.patch} ({len(diff.stdout)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
