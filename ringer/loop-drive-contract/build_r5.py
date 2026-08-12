#!/usr/bin/env python3
"""Generate manifest-r5-u6-crossloop.json for the loop-drive-contract job.

Round 5 is U6: a gate owned by another loop stops being a silent daily zero.

Runs AFTER U1 has landed, because both units edit run_podcast_loop.sh and this
one writes `blocked_by` into the loop-drive-v1 block that U1's verdict computer
reads. Serial rather than parallel: two lanes over one file is a merge problem
nobody needs for the sake of ninety seconds.

Edit LANES, then:  python3 build_r5.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
CHECK = f"{HERE}/checks/u6_cross_loop_check.py"
# r5 passed its check and was rejected in review: it called the repair opener at
# line 513, but the blocked path leaves run_podcast_loop.sh at line 500, so the
# opener was dead code on the one scenario the unit exists for. The check gained a
# reachability assertion; r5b rebuilds from HEAD with the finding (requirement 7).
ROUND = "r5b"
WORKDIR = f"/mnt/d_drive/ringer-work/loop-drive-contract-{ROUND}"

LANES: list[tuple[str, str, str | None, list[str]]] = [
    ("codex", "codex", None, []),
]


def spec() -> str:
    return f"""You are a fix worker. Your current working directory IS a dedicated git
worktree of the repository {PODCAST}, detached at HEAD — edit the files here directly.

BOUNDARY, read this before anything else.

You own exactly two tracked paths: `scripts/obe_cross_loop_repair.py` (you create it)
and `scripts/run_podcast_loop.sh`. You may also create ./fix-summary.md in the
worktree root. Change nothing else — in particular do NOT edit
`server/pipeline/referral_touch_automation.py`; that module already reports its block
correctly and rewriting it would widen the blast radius for no gain. Do not touch
.git. Do not run git commit, branch, checkout, stash or push — leave your work
uncommitted; a validator exports it as a patch.

This repository drives seven live podcast loops that page a human over Telegram and
can send real email. So: never execute `scripts/run_podcast_loop.sh`, never ssh
anywhere, never send a Telegram message, never run anything under server/ that
performs an action, never create or edit systemd units. Do not load skills and do not
call any MCP tool or App; nothing here should be captured to any memory backend.

THE DEFECT

On 2026-08-06 the referral flywheel loop had a verified referral ask ready to go and
recorded this, then stopped:

    {{"cards_created": 0, "drafts_created": 0, "sent": false,
      "reason": "Health QA is not PASS", "status": "blocked"}}

Health QA was REVISE. The health loop owns that gate. Nothing told the health loop it
was blocking anyone, nothing carried a deadline, and nothing noticed when the same
block came back the next day. The referral loop simply recorded a zero and exited
successfully. You can watch this happen today: with a health QA file whose first line
is `QA: REVISE`, `referral_touch_automation.validate_inputs` raises
`GateBlocked("Health QA is not PASS")` and no repair task exists anywhere in the repo.

A block owned by someone else is not this loop's failure — but it is somebody's, and
right now it is nobody's.

WHAT TO BUILD

1. NEW FILE `scripts/obe_cross_loop_repair.py`, invoked as:

       python3 scripts/obe_cross_loop_repair.py --blocked-loop <loop> --reason <text>
           --ledger <path> [--now <iso8601>] [--detection-latency-hours 26]

   It prints exactly one line of JSON on stdout:

       {{"cross_loop": <bool>, "owner": <loop|null>, "verdict": "BLOCKED: <owner>"|null,
         "task_id": <str|null>, "deadline": <iso8601|null>,
         "consecutive_days": <int>, "defect": <bool>, "reason": <str>}}

   `--now` exists so the check can drive several business days deterministically;
   default to the current UTC time when it is absent. `--detection-latency-hours`
   defaults to 26, which is the podcast charter's `detection_latency_hours`.

2. OWNERSHIP RESOLUTION. A reason maps to the loop that owns the gate. Today exactly
   one mapping is real: a reason naming Health QA or the Health receipt is owned by
   the `health` loop. Everything else is NOT cross-loop — it is the blocked loop's own
   problem, and it must return `cross_loop: false`, a null owner, no task_id, and
   write NOTHING to the ledger. Handing a loop's own failure to another loop is worse
   than leaving it alone. Keep the mapping table obvious and easy to extend; do not
   invent owners for reasons you cannot justify.

3. THE REPAIR TASK. A cross-loop block opens a task in the ledger carrying at least a
   task_id, the blocked loop, the owner, a fingerprint of the block, the business
   date, when it opened, a deadline `--detection-latency-hours` after the block, a
   consecutive-day count, and a status. The ledger is a JSON file you create if it is
   absent; tolerate an unreadable one by starting fresh rather than crashing.

4. RECURRENCE. The same fingerprint on two CONSECUTIVE business days is a department
   defect: `consecutive_days` reaches 2 and `defect` becomes true. A gap breaks the
   streak and the count restarts at 1. Running twice on the SAME day is idempotent: it
   must not open a second task and must not advance the day count, because the runner
   may invoke you more than once in a day.

5. PRIVACY, non-negotiable. Reasons can carry an address or a phone number, and this
   ledger gets read into receipts that are pasted into Telegram. Strip email addresses
   and phone numbers from the reason before it is stored or printed. No real names,
   no raw contact IDs, ever.

6. WIRING in `scripts/run_podcast_loop.sh`. When a loop's automation receipt reports a
   blocked status, invoke the repair opener with that loop and reason. When the result
   is cross-loop, the run is NOT a success: make sure the receipt's loop-drive-v1
   block carries `"blocked_by": "<owner>"` so `scripts/obe_loop_verdict.py` computes
   BLOCKED, and put the defect on the escalation path when `defect` is true. Change
   nothing else in that script — escalation delivery, the verdict computation, the QA
   verdict handling, the budget guard and the traps all stay exactly as they are.

7. REACHABILITY, and this is where an earlier attempt at this unit died, so read it
   twice. `server/pipeline/referral_touch_automation.main()` ends with
   `return 0 if result["status"] in {{"card_created", "replay", "no_action"}} else 1`,
   so a BLOCKED status exits NON-ZERO. In `run_podcast_loop.sh` the referral post-QA
   action captures that as `POST_RC=$?` and answers a non-zero value by sending a
   Telegram line and running `exit 1`. The earlier attempt put its repair call after
   that branch: correct code, never executed, because the blocked path had already
   left the script. Open the repair task INSIDE the `POST_RC` failure branch, before
   anything exits. The run may still end non-zero afterwards — a cross-loop block is
   not a success — but the task, the `blocked_by` field, the recomputed verdict and
   the defect escalation must all happen first. The check now asserts this by line
   position and will tell you both line numbers if you get it wrong.

HOW TO RUN THE CHECK — this is the command the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --worktree "$PWD"

It first drives the REAL gate at HEAD to prove the block still reproduces, then drives
your module across four business days, a self-caused reason, a same-day repeat and a
reason carrying contact data. Every failure names the case. Run it before you start to
see the two expected failures.

Also run `python3 -m py_compile scripts/obe_cross_loop_repair.py` and
`bash -n scripts/run_podcast_loop.sh`.

OUTPUT CONTRACT

- Your edits, uncommitted, in this worktree.
- ./fix-summary.md with `# Fix Summary`, `## Summary`, `## Files Changed`,
  `## Verification` and `## Assumptions`, under 900 words. Paste the real final output
  of the check command under `## Verification`, not a description of it.

If you cannot make the check pass inside those two files, do NOT make speculative
edits and do NOT weaken the check. Explain the blocker under `## Assumptions`.
"""


tasks = []
for suffix, engine, model, engine_args in LANES:
    key = f"{ROUND}-u6-crossloop-{suffix}"
    task: dict[str, object] = {
        "key": key,
        "engine": engine,
        "task_type": "code-feature",
        "timeout_s": 2400,
        "spec": spec(),
        "check": (
            f"PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --worktree \"$PWD\" "
            f"--patch {WORKDIR}/{key}.patch "
            f"--summary fix-summary.md --exported-summary {WORKDIR}/{key}.summary.md"
        ),
        "expect_files": [f"{WORKDIR}/{key}.patch", f"{WORKDIR}/{key}.summary.md"],
        "verified": (
            "a cross-loop block resolves to its owning loop, returns BLOCKED naming it, opens a "
            "repair task with a deadline inside detection_latency_hours: 26, escalates as a "
            "department defect on the second consecutive day but not across a gap, opens nothing "
            "for a self-caused block, is idempotent within a day, and never writes contact data"
        ),
    }
    if model:
        task["model"] = model
    if engine_args:
        task["engine_args"] = engine_args
    tasks.append(task)

manifest = {
    "run_name": "loop-drive-contract",
    "workdir": WORKDIR,
    "repo": PODCAST,
    "worktrees": True,
    "max_parallel": len(tasks),
    "tasks": tasks,
}

out = HERE / f"manifest-{ROUND}-u6-crossloop.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<28} {task['engine']:<12} spec={len(task['spec'])} chars")
