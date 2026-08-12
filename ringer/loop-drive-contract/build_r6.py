#!/usr/bin/env python3
"""Generate manifest-r6-u0-corroboration.json for the loop-drive-contract job.

U0 is not in the original spec. It was added after a Fable design review found
that U1 moved the verdict out of the worker's hands but left its INPUTS there,
and that the verdict is final before anything can corroborate it. Owner approved
the addition (Ankit, 2026-08-07) with the instruction to stop after U0 and plan
the rest.

Edit LANES, then:  python3 build_r6.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
CHECK = f"{HERE}/checks/u0_corroboration_check.py"
ROUND = "r6"
WORKDIR = f"/mnt/d_drive/ringer-work/loop-drive-contract-{ROUND}"

LANES: list[tuple[str, str, str | None, list[str]]] = [
    ("codex", "codex", None, []),
]


def spec() -> str:
    return f"""You are a fix worker. Your current working directory IS a dedicated git
worktree of the repository {PODCAST}, detached at HEAD — edit the files here directly.

BOUNDARY, read this before anything else.

You own exactly two tracked paths: `scripts/obe_loop_verdict.py` and
`scripts/run_podcast_loop.sh`. You may also create ./fix-summary.md in the worktree
root. Change nothing else. Do not touch .git. Do not run git commit, branch,
checkout, stash or push — leave your work uncommitted; a validator exports a patch.

This repository drives seven live podcast loops that page a human over Telegram and
can send real email. Never execute `scripts/run_podcast_loop.sh` directly, never ssh
anywhere, never send a Telegram message, never run anything under server/ that
performs an action, never create or edit systemd units. Do not load skills and do not
call any MCP tool or App. There IS a safe way to run the runner, described below.

THE DEFECT

An earlier unit moved the loop verdict from something the worker typed to something
`scripts/obe_loop_verdict.py` computes. It did not move the INPUTS. Three fields in
the receipt block are still written by the worker and each one buys a success verdict
outright:

  "cap": "anything"            -> CAPPED. Any non-null value. No shape check.
  "blocked_by": "anything"     -> BLOCKED. Any non-null value.
  "quota": {{"met": true}}       -> stops re-entry for the day.

Only EXHAUSTED was made to pay for itself, and even there nothing checks that a
candidate's `evidence` string points at anything real.

The second half is worse, and it is a sequencing problem. The verdict is computed
inside the WORKER TASK'S CHECK, which runs before the reviewer and before the post-QA
action. So when the reviewer catches a fabricated disqualifier and returns
`QA: REVISE`, the runner sends a Telegram line and exits 0 with EXHAUSTED — a success
verdict — standing in the receipt. The review changes nothing it was built to change.

THE RULE YOU ARE IMPLEMENTING: no success verdict without corroboration the RUNNER
observed. Corroboration arrives as command-line flags, because a flag comes from the
runner's own execution and a receipt field comes from the worker.

1. `scripts/obe_loop_verdict.py` grows four optional flags:

       --qa-verdict PASS|REVISE|BLOCK   the reviewer's actual verdict
       --blocked-by <loop>              the ONLY source of a BLOCKED verdict
       --sends-proof <path>             a JSON artifact proving sends happened
       --quota-target <int>             from the charter; default 2

   and the verdicts become:

       BLOCKED    only when --blocked-by names a loop. The receipt's own
                  `blocked_by` field is IGNORED from now on.
       CAPPED     only when `cap` is an object whose `ceiling` is one of the real
                  charter ceilings: outbound_per_day, new_contacts_per_day,
                  per_contact_cadence_floor_days, worker_minutes,
                  weekly_touch_ceiling. An invented ceiling name buys nothing.
       DROVE      only when the receipt claims sends > 0 AND --sends-proof names a
                  readable JSON artifact showing at least one send. Read `sends`,
                  else `drafts_created`, else treat `sent: true` as one. If the
                  claim has no proof, or the proof contradicts it, that is FAILED
                  with a reason saying so. The artifact outranks the claim.
       EXHAUSTED  as before, and additionally every candidate's `evidence` that
                  looks like a repo path (contains a `/`, no spaces; strip any
                  trailing `:anchor`) must resolve to a real file under the repo.
                  A citation to a file that does not exist is a fabricated citation.
       FAILED     everything else.

   Then, after the verdict is chosen: if `--qa-verdict` is REVISE or BLOCK and the
   verdict is DROVE, EXHAUSTED or CAPPED, demote it to FAILED with a reason naming
   the reviewer's verdict. BLOCKED is not demoted; it is not this loop's failure.

   `reentry_allowed` stays true for FAILED, and for DROVE while PROVEN sends are
   below `--quota-target`. The receipt's own `quota.met` is ignored: a worker must
   not be able to end its own day by typing a boolean.

   Keep every flag optional so existing calls without them still work.

2. `scripts/run_podcast_loop.sh` must actually pass them. Two places:

   a. Immediately after `QA_VERDICT=$(head -1 "$QA_FILE" ...)`, re-run the verdict
      computer with `--apply --qa-verdict "${{QA_VERDICT#QA: }}"` so a rejected
      receipt cannot keep a success verdict. This must happen before the case
      statement acts on the verdict.

   b. In the cross-loop block path inside the POST_RC failure branch, the existing
      recompute must pass `--blocked-by <owner>`. Note the owner is currently only
      available inside the `$REPAIR_JSON` string; there is no `REPAIR_OWNER`
      variable yet, so extract it before you use it or the script will die under
      `set -u`. Writing `blocked_by` into the receipt block is now pointless since
      the module ignores it; you may leave that code or remove it, but the
      `--blocked-by` flag is what must carry the fact.

   Change nothing else in that script. Escalation delivery, the budget guard, the
   traps, the referral preflight and the exit codes all stay exactly as they are.

HOW TO RUN THE CHECK — this is the command the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --worktree "$PWD"

It drives your module over thirteen fixtures, then EXECUTES the real runner twice
through `scripts/loop_shadow_run.py`, which stubs Ringer, Telegram, the Linear card
and secret_exec so nothing leaves the machine. That second half matters: three units
of this job passed checks that only grepped the runner, and all three were rejected
in review for defects in its control flow. Reading the script does not tell you what
it runs. You may run the harness yourself as often as you like; it is safe and it
verifies that it changed no file in the tree.

Also run `bash -n scripts/run_podcast_loop.sh` and
`python3 -m py_compile scripts/obe_loop_verdict.py`.

OUTPUT CONTRACT

- Your edits, uncommitted, in this worktree.
- ./fix-summary.md with `# Fix Summary`, `## Summary`, `## Files Changed`,
  `## Verification` and `## Assumptions`, under 900 words. Paste the real final
  output of the check command under `## Verification`, not a description of it.

If you cannot make the check pass inside those two files, do NOT make speculative
edits and do NOT weaken the check. Explain the blocker under `## Assumptions`.
"""


tasks = []
for suffix, engine, model, engine_args in LANES:
    key = f"{ROUND}-u0-corroboration-{suffix}"
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
            "no success verdict survives without corroboration the runner observed: an invented "
            "ceiling is not CAPPED, a worker-written blocked_by is not BLOCKED, an unproven send "
            "claim is not DROVE, a fabricated evidence path is not EXHAUSTED, a REVISE or BLOCK "
            "review demotes any success verdict, the receipt's quota.met cannot stop re-entry, "
            "and the real runner executed through the shadow harness applies all of it"
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

out = HERE / f"manifest-{ROUND}-u0-corroboration.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<30} {task['engine']:<12} spec={len(task['spec'])} chars")
