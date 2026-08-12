#!/usr/bin/env python3
"""Wave-1 worktree wrapper: run the unit's executed check, then export the patch.

One wrapper, two units (disjoint file ownership is enforced here):

  --unit u4   owns scripts/run_podcast_loop.sh + scripts/obe_loop_verdict.py
              runs u4_reentry_check.py (executes the REAL runner through the
              shadow harness, four scenarios) plus bash -n / py_compile.
  --unit u7   owns server/pipeline/prose_gates.py + tests/test_prose_gates.py
              runs u7_gates_check.py (23 fixture executions of the module CLI)
              plus the worker's own pytest file.

Worktree contract (Ringer worktrees mode): the worker's edits stay uncommitted;
this check stages ONLY the owned paths, refuses any other tracked change,
exports `git diff --cached` outside the worktree, and copies fix-summary.md
out. Passing tasks get their worktree deleted — the patch and summary are the
deliverables.
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
OWNED = {
    "u4": ["scripts/run_podcast_loop.sh", "scripts/obe_loop_verdict.py"],
    "u7": ["server/pipeline/prose_gates.py", "tests/test_prose_gates.py"],
}
SUMMARY_SECTIONS = ["# Fix Summary", "## Summary", "## Files Changed", "## Verification", "## Assumptions"]


def git(worktree: Path, *args: str):
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def run_unit_check(unit: str, worktree: Path, base: str | None, tmp: Path) -> list[str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    failures: list[str] = []
    if unit == "u4":
        syntax = subprocess.run(["bash", "-n", str(worktree / "scripts/run_podcast_loop.sh")],
                                capture_output=True, text=True)
        if syntax.returncode != 0:
            failures.append(f"FAIL [bash_syntax]: {syntax.stderr.strip()[:300]}")
        compile_check = subprocess.run(
            [sys.executable, "-m", "py_compile", str(worktree / "scripts/obe_loop_verdict.py")],
            capture_output=True, text=True, env=env)
        if compile_check.returncode != 0:
            failures.append(f"FAIL [py_compile]: {compile_check.stderr.strip()[:300]}")
        if failures:
            return failures  # do not drive a runner that cannot parse
        cmd = [sys.executable, str(HERE / "u4_reentry_check.py"),
               "--repo", str(worktree), "--out", str(tmp / "u4-out")]
        if base:
            cmd += ["--base", base]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env)
        sys.stdout.write(done.stdout)
        if done.returncode != 0:
            failures.append("FAIL [u4_reentry_check]: see the executed-check output above")
    elif unit == "u7":
        done = subprocess.run(
            [sys.executable, str(HERE / "u7_gates_check.py"),
             "--repo", str(worktree), "--out", str(tmp / "u7-out")],
            capture_output=True, text=True, timeout=600, env=env)
        sys.stdout.write(done.stdout)
        if done.returncode != 0:
            failures.append("FAIL [u7_gates_check]: see the executed-check output above")
        test_file = worktree / "tests/test_prose_gates.py"
        if not test_file.is_file():
            failures.append("FAIL [tests_missing]: tests/test_prose_gates.py was not written")
        else:
            pytest_run = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)],
                capture_output=True, text=True, timeout=300, cwd=str(worktree),
                env={**env, "PYTHONPATH": str(worktree),
                     "PYTHONPYCACHEPREFIX": str(tmp / "pycache")})
            if pytest_run.returncode != 0:
                tail = (pytest_run.stdout + pytest_run.stderr)[-400:]
                failures.append(f"FAIL [unit_tests]: pytest on tests/test_prose_gates.py failed: {tail}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", required=True, choices=sorted(OWNED))
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--base", help="tree still carrying the defect (u4 defect-repro phase)")
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    owned = OWNED[args.unit]
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"wave1-{args.unit}-") as tmp_name:
        failures += run_unit_check(args.unit, worktree, args.base, Path(tmp_name))

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
    print(f"PASS [wave1-{args.unit}]: executed check green, patch exported, ownership clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
