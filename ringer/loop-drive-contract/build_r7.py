#!/usr/bin/env python3
"""Generate manifest-r7-wave1.json for the loop-drive-contract job.

Wave 1 of Fable's plan, owner-signed by Ankit 2026-08-10 ("build wave 1 using
/ringer"): U4 re-entry and the U7 gate module, in parallel, disjoint file
ownership. U2a is specced separately once the source-truth staleness finding is
resolved. Neither of these units can send anything.

Both checks were proven RED against HEAD (podcast 29a4a19) before this manifest
was generated: u4_reentry_check 14 named failures, u7_gates_check module-absent.

Edit LANES, then:  python3 build_r7.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
WRAPPER = f"{HERE}/checks/wave1_export_check.py"
ROUND = "r7"
WORKDIR = f"/mnt/d_drive/ringer-work/loop-drive-contract-{ROUND}"

COMMON_BOUNDARY = f"""You are a fix worker. Your current working directory IS a dedicated git
worktree of the repository {PODCAST}, detached at HEAD — edit the files here directly.

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
`scripts/obe_loop_verdict.py`. You may also create ./fix-summary.md in the worktree
root. Change nothing else.

THE DEFECT (all four observations made by EXECUTING the runner through
scripts/loop_shadow_run.py against HEAD on 2026-08-10):

  1. A FAILED verdict with `reentry_allowed: true` in the verdict JSON ends the
     run at exit 0. The runner quits; nothing re-enters. (scenario
     guest-reenter-then-exhausted: 1 invocation, verdict FAILED, exit 0)
  2. No receipt carries a re-entry count. (`REENTRY:` appears nowhere)
  3. Nothing meters worker time. The charter caps worker_minutes at 1680/week and
     `obe_loop_verdict.py` lists it as a real ceiling, but no ledger exists
     anywhere in the estate — this unit BUILDS it.
  4. A ledger already over budget changes nothing: the runner invokes the worker
     anyway. (scenario guest-worker-minutes-preseeded: EXHAUSTED, 1 invocation)

THE CONTRACT YOU ARE IMPLEMENTING (frozen; the validator enforces every line by
executing the real runner through the shadow harness):

1. WORKER-MINUTES LEDGER. Path: `${{LOOP_RECEIPT_DIR}}/worker-minutes-<ISOWEEK>.jsonl`
   where ISOWEEK is `date +%G-W%V` (e.g. 2026-W33). The RUNNER appends exactly one
   JSON row per Ringer invocation, after the invocation returns, even when ringer
   exits nonzero: {{"ts": ISO8601, "loop": "<loop>", "date_tag": "<YYYYMMDD>",
   "attempt": <0-based int>, "seconds": <wall-clock float>}}. Measure wall time
   around the `timeout ... ringer run` call.

2. PREFLIGHT CAP. Before the FIRST ringer invocation and again before EVERY
   re-entry: sum `seconds` across rows of the current ISO week's ledger file; if
   the total / 60 >= 1680, do not invoke ringer. First-pass case: write an honest
   runner-authored receipt to $RECEIPT (at least 600 bytes, evidence language,
   naming worker_minutes and the minutes consumed) and stamp it via
   `obe_loop_verdict.py --receipt "$RECEIPT" --apply --cap worker_minutes --reentry 0`.
   Re-entry case: re-stamp the existing receipt with `--cap worker_minutes
   --reentry <n>`. Exit 0 — CAPPED is a clean stop, and the estate's daily chain
   must keep running after it.

3. NEW FLAGS on `scripts/obe_loop_verdict.py` (keep every existing flag and
   behavior; calls without the new flags must work unchanged):
     --cap <ceiling>    runner-observed ceiling. When given and the name is one of
                        the real CAP_CEILINGS, the verdict is CAPPED regardless of
                        the receipt's own cap field (a flag comes from the runner's
                        execution; a receipt field comes from the worker — same
                        philosophy U0 established for --blocked-by). An invalid
                        name is an argument error (exit 2), never silently ignored.
     --reentry <int>    with --apply, maintain line 2 of the receipt as
                        `REENTRY: <int>` (insert it, or replace an existing
                        REENTRY line; line 1 stays the VERDICT line). The printed
                        JSON gains a "reentry" field echoing it. Without --reentry
                        the stamp is REENTRY: 0 on --apply.

4. RE-ENTRY LOOP in `scripts/run_podcast_loop.sh`. Today the verdict re-apply
   after `QA_VERDICT=$(head -1 ...)` discards the verdict JSON into $LOG. Capture
   it instead. Then, while the JSON says `"reentry_allowed":true`:
     a. run the preflight cap (rule 2); if it fires, stamp and exit 0 as above.
     b. fold the current receipt into history BEFORE the next pass: keep the file,
        and after the next invocation completes, append the superseded content
        under a `## Prior attempt <n> (superseded)` heading with every
        ``` loop-drive-v1 fence in the appended history rewritten (e.g. to
        ```loop-drive-v1-history) so exactly ONE live block remains in the file —
        `obe_loop_verdict.py` reads the FIRST live block and
        `loop_receipt_check.sh` requires the VERDICT line in the head.
     c. re-invoke ringer under the SAME run identity: same $RECEIPT, same $QA_FILE,
        same $DATE_TAG. Regenerate the manifest first with re-entry context
        appended to the worker spec (pass LOOP_REENTRY_ATTEMPT and the previous
        verdict reason through the environment into the heredoc): a paragraph
        stating "RE-ENTRY ATTEMPT <n>: the previous pass computed VERDICT FAILED
        because: <reason>. Address that failure; repeating the same output will
        fail again." Each invocation keeps its own `timeout ... $RUN_BUDGET_S`
        wrapper and appends its own ledger row (rule 1).
     d. after the invocation: existing nonzero-rc handling stays EXACTLY as it is
        (telegram + exit 1 — a crashed re-entry pass must stay loud). On rc 0,
        recompute the verdict with `--apply --qa-verdict ... --reentry <n>` and
        loop.
   Stop re-entering when the fresh JSON has `"reentry_allowed":false` (DROVE with
   quota met, EXHAUSTED, CAPPED, BLOCKED) — those paths exit exactly as today.
   Every first pass stamps `--reentry 0` so REENTRY: 0 appears in every receipt.

5. CHANGE NOTHING ELSE. Escalation delivery and its digest markers, the budget
   guard, the traps, the referral preflight, the QA case statement's REVISE/BLOCK
   handling, the cross-loop repair path, and every exit code stay exactly as they
   are. `--quota-target` stays at its default.

HOW TO RUN THE CHECK — this is the command the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {WRAPPER} --unit u4 --worktree "$PWD" \\
        --base {PODCAST} --patch {WORKDIR}/<key>.patch \\
        --summary fix-summary.md --exported-summary {WORKDIR}/<key>.summary.md

It first re-proves the defect against the live tree, then drives YOUR worktree's
runner through scripts/loop_shadow_run.py four times: re-enter-then-EXHAUSTED (2
invocations, REENTRY: 1, one live block, 'Prior attempt' section, 2 ledger rows),
CAPPED-no-re-entry (REENTRY: 0, 1 metered row), preseeded-over-budget (0
invocations, CAPPED naming worker_minutes, 600+ byte receipt), and
re-entry-worker-crash (nonzero exit, failure in the alert sink). The harness stubs
Ringer, Telegram, the Linear card and secret_exec — nothing leaves the machine, and
it verifies the driven tree is unchanged afterward. Run it yourself as often as you
like. Also run `bash -n scripts/run_podcast_loop.sh` and
`python3 -m py_compile scripts/obe_loop_verdict.py`.

OUTPUT CONTRACT

- Your edits, uncommitted, in this worktree.
- ./fix-summary.md with `# Fix Summary`, `## Summary`, `## Files Changed`,
  `## Verification` and `## Assumptions`, under 900 words. Paste the real final
  output of the check command under `## Verification`, not a description of it.

If you cannot make the check pass inside the two owned files, do NOT make
speculative edits and do NOT weaken or edit the check or the harness. Explain the
blocker under `## Assumptions`."""


def u7_spec() -> str:
    return f"""{COMMON_BOUNDARY}

You own exactly two tracked paths, both NEW files you create:
`server/pipeline/prose_gates.py` and `tests/test_prose_gates.py`. You may also
create ./fix-summary.md in the worktree root. Change nothing else — this unit is
deliberately NOT wired into any runner or pipeline (that is a later wave with
different ownership).

WHY THIS UNIT EXISTS. The 2026-08-06 audit found 5 of the runbooks' 12 gates are
prose — instructions a model is asked to follow, with nothing executing them. The
owner approved making them executable (D5). Wave 1 builds FOUR of the five as a
standalone module (the flagship-model gate is excluded: its inputs are not
recorded anywhere today, measured 2026-08-07). cross_model_qa_pass_before_done is
the highest-value gate: it being prose is why zero-send runs passed QA at all.

THE MODULE CONTRACT (frozen; the validator executes it 23 times):

  CLI    python3 -m server.pipeline.prose_gates --gate <name> --input <json-file>
         exit 0 = PASS.
         exit 2 = BLOCK; stdout's last line is ONE json object:
                  {{"gate", "violation", "offending_span", "fix_hint"}} — all four
                  values non-empty strings, "gate" equal to the gate invoked.
         exit 3 = unknown gate name, or missing/unreadable/invalid input file.
         Never exit 0 on anything it cannot positively verify. Fail closed.
         Stdlib only. The module reads the input file and (for the two gates
         below that name one) the file a payload field points at; it writes
         NOTHING anywhere.

  GATES and their input payloads:

  source_truth_resolved_before_intake
      {{"manifest_path": str, "max_age_days": int}}
      BLOCK when the manifest file is missing, unparseable JSON, lacks a parseable
      ISO-8601 `generated_at`, or `generated_at` is older than max_age_days from
      now (UTC). PASS otherwise. offending_span carries the stale timestamp or
      the missing path.

  channel_rule_cold_postcard_linkedin_warm_email_text
      {{"candidate": {{"alias": str, "temperature": str, "channel": str}}}}
      BLOCK when temperature is "cold" and channel is "email" or "text".
      BLOCK when temperature is not one of cold/warm, or channel is not one of
      email/text/postcard/linkedin (unknown values fail closed).
      PASS cold+postcard, cold+linkedin, and warm with any known channel.

  neutralize_preexisting_nominated_before_draft
      {{"candidate": {{"alias": str, "podcast_status": str,
                     "email_present": bool, "cleared_by_human": bool}}}}
      BLOCK when podcast_status == "nominated" AND email_present is true AND
      cleared_by_human is not true (CMQA-001: a pre-nominated candidate with a
      live email needs a human to clear it before any draft). PASS otherwise.

  cross_model_qa_pass_before_done
      {{"qa_file": str, "worker_model": str, "qa_model": str}}
      BLOCK when the QA file is missing or its first line is not exactly
      'QA: PASS'; when either model string is empty; or when worker_model equals
      qa_model case-insensitively — self-graded QA is the exact failure this
      gate exists to catch. PASS only when a different model wrote a PASS.

  Also write `tests/test_prose_gates.py` (pytest, stdlib + pytest only) covering
  every BLOCK and PASS case above, including the fail-closed edges (missing
  manifest, garbled JSON, unknown temperature, unknown channel, empty model,
  same-model case-insensitive). Tests must import the module, not shell out.

HOW TO RUN THE CHECK — this is the command the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {WRAPPER} --unit u7 --worktree "$PWD" \\
        --patch {WORKDIR}/<key>.patch \\
        --summary fix-summary.md --exported-summary {WORKDIR}/<key>.summary.md

It executes your module's CLI over randomized fixtures (aliases and dates change
every run — hardcoding fixture values cannot pass twice), asserts every BLOCK
returns the four-field JSON naming the right gate, asserts exit 3 on an unknown
gate and an unreadable input, and then runs your pytest file. Run it yourself as
often as you like.

OUTPUT CONTRACT

- Your two new files, uncommitted, in this worktree.
- ./fix-summary.md with `# Fix Summary`, `## Summary`, `## Files Changed`,
  `## Verification` and `## Assumptions`, under 900 words. Paste the real final
  output of the check command under `## Verification`.

If you cannot satisfy the contract, do NOT weaken or edit the check. Explain the
blocker under `## Assumptions`."""


LANES: list[tuple[str, str, str, str | None]] = [
    # (unit, key-suffix, engine, model)
    ("u4", "reentry", "codex", None),
    ("u7", "gates", "codex", None),
]

tasks = []
for unit, suffix, engine, model in LANES:
    key = f"{ROUND}-{unit}-{suffix}"
    base = f" --base {PODCAST}" if unit == "u4" else ""
    task: dict[str, object] = {
        "key": key,
        "engine": engine,
        "task_type": "code-feature",
        "timeout_s": 2400 if unit == "u4" else 1800,
        "spec": u4_spec() if unit == "u4" else u7_spec(),
        "check": (
            f"PYTHONDONTWRITEBYTECODE=1 python3 {WRAPPER} --unit {unit} --worktree \"$PWD\"{base} "
            f"--patch {WORKDIR}/{key}.patch "
            f"--summary fix-summary.md --exported-summary {WORKDIR}/{key}.summary.md"
        ),
        "expect_files": [f"{WORKDIR}/{key}.patch", f"{WORKDIR}/{key}.summary.md"],
        "verified": (
            "re-entry, metering, preflight cap and loud failure proven by executing the real runner "
            "through the shadow harness" if unit == "u4" else
            "all four prose gates block their violation, pass their fixture, and fail closed, proven "
            "by 23 executed CLI fixtures plus the unit's own pytest file"
        ),
    }
    if model:
        task["model"] = model
    tasks.append(task)

manifest = {
    "run_name": "loop-drive-contract",
    "workdir": WORKDIR,
    "repo": PODCAST,
    "worktrees": True,
    "max_parallel": len(tasks),
    "tasks": tasks,
}

out = HERE / "manifest-r7-wave1.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<20} {task['engine']:<8} timeout={task['timeout_s']} spec={len(task['spec'])} chars")
