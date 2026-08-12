#!/usr/bin/env python3
"""Generate manifest-r9-wave1-fixes.json: repair the r8 review findings.

Wave 1 landed (podcast dcce173 + 6a632d0) after green executed checks; the r8
cross-model review then returned QA: REVISE on both units — the sixth and
seventh instances of this job's core pattern: a defect the FIX invents that a
check written from the DEFECT cannot see. Every finding below was CONFIRMED by
execution before this manifest was generated (u4_reentry_check now 5 red
assertions on HEAD, u7_gates_check now 6).

Edit LANES, then:  python3 build_r9.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
WRAPPER = f"{HERE}/checks/wave1_export_check.py"
ROUND = "r9"
WORKDIR = f"/mnt/d_drive/ringer-work/loop-drive-contract-{ROUND}"

COMMON_BOUNDARY = f"""You are a fix worker. Your current working directory IS a dedicated git
worktree of the repository {PODCAST}, detached at HEAD — edit the files here directly.
HEAD already contains the feature you are hardening; you are repairing specific
review findings, not rebuilding it.

BOUNDARY, read this before anything else.

This repository drives seven live podcast loops that page a human over Telegram and
can send real email. Never execute `scripts/run_podcast_loop.sh` directly, never ssh
anywhere, never send a Telegram message, never run anything under server/ that
performs an action, never create or edit systemd units, never touch
~/.config/ringer. Do not load skills and do not call any MCP tool or App. Do not
touch .git. Do not run git commit, branch, checkout, stash or push — leave your work
uncommitted; the validator exports a patch. There IS a safe way to execute the
runner, described under HOW TO RUN THE CHECK."""


def u4_spec() -> str:
    return f"""{COMMON_BOUNDARY}

You own exactly two tracked paths: `scripts/run_podcast_loop.sh` and
`scripts/obe_loop_verdict.py`. You may also create ./fix-summary.md. Change
nothing else.

FIVE CONFIRMED FINDINGS TO FIX (cross-model review r8, each verified by
executing the runner through scripts/loop_shadow_run.py):

1. FAST-FAIL THRASH (HIGH). The re-entry while-loop's only brake besides a
   verdict is the worker-minutes ledger. A worker that fails FAST (rc 0, valid
   receipt, FAILED verdict, seconds per pass) re-enters thousands of times
   before 1680 minutes accrue, and the history fold makes receipt I/O O(N^2).
   FIX: a thrash DEFECT detector, not a quota cap (the spec's no-separate-cap
   language stands for honest work): track consecutive completed passes whose
   verdict is FAILED and whose measured wall-clock seconds (the value you
   already append to the ledger) is under 60. Reset the counter on any pass
   >= 60s or any non-FAILED verdict. When the counter reaches 3, deliver
   `printf 'PODCAST %s LOOP FAILED: re-entry thrash defect — 3 consecutive
   FAILED passes each under 60s; the worker lane is malfunctioning, not
   working.' "$LOOP" | telegram` and exit 1. The word 'thrash' must appear in
   the alert.

2. LEDGER TOCTOU (HIGH). worker_minutes_capped() is read-then-decide with no
   lock while seven loops share one weekly ledger file on independent systemd
   timers. FIX: serialize with `flock` on a lockfile next to the ledger
   (`"$WORKER_MINUTES_LEDGER.lock"`): hold the lock across read-and-decide in
   the preflight, and across the row append in invoke_ringer_metered. Keep the
   critical sections tight (never hold the lock across the ringer invocation
   itself). `flock` failing to acquire within a sane timeout (say 30s) is an
   ERROR, not a pass-through.

3. FAIL-OPEN LEDGER READ (MEDIUM-HIGH). worker_minutes_consumed() catches only
   FileNotFoundError; any other failure (corrupt bytes, permissions, path is a
   directory) crashes, and in bash that crash is indistinguishable from "not
   capped" — the safety ceiling silently disables exactly when the ledger is
   damaged. FIX: make the helpers three-valued. consumed(): absent file -> 0.0;
   readable file -> total (skip individually corrupt lines ONLY when the file
   as a whole is readable UTF-8; a file that cannot be read or decoded at all
   is an error). capped(): exit 0 = capped, exit 1 = not capped, exit 2 =
   ledger unreadable. The runner treats exit 2 as a loud failure BEFORE any
   worker is invoked: `printf 'PODCAST %s LOOP FAILED: worker-minutes ledger
   unreadable ...' | telegram` and exit 1.

4. PREFLIGHT CAP CLOBBERS SAME-DAY HISTORY (MEDIUM). stamp_worker_minutes_cap
   with reentry=0 unconditionally overwrites $RECEIPT. If an earlier run today
   already wrote a real (possibly folded, multi-attempt) receipt and the weekly
   cap crosses in between, that history is destroyed. FIX: write the stub
   receipt ONLY when $RECEIPT does not exist. When it exists, preserve it
   byte-for-byte below the header lines and re-stamp: parse the existing
   `REENTRY: N` value from the first 5 lines (absent -> 0) and pass THAT value
   to --reentry so the count survives the re-stamp.

5. STALE CALL SITE RESETS REENTRY (MEDIUM-HIGH). The cross-loop repair path's
   verdict call (`--apply --blocked-by "$REPAIR_OWNER"`, in the referral
   QA-PASS branch) predates REENTRY and passes no --reentry flag, so
   apply_verdict defaults to 0 and silently erases a genuine REENTRY: N from a
   multi-attempt day. FIX: pass `--reentry "$REENTRY_ATTEMPT"` there
   ($REENTRY_ATTEMPT is in scope and holds the final attempt count; it is 0
   when no re-entry happened).

Also add one comment (no behavior change) at compute()'s cap branch in
obe_loop_verdict.py noting that --cap is currently never combined with
--qa-verdict by any caller, and that the QA demotion deliberately outranks the
cap if a future caller combines them.

Change nothing else. Escalation digest markers, budget guard, traps, referral
preflight, the QA case exits, and every existing green scenario stay exactly
as they are — the check runs ALL of them, the four originals plus three new
ones for these findings.

HOW TO RUN THE CHECK — this is the command the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {WRAPPER} --unit u4 --worktree "$PWD" \\
        --patch {WORKDIR}/<key>.patch \\
        --summary fix-summary.md --exported-summary {WORKDIR}/<key>.summary.md

(No --base this round: HEAD carries the feature; the defect-repro phase is
skipped by omitting the flag.) The harness stubs Ringer, Telegram, the Linear
card and secret_exec — nothing leaves the machine. New scenarios your fix must
turn green: guest-worker-minutes-corrupt (loud pre-worker failure, 0
invocations), guest-worker-minutes-preseeded-receipt (CAPPED, marker text
preserved, REENTRY: 1 kept), guest-thrash-fast-fails (exit 1 after exactly 3
invocations, 'thrash' in the alert). Also run
`bash -n scripts/run_podcast_loop.sh` and
`python3 -m py_compile scripts/obe_loop_verdict.py`.

OUTPUT CONTRACT

- Your edits, uncommitted, in this worktree.
- ./fix-summary.md with `# Fix Summary`, `## Summary`, `## Files Changed`,
  `## Verification` and `## Assumptions`, under 900 words. Paste the real final
  output of the check command under `## Verification`.

If you cannot make the check pass inside the two owned files, do NOT weaken or
edit the check or the harness. Explain the blocker under `## Assumptions`."""


def u7_spec() -> str:
    return f"""{COMMON_BOUNDARY}

You own exactly two tracked paths: `server/pipeline/prose_gates.py` and
`tests/test_prose_gates.py` (both exist at HEAD; you are hardening them). You
may also create ./fix-summary.md. Change nothing else.

FIVE CONFIRMED FINDINGS TO FIX (cross-model review r8, each verified by
executing the module CLI on the hostile input):

1. UNHASHABLE VALUES CRASH THE CHANNEL GATE (HIGH). `temperature not in
   {{"cold","warm"}}` raises TypeError when the JSON value is an array or
   object (hashing happens before membership). Contract says unknown values
   BLOCK. FIX: type-check the fields (str) before any set probe; non-string ->
   scoped exit-2 block.

2. NUL-BYTE PATHS CRASH BOTH FILE-RESOLVING GATES (HIGH).
   `Path(x).read_text()` raises ValueError («embedded null byte»), which
   neither except tuple catches. FIX: add ValueError to both read_text except
   clauses (manifest read and QA-file read).

3. NO LAST-RESORT CONTAINMENT (MEDIUM). main() calls gate(payload) unwrapped,
   so ANY unforeseen exception exits 1 — outside the 0/2/3 contract, and a
   caller branching on rc==2 reads a crash as "not blocked". FIX: wrap the
   gate call; an unexpected exception becomes a generic exit-2 BLOCK naming
   the gate, with the exception class in the violation text. Fail closed,
   always inside the contract.

4. NOMINATED MATCH IS FRAGILE (LOW, confirmed fail-open by execution).
   `candidate["podcast_status"] == "nominated"` lets "Nominated", 123, or a
   list sail through to PASS. FIX: a non-string podcast_status is a scoped
   exit-2 block (fail closed); string comparison is case-insensitive
   (strip + casefold), so "Nominated" with a live email and no human clearance
   BLOCKS.

5. UNSCOPED offending_span (NOTE). The boolean-fields block passes the whole
   candidate dict as the span. FIX: name the specific offending field(s).

Extend tests/test_prose_gates.py to cover every shape above (array/object
temperature and channel, NUL-byte manifest_path and qa_file, "Nominated" and
non-string statuses, and one arbitrary-exception containment case). Tests keep
importing the module directly.

HOW TO RUN THE CHECK — this is the command the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {WRAPPER} --unit u7 --worktree "$PWD" \\
        --patch {WORKDIR}/<key>.patch \\
        --summary fix-summary.md --exported-summary {WORKDIR}/<key>.summary.md

It executes the module CLI over randomized fixtures including all six hostile
shapes above, then runs your pytest file. Run it yourself as often as you like.

OUTPUT CONTRACT

- Your edits, uncommitted, in this worktree.
- ./fix-summary.md with `# Fix Summary`, `## Summary`, `## Files Changed`,
  `## Verification` and `## Assumptions`, under 900 words. Paste the real final
  output of the check command under `## Verification`.

If you cannot satisfy the contract, do NOT weaken or edit the check. Explain
the blocker under `## Assumptions`."""


LANES = [
    ("u4", "reentry-fixes", "codex", 3600),
    ("u7", "gates-fixes", "codex", 1800),
]

tasks = []
for unit, suffix, engine, timeout in LANES:
    key = f"{ROUND}-{unit}-{suffix}"
    tasks.append({
        "key": key,
        "engine": engine,
        "task_type": "code-fix",
        "timeout_s": timeout,
        "spec": u4_spec() if unit == "u4" else u7_spec(),
        "check": (
            f"PYTHONDONTWRITEBYTECODE=1 python3 {WRAPPER} --unit {unit} --worktree \"$PWD\" "
            f"--patch {WORKDIR}/{key}.patch "
            f"--summary fix-summary.md --exported-summary {WORKDIR}/{key}.summary.md"
        ),
        "expect_files": [f"{WORKDIR}/{key}.patch", f"{WORKDIR}/{key}.summary.md"],
        "verified": (
            "thrash detector, flock'd ledger, fail-closed reads, preserved same-day receipts and the "
            "restored REENTRY stamp all proven by executing the real runner through the shadow harness"
            if unit == "u4" else
            "all hostile JSON shapes block inside the 0/2/3 contract, proven by 29 executed CLI fixtures "
            "plus the unit's own pytest file"
        ),
    })

manifest = {
    "run_name": "loop-drive-contract",
    "workdir": WORKDIR,
    "repo": PODCAST,
    "worktrees": True,
    "max_parallel": len(tasks),
    "tasks": tasks,
}

out = HERE / "manifest-r9-wave1-fixes.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<22} {task['engine']:<8} timeout={task['timeout_s']} spec={len(task['spec'])} chars")
