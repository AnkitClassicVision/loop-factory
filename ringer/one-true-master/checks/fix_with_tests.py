#!/usr/bin/env python3
"""Verify a fix lane, enforce that it actually shipped tests, and export a patch.

Written to replace the bundled fix-swarm check for this job, for two reasons
learned the hard way on 2026-07-26:

  1. The bundled check enforces a document contract that lived only in the
     checker, never in the worker spec. Three lanes did correct work and were
     failed on section headings.
  2. Nothing verified that a lane added tests. One lane shipped a change to the
     most consequential module in the pipeline with zero tests, and the harness
     could not see it. A rule that exists only as prose in a spec is not
     enforced.

So: this check states its own contract in its failure messages, and it fails a
lane that changed production code without adding test functions.

Usage:
  fix_with_tests.py --verify-command CMD --patch OUT.patch
                    --owned-files a.py,b.py --min-new-tests N
                    [--require-test-file PATH]
"""
import argparse
import os
import re
import subprocess
import sys


def run(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                          text=True, **kw)


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-command", required=True)
    ap.add_argument("--patch", required=True)
    ap.add_argument("--owned-files", required=True)
    ap.add_argument("--min-new-tests", type=int, default=1)
    ap.add_argument("--require-test-file")
    args = ap.parse_args()

    owned = [p.strip() for p in args.owned_files.split(",") if p.strip()]

    # ---- stage only owned paths, and prove nothing else was touched --------
    run(["git", "add", "-A", "--", "server", "tests"])
    changed = run(["git", "diff", "--cached", "--name-only"]).stdout.split()
    stray = [p for p in changed if p not in owned]
    if stray:
        fail(
            "these files were changed but are not in this lane's owned list: %s\n"
            "owned: %s\n"
            "Another worker owns those in parallel, so editing them would collide "
            "at integration." % (", ".join(stray), ", ".join(owned))
        )
    if not changed:
        fail("nothing was changed. The lane produced no edits under server/ or tests/.")

    diff = run(["git", "diff", "--cached"]).stdout

    # ---- the rule the prose could not enforce: tests must exist -----------
    touched_src = [p for p in changed if p.startswith("server/")]
    touched_tests = [p for p in changed if p.startswith("tests/")]

    if touched_src and not touched_tests:
        fail(
            "this lane changed production code (%s) and added no tests at all.\n"
            "The spec requires every guard to be proven capable of failing, which "
            "means a test that feeds the poisoned input and asserts it blocks.\n"
            "Untested code is not acceptable here: this codebase shipped five "
            "broken releases green because gates could pass without measuring."
            % ", ".join(touched_src)
        )

    added_tests = re.findall(r"^\+\s*def (test_[A-Za-z0-9_]+)", diff, re.M)
    if len(added_tests) < args.min_new_tests:
        fail(
            "found %d new test function(s), need at least %d.\nfound: %s\n"
            "Count is of lines adding `def test_...` in the staged diff."
            % (len(added_tests), args.min_new_tests, added_tests or "(none)")
        )

    # ---- a test suite that only proves the happy path is not a proof ------
    negatives = re.findall(
        r"^\+.*(pytest\.raises|assert_blocked|\[.passed.\]\s*is\s*False|"
        r"passed.*is\s*False|must\s+(?:block|fail|raise|halt)|refus)",
        diff, re.M | re.I)
    if not negatives:
        fail(
            "the new tests contain no negative case. Nothing in the diff raises, "
            "asserts a block, or asserts a failure.\n"
            "A check nobody has watched fail is not a check. Add a test that feeds "
            "the poisoned input and asserts the guard stops it."
        )

    # ---- run the caller's verification ------------------------------------
    proc = run(args.verify_command)
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-2500:] + (proc.stderr or "")[-1200:]
        fail("verification command exited %d:\n%s\n--- output tail ---\n%s"
             % (proc.returncode, args.verify_command, tail))

    # ---- export the patch outside the worktree, which may be deleted ------
    os.makedirs(os.path.dirname(os.path.abspath(args.patch)), exist_ok=True)
    with open(args.patch, "w", encoding="utf-8") as fh:
        fh.write(diff)
    if not os.path.getsize(args.patch):
        fail("exported patch at %s is empty" % args.patch)

    print("PASS: %d file(s) changed, all owned. %d new test(s) including %d "
          "negative assertion(s). Verification exited 0. Patch exported to %s (%d bytes)."
          % (len(changed), len(added_tests), len(negatives), args.patch,
             os.path.getsize(args.patch)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
