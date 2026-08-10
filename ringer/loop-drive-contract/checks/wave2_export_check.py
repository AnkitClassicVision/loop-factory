#!/usr/bin/env python3
"""Wave-2 worktree wrapper: run the unit's half of the U2a/U2b check, export the patch.

Same worktree contract as wave 1: the worker leaves edits uncommitted, this
wrapper runs the executed check, stages ONLY the declared owned paths, refuses
any other tracked change, and exports the patch outside the worktree (passing
tasks get their worktree deleted).

  --mode module   U2a: drives server/pipeline/guest_outreach_draft.py
                         directly with a fake Gmail service and the real gates.
  --mode runner   U2b: executes the real runner through the shadow
                         harness for the three producer scenarios.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECK = HERE / "u2a_producer_check.py"
SUMMARY_SECTIONS = ["# Fix Summary", "## Summary", "## Files Changed", "## Verification", "## Assumptions"]


def git(worktree: Path, *args: str):
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", default=".", type=Path)
    # Values must not start with "--": argparse would read the next token as a
    # flag and fail with "expected one argument". That defect cost two worker
    # lanes ~253k tokens on 2026-08-10 before it was classified.
    parser.add_argument("--mode", required=True, choices=["module", "runner"])
    parser.add_argument("--owned", action="append", required=True)
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    owned = list(args.owned)
    failures: list[str] = []
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    check_flag = "--module-only" if args.mode == "module" else "--runner-only"
    if args.mode == "runner":
        syntax = subprocess.run(["bash", "-n", str(worktree / "scripts/run_podcast_loop.sh")],
                                capture_output=True, text=True)
        if syntax.returncode != 0:
            failures.append(f"FAIL [bash_syntax]: {syntax.stderr.strip()[:300]}")

    if not failures:
        with tempfile.TemporaryDirectory(prefix="wave2-") as tmp:
            done = subprocess.run(
                [sys.executable, str(CHECK), "--repo", str(worktree), check_flag, "--out", tmp],
                capture_output=True, text=True, timeout=1800, env=env)
            sys.stdout.write(done.stdout)
            if done.stderr.strip():
                sys.stdout.write(done.stderr[-500:])
            if done.returncode != 0:
                failures.append(f"FAIL [u2a_producer_check {check_flag}]: see the executed-check output above")

    if args.exported_summary:
        if not args.summary.is_file():
            failures.append(f"FAIL [summary_missing]: {args.summary} was not written")
        else:
            text = args.summary.read_text(encoding="utf-8")
            missing = [s for s in SUMMARY_SECTIONS if s.lower() not in text.lower()]
            if missing:
                failures.append(f"FAIL [summary_sections]: fix-summary.md is missing {', '.join(missing)}")
            else:
                args.exported_summary.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.summary, args.exported_summary)

    if args.patch:
        add = git(worktree, "add", "--", *owned)
        if add.returncode != 0:
            failures.append(f"FAIL [git_add_failed]: {add.stderr.strip()}")
        for line in git(worktree, "status", "--porcelain").stdout.splitlines():
            code, path = line[:2], line[3:].strip('"')
            if code != "??" and path not in owned:
                failures.append(f"FAIL [outside_owned_files]: {path} changed; this task owns {owned}")
        diff = git(worktree, "diff", "--cached", "--binary", "--", *owned)
        if not diff.stdout.strip():
            failures.append("FAIL [empty_patch]: nothing staged; no owned file was edited")
        else:
            args.patch.parent.mkdir(parents=True, exist_ok=True)
            args.patch.write_text(diff.stdout, encoding="utf-8")

    if failures:
        for item in failures:
            print(item)
        print(f"\n{len(failures)} failure(s). Exit 1.")
        return 1
    print(f"PASS [wave2 {check_flag}]: executed check green, patch exported, ownership clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
