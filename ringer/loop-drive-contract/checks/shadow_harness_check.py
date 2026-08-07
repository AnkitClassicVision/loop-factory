#!/usr/bin/env python3
"""Meta-check for the executed-runner shadow harness.

Three units in a row (U1, U6, and U5's first attempt) passed an executed check and
were rejected in human review. All three defects lived in `run_podcast_loop.sh`'s
CONTROL FLOW, and every check in this job validates artifacts in isolation: it
greps the runner for a filename, or extracts functions and drives them outside the
script. Grep proves presence. Only execution proves reachability.

So this check does not grade a harness by reading it. It hands the harness two
trees that differ ONLY in where a call sits, and requires the harness to tell them
apart by running them:

  DEAD  = podcast @ 98fc703 + the rejected r5 patch. The cross-loop repair opener
          is called at line 513, but the blocked path leaves the script at line 500,
          so it never executes.
  LIVE  = podcast @ 98fc703 + the accepted r5b patch. Same call, inside the failure
          branch, before the exit.

A harness that reports the same thing for both is worthless no matter how it is
written. One that separates them can see the defect class that got past three
checks.

It also requires the harness to be inert: no ssh, no Linear card, no secret fetch,
and not one byte changed in the tree it drives.

Locked interface (coordinator decision, restated in the worker spec):

    python3 scripts/loop_shadow_run.py --repo <tree> --loop <loop>
        --scenario <name> --out <dir>

    stdout, one JSON object:
      {"scenario", "loop", "exit_code", "verdict", "receipt", "sink": [...],
       "escalation_delivered": bool, "repair_ledger": {...}|null,
       "tree_unchanged": bool, "external_calls": [...]}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SUMMARY_SECTIONS = ("# Fix Summary", "## Summary", "## Files Changed", "## Verification")
REQUIRED_KEYS = (
    "scenario", "loop", "exit_code", "verdict", "sink",
    "repair_ledger", "tree_unchanged", "external_calls",
)
BASE_REF = "98fc703"
PODCAST = Path("/mnt/d_drive/repos/podcast")
DEAD_PATCH = Path("/mnt/d_drive/ringer-work/loop-drive-contract-r5/r5-u6-crossloop-codex.patch")
LIVE_PATCH = Path("/mnt/d_drive/ringer-work/loop-drive-contract-r5b/r5b-u6-crossloop-codex.patch")
CROSS_LOOP_SCENARIO = "referral-blocked-cross-loop"


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_fixture(dest: Path, patch: Path) -> str | None:
    """podcast @ BASE_REF with `patch` applied. Returns an error string or None."""
    dest.mkdir(parents=True, exist_ok=True)
    # scripts/ ONLY. The podcast repo tracks episode media, so a full archive is
    # ~3.4 GB per fixture and blows the tmpfs quota. Everything the runner reaches
    # outside scripts/ is stubbed by the harness anyway.
    archive = subprocess.run(
        ["git", "-C", str(PODCAST), "archive", BASE_REF, "scripts"],
        capture_output=True, timeout=180,
    )
    if archive.returncode != 0:
        return f"git archive {BASE_REF} failed: {archive.stderr.decode()[:300]}"
    extract = subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, capture_output=True, timeout=180)
    if extract.returncode != 0:
        return f"tar extract failed: {extract.stderr.decode()[:300]}"
    if not patch.is_file():
        return f"fixture patch missing: {patch}"
    applied = subprocess.run(
        ["patch", "-p1", "--silent"], cwd=str(dest),
        input=patch.read_bytes(), capture_output=True, timeout=120,
    )
    if applied.returncode != 0:
        return f"applying {patch.name} failed: {applied.stdout.decode()[:300]}"
    return normalize_fixture(dest / "scripts/run_podcast_loop.sh")


def normalize_fixture(runner: Path) -> str | None:
    """Repair one unrelated defect the fixture base carries, or nothing runs.

    Both fixtures are built from 98fc703, which shipped the worker prompt with an
    unescaped JSON example: 36 raw double quotes inside a double-quoted bash
    assignment terminated the string early and the runner died before reaching any
    of the control flow this check is about. That was fixed in cfc502d. The fix is
    reapplied here rather than moving the fixture base, because the base must be
    the commit the two rejected/accepted patches were written against.
    """
    text = runner.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.startswith("1. Receipt at ${RECEIPT}") or '{\\"sends\\"' in line:
            continue
        if '{"sends"' not in line:
            continue
        start = line.index('{"sends"')
        end = line.index("}]}", start) + len("}]}")
        lines[index] = line[:start] + line[start:end].replace('"', '\\"') + line[end:]
        runner.write_text("".join(lines), encoding="utf-8")
        return None
    return None


def run_harness(worktree: Path, repo: Path, loop: str, scenario: str, out: Path,
                stub_bin: Path) -> tuple[dict | None, str]:
    env = dict(os.environ)
    env["PATH"] = f"{stub_bin}:{env.get('PATH', '')}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "scripts/loop_shadow_run.py", "--repo", str(repo),
         "--loop", loop, "--scenario", scenario, "--out", str(out)],
        capture_output=True, text=True, timeout=600, cwd=str(worktree), env=env,
    )
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line), ""
            except json.JSONDecodeError:
                continue
    return None, (
        f"no JSON report on stdout (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}"
    )


def make_stub_bin(tmp: Path) -> Path:
    """A PATH where any real outbound tool records itself instead of running."""
    stub = tmp / "stubbin"
    stub.mkdir(exist_ok=True)
    marker = tmp / "external-calls.log"
    for tool in ("ssh", "curl", "scp"):
        path = stub / tool
        path.write_text(f'#!/bin/sh\necho "$0 $@" >> "{marker}"\nexit 1\n', encoding="utf-8")
        path.chmod(0o755)
    return stub


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    failures: list[str] = []

    harness = worktree / "scripts/loop_shadow_run.py"
    if not harness.is_file():
        print(
            "FAIL [harness_missing]: scripts/loop_shadow_run.py does not exist. Every remaining "
            "unit in this job edits run_podcast_loop.sh's control flow, and no check in this job "
            "can currently see whether new code is reachable."
        )
        return 1

    with tempfile.TemporaryDirectory(prefix="shadow-meta-") as tmp_name:
        tmp = Path(tmp_name)
        stub_bin = make_stub_bin(tmp)
        calls_log = tmp / "external-calls.log"

        reports: dict[str, dict] = {}
        for label, patch in (("dead", DEAD_PATCH), ("live", LIVE_PATCH)):
            fixture = tmp / f"fixture-{label}"
            error = build_fixture(fixture, patch)
            if error:
                failures.append(f"FAIL [fixture_{label}_unbuildable]: {error}")
                continue
            before = tree_hash(fixture)
            report, detail = run_harness(
                worktree, fixture, "referral-flywheel", CROSS_LOOP_SCENARIO,
                tmp / f"out-{label}", stub_bin,
            )
            if report is None:
                failures.append(f"FAIL [harness_output_{label}]: {detail}")
                continue
            reports[label] = report

            missing = [key for key in REQUIRED_KEYS if key not in report]
            if missing:
                failures.append(
                    f"FAIL [report_contract_{label}]: the JSON report is missing {missing}"
                )
            after = tree_hash(fixture)
            if before != after:
                failures.append(
                    f"FAIL [harness_mutated_the_tree_{label}]: the {label} tree changed while the "
                    "harness drove it. A shadow run must leave the tree it drives byte-identical."
                )
            if report.get("tree_unchanged") is not True:
                failures.append(
                    f"FAIL [tree_unchanged_not_reported_{label}]: the harness must verify and "
                    "report tree_unchanged itself, not leave it to the caller."
                )

        if calls_log.is_file():
            failures.append(
                "FAIL [harness_made_external_calls]: something reached for ssh, curl or scp during "
                f"a shadow run:\n{calls_log.read_text()[:600]}"
            )

        # THE DISCRIMINATING ASSERTION. Everything above is hygiene; this is the point.
        dead, live = reports.get("dead"), reports.get("live")
        if dead is not None and live is not None:
            dead_opened = bool(dead.get("repair_ledger"))
            live_opened = bool(live.get("repair_ledger"))
            if dead_opened:
                failures.append(
                    "FAIL [dead_tree_reported_a_repair_task]: on the rejected r5 tree the repair "
                    "opener sits after the blocked path's `exit 1`, so it CANNOT run — but the "
                    "harness reported a repair task. The harness is not executing the real control "
                    "flow; it is inferring."
                )
            if not live_opened:
                failures.append(
                    "FAIL [live_tree_reported_no_repair_task]: on the accepted r5b tree the repair "
                    "opener is inside the failure branch and does run, but the harness saw no "
                    "repair task. It is not reaching the blocked path at all."
                )
            if dead_opened == live_opened:
                failures.append(
                    "FAIL [harness_cannot_see_reachability]: the harness reported the same outcome "
                    "for a tree where the call is dead and a tree where it runs. That is the exact "
                    "defect class that got past three executed checks in this job; a harness that "
                    "cannot separate these two trees adds nothing."
                )
            elif not dead_opened and live_opened:
                owner = (live.get("repair_ledger") or {})
                blob = json.dumps(owner)
                if "health" not in blob:
                    failures.append(
                        "FAIL [repair_task_does_not_name_health]: the live tree opened a repair "
                        f"task that never names the owning loop: {blob[:400]}"
                    )
                if str(live.get("verdict") or "").upper() != "BLOCKED":
                    failures.append(
                        f"FAIL [live_verdict_not_blocked]: verdict was {live.get('verdict')!r}; a "
                        "cross-loop block must compute BLOCKED once blocked_by is set."
                    )

        # The harness must also work on the tree under test, not only on fixtures.
        before = tree_hash(worktree / "scripts")
        report, detail = run_harness(
            worktree, worktree, "guest-acquisition", "guest-failed-zero-sends",
            tmp / "out-self", stub_bin,
        )
        if report is None:
            failures.append(f"FAIL [harness_output_self]: {detail}")
        else:
            if str(report.get("verdict") or "").upper() != "FAILED":
                failures.append(
                    "FAIL [self_scenario_verdict]: driving the candidate tree with a zero-send "
                    "receipt carrying an eligible candidate must compute FAILED, got "
                    f"{report.get('verdict')!r}."
                )
            if not isinstance(report.get("sink"), list):
                failures.append(
                    "FAIL [sink_not_captured]: the report's 'sink' must be the list of messages "
                    "the run would have sent, so a check can assert on alerts without sending one."
                )
        if tree_hash(worktree / "scripts") != before:
            failures.append(
                "FAIL [harness_mutated_the_candidate_tree]: driving the candidate tree changed "
                "scripts/. The harness must write only under --out."
            )

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
        owned = ["scripts/loop_shadow_run.py", "scripts/run_podcast_loop.sh"]
        add = subprocess.run(["git", "-C", str(worktree), "add", "--", *owned],
                             capture_output=True, text=True, timeout=120)
        if add.returncode != 0:
            failures.append(f"FAIL [git_add_failed]: {add.stderr.strip()}")
        status = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=120)
        for line in status.stdout.splitlines():
            code, path = line[:2], line[3:].strip('"')
            if code != "??" and path not in owned:
                failures.append(f"FAIL [outside_owned_files]: {path} changed; this task owns {owned}")
        diff = subprocess.run(["git", "-C", str(worktree), "diff", "--cached", "--binary", "--", *owned],
                              capture_output=True, text=True, timeout=120)
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

    print("PASS [shadow_harness_sees_control_flow]")
    print("  the harness ran the real runner on two trees that differ only in call position")
    print("  dead tree (rejected r5): no repair task, because the call sits past the exit")
    print("  live tree (accepted r5b): repair task opened, naming health, verdict BLOCKED")
    print("  the candidate tree drives too: a zero-send receipt with an eligible candidate is FAILED")
    print("  no ssh, curl or scp was reached, and no tree changed by a single byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
