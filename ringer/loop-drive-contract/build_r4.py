#!/usr/bin/env python3
"""Generate manifest-r4-u1-verdict.json for the loop-drive-contract job.

Round 4 is U1: the loop verdict stops being something the worker types and
becomes something a deterministic step computes from the receipt.

The machine-readable contract in the spec below is a COORDINATOR decision, not a
worker's design choice — U6, U4 and U2 all key on it, so it is stated verbatim
here and in every later round rather than left to be invented per lane.

Edit LANES, then:  python3 build_r4.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
CHECK = f"{HERE}/checks/u1_verdict_check.py"
# r4 passed its check and was rejected in review: --apply overwrote the receipt's
# first line even when that line was not a verdict, and the new prompt no longer
# asks the worker for one, so the receipt title was the likely casualty. r4b
# rebuilds from HEAD with that finding in the spec (requirement 6).
ROUND = "r4b"
WORKDIR = f"/mnt/d_drive/ringer-work/loop-drive-contract-{ROUND}"
OWNED = "scripts/obe_loop_verdict.py, scripts/loop_receipt_check.sh, scripts/run_podcast_loop.sh"

LANES: list[tuple[str, str, str | None, list[str]]] = [
    ("codex", "codex", None, []),
]


def spec() -> str:
    return f"""You are a fix worker. Your current working directory IS a dedicated git
worktree of the repository {PODCAST}, detached at HEAD — edit the files here directly.

BOUNDARY, read this before anything else.

You own exactly three tracked paths: `scripts/obe_loop_verdict.py` (you create it),
`scripts/loop_receipt_check.sh`, and `scripts/run_podcast_loop.sh`. You may also
create ./fix-summary.md in the worktree root. Change nothing else. Do not touch .git.
Do not run git commit, branch, checkout, stash or push — leave your work uncommitted;
a validator exports it as a patch.

This repository drives seven live podcast loops that page a human over Telegram and
can send real email. So: never execute `scripts/run_podcast_loop.sh`, never ssh
anywhere, never send a Telegram message, never run anything under server/ or any
script whose name suggests it sends, drafts, publishes, or writes to a CRM. Do not
create or edit systemd units. Do not load skills and do not call any MCP tool or App;
nothing about this task should be captured to any memory or ledger backend.

THE DEFECT

A loop run today succeeds by saying so. The worker writes the receipt AND its own
first line, `VERDICT: OK | NEEDS_ANKIT | BLOCKED`, and `scripts/loop_receipt_check.sh`
only checks that one of those words is present. On 2026-08-06 all five due loops ran,
reported `outreach +0; first responses +0; conversations +0; pre-call +0; booked +0`,
and passed. The guest pipeline was 3 against a required 6. Zero sends earned a
passing grade because nothing computed anything from the numbers in the receipt.

The owner's decision (D1, 2026-08-06): "if 0 sent that is not success, only success
is you actually sent them out."

THE CONTRACT YOU ARE IMPLEMENTING

This shape is fixed. Later units (re-entry, cross-loop blocking, the repair loop) all
read it, so do not redesign it, rename its fields, or "improve" the JSON.

Every loop receipt carries a fenced block, anywhere in the file:

    ```loop-drive-v1
    {{"sends": 0,
      "cap": null,
      "blocked_by": null,
      "quota": {{"target": 2, "met": false}},
      "candidates": [
        {{"alias": "cand-1", "eligible": true, "disqualifier": null, "evidence": null}},
        {{"alias": "cand-2", "eligible": false,
          "disqualifier": "inside the 4-day per-contact cadence floor",
          "evidence": "episodes/_loop_receipts/guest-acquisition-candidate-ledger.json:contacted_at"}}
      ]}}
    ```

`cap` is null or `{{"ceiling": "<name>", "limit": <number>}}`. `blocked_by` is null or
the name of the loop that owns the upstream gate. Aliases are sanitized; no real
names, emails or phone numbers ever go in a receipt.

1. NEW FILE `scripts/obe_loop_verdict.py`, invoked as:

       python3 scripts/obe_loop_verdict.py --receipt <path> [--apply]

   It prints exactly one line of JSON on stdout:

       {{"verdict": "...", "reason": "...", "sends": <int>, "reentry_allowed": <bool>}}

   With `--apply` it also rewrites the receipt's FIRST LINE to `VERDICT: <verdict>`,
   replacing whatever the worker put there. Running it twice must produce the same
   file: re-entry will do exactly that. Exit 0 when a verdict was computed, exit 2
   when the receipt cannot be read.

2. THE FIVE VERDICTS, computed in this precedence order. The order matters: every
   verdict that must STOP re-entry outranks one that may continue, so that a later
   unit can key on the verdict word alone.

       CAPPED     `cap` is set. A named hard ceiling stopped the run. Clean success.
       BLOCKED    `blocked_by` is set. An upstream gate owned by another loop.
       DROVE      sends > 0.
       EXHAUSTED  sends == 0, the candidate list is NON-EMPTY, and every candidate is
                  ineligible with BOTH a non-empty disqualifier and non-empty evidence.
       FAILED     everything else.

   FAILED therefore covers: zero sends with at least one eligible candidate; any
   candidate whose disqualifier lacks evidence; an empty candidate list; and a
   receipt with no loop-drive-v1 block at all or an unparseable one. That last case
   must not crash — a missing block is an unproven zero, which is a FAILED verdict,
   not a stack trace.

3. `reentry_allowed` is true for FAILED, and true for DROVE when `quota.met` is
   false. It is false for EXHAUSTED, CAPPED, BLOCKED, and for DROVE with the quota
   met. Nothing consumes this yet; the re-entry unit will.

4. `scripts/loop_receipt_check.sh` accepts exactly the five computed verdicts and
   rejects anything else, including today's `OK` and `NEEDS_ANKIT`. Keep every other
   assertion in that script — the size floor, the evidence-language grep and above
   all the secret-scan — intact.

5. `scripts/run_podcast_loop.sh`: after the worker pass produces the receipt and
   BEFORE the receipt is judged, run the verdict computer with `--apply` so the
   receipt carries a computed verdict from then on. Update the worker prompt so it
   asks for the loop-drive-v1 block and stops asking the worker to pick OK or
   NEEDS_ANKIT. Update the reviewer prompt too: EXHAUSTED is now a success verdict a
   lazy worker could buy by calling good candidates ineligible, so the reviewer must
   spot-check at least one candidate disqualifier in the block against the file or
   thread it cites. Do not weaken any existing safety behaviour in that script —
   escalation delivery, the QA verdict handling, the budget guard and the traps all
   stay exactly as they are.

6. `--apply` REPLACES an existing `VERDICT:` first line, and otherwise INSERTS the
   computed verdict above the existing content. This is a real finding from an earlier
   attempt at this same unit, so do not rediscover it the hard way: that attempt did
   `lines[0] = "VERDICT: " + verdict` unconditionally. Since requirement 5 removes the
   instruction telling the worker to write a verdict line at all, the receipt's first
   line will usually be its markdown title — and that attempt silently deleted it.
   Losing a line of the receipt is losing evidence.

HOW TO RUN THE CHECK — this is the command the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --worktree "$PWD"

It first proves the defect still reproduces against the pristine
`git show HEAD:scripts/loop_receipt_check.sh` (a zero-send `VERDICT: OK` receipt
passes today), then drives your module over nine fixture receipts, checks that
`--apply` overwrites and is idempotent, checks `reentry_allowed` for every verdict,
and greps the runner for the wiring. Every failure names the case and says what was
expected. Run it before you start, to see the seven expected failures.

Also run `bash -n scripts/run_podcast_loop.sh` and
`bash -n scripts/loop_receipt_check.sh`.

OUTPUT CONTRACT

- Your edits, uncommitted, in this worktree.
- ./fix-summary.md with `# Fix Summary`, `## Summary`, `## Files Changed`,
  `## Verification` and `## Assumptions`, under 900 words. Paste the real final
  output of the check command under `## Verification`, not a description of it.

If you cannot make the check pass inside those three files, do NOT make speculative
edits and do NOT weaken the check. Explain the blocker under `## Assumptions` so the
red is readable. A wrong fix that ships is worse than a red check that explains itself.
"""


tasks = []
for suffix, engine, model, engine_args in LANES:
    key = f"{ROUND}-u1-verdict-{suffix}"
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
            "the verdict is computed from the receipt's loop-drive-v1 block, not authored by "
            "the worker: all five verdicts fall out in the right precedence, every unproven "
            "zero lands on FAILED, --apply overwrites the worker's line idempotently, "
            "reentry_allowed is correct, and the runner both computes the verdict and asks "
            "for the block"
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

out = HERE / f"manifest-{ROUND}-u1-verdict.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<28} {task['engine']:<12} spec={len(task['spec'])} chars")
