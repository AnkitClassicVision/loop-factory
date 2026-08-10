#!/usr/bin/env python3
"""Generate manifest-r10-wave2.json — U2a producer + U2b wiring.

Owner sign-off: Ankit, 2026-08-10, "sign off use /ringer", and on the send-gate
question he chose **ride the existing autosend lane now**. That choice is why
the ceiling work below is mandatory rather than optional.

Design decision this implements: ringer/loop-drive-contract/DECISION-u2a-draft-path.md
(committed a8994cf). Checks proven RED on HEAD before this manifest was written:
u2a_producer_check 7 failures.

Edit LANES, then:  python3 build_r10.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
CHECK = f"{HERE}/checks/u2a_producer_check.py"
ROUND = "r10"
WORKDIR = f"/mnt/d_drive/ringer-work/loop-drive-contract-{ROUND}"

BOUNDARY = f"""You are a fix worker. Your current working directory IS a dedicated git
worktree of the repository {PODCAST}, detached at HEAD — edit files here directly.

BOUNDARY, read this before anything else.

This repository sends real email. Two systemd services are LIVE right now: a
bridge that turns podcast Gmail drafts into approval cards every 30 minutes with
`--execute`, and an approved-send executor that SENDS every 10 minutes with
`--execute`. A Gmail draft that exists and passes the bridge's QA chain WILL be
sent to a real person without further human approval (owner promotion
2026-07-22). Treat every line you write as one step from a live send.

Never execute `scripts/run_podcast_loop.sh` directly. Never ssh. Never send a
Telegram message. Never run anything under server/ that performs an action
against Gmail, HubSpot, or Linear. Never create or edit systemd units. Never
touch ~/.config/ringer or .git. Do not load skills; do not call any MCP tool or
App. Do not git commit, branch, checkout, stash or push — leave work uncommitted;
the validator exports a patch. There IS a safe way to execute the runner, under
HOW TO RUN THE CHECK."""


def u2a_spec() -> str:
    return f"""{BOUNDARY}

You own exactly ONE new tracked path: `server/pipeline/guest_outreach_draft.py`.
You may also create ./fix-summary.md. Change nothing else — not the runner, not
the bridge, not referral_touch_automation. A second worker is wiring the runner
in parallel; touching it collides with them.

WHY THIS EXISTS. The guest-acquisition loop researches candidates but produces
nothing the send pipeline can carry. Today's live bridge run logged, verbatim:
`"execute": true, "candidates": 0, "results": []`. The conveyor is armed and
starved. Your module is what puts one letter on it.

WHAT YOU MUST BUILD — `run_guest_outreach_draft`, exact signature:

    run_guest_outreach_draft(
        *, candidates: list[dict], gmail_service, voice_gate, gate_runner,
        ledger_path: Path, receipt_path: Path, now=None, ceilings=None,
        sent_today: int = 0, new_contacts_today: int = 0, touches_this_week: int = 0,
    ) -> dict

  candidates    each {{"alias","temperature","channel","podcast_status",
                      "email_present","cleared_by_human","to","subject","body"}}
  gmail_service duck-typed: `.create_draft(*, to, subject, body, bcc=None) -> str`
                and `.delete_draft(draft_id)`. Do NOT import googleapiclient in
                this module; the caller injects the service. Reuse the primitive
                in `server/pipeline/referral_touch_automation.py:279` for the real
                caller, but this module only ever touches the injected object.
  gate_runner   (gate_name: str, payload: dict) -> {{"ok": bool, "violation": str|None}}
  voice_gate    (draft_id: str) -> {{"verdict": "pass"|"fail", "iterations": list,
                                    "receipt_path": str}}
  ceilings      defaults to the charter: outbound_per_day 12, new_contacts_per_day 5,
                weekly_touches 300

ORDER OF OPERATIONS, and the order is the safety property:

  1. CEILINGS FIRST, before touching a candidate. If sent_today >= outbound_per_day,
     or new_contacts_today >= new_contacts_per_day, or touches_this_week >=
     weekly_touches: return status "capped" with `ceiling` naming which one, create
     NOTHING. **This is the only place these ceilings exist.** Measured 2026-08-10:
     the bridge's `_caps_check` enforces only a 7-day per-recipient cooldown and 5
     cards per run; 12/day, 5 new contacts/day and 300/week are enforced NOWHERE
     downstream. A quota-driven loop feeding a lane with no daily ceiling is the
     exact hazard this module exists to prevent.
  2. GATES BEFORE GMAIL, per candidate, in this order, each via gate_runner:
     `source_truth_resolved_before_intake`, then
     `neutralize_preexisting_nominated_before_draft`, then
     `channel_rule_cold_postcard_linkedin_warm_email_text`.
     Any block: record it, DO NOT create a draft, move to the next candidate. A
     COLD candidate must never produce a Gmail draft; the charter's cold rule is
     postcard and LinkedIn only, and a draft in this estate is a pending send.
  3. DRAFT, once, for the first candidate that clears every gate.
  4. VOICE GATE on that draft id. On verdict "fail": **DELETE the draft**
     (`gmail_service.delete_draft`), set `draft_deleted: true`, carry the
     `iterations` array into the receipt, return status "gate_blocked". Leaving a
     rejected draft alive means the live bridge cards it within 30 minutes and
     the executor autosends it — a letter your own gate refused.
  5. On verdict "pass": status "drafted", `drafts_created: 1`.

RETURN AND RECEIPT (write the same dict to receipt_path, mode 0600):
  schema "obe.guest.outreach.draft.v1"; status one of
  drafted|gate_blocked|no_candidate|capped|error; drafts_created int;
  **sent: false ALWAYS** (this module never sends); candidate_key str;
  draft_deleted bool; gates dict; violation str|None; iterations list on
  gate_blocked; ceiling str|None on capped.

PRIVACY, enforced by the check: the receipt is pasted into Telegram. Never put an
email address, a raw contact/draft id, or letter body text in it. Aliases and
gate names only. The check greps for an "@" and fails.

Module CLI (`python3 -m server.pipeline.guest_outreach_draft`) exits 0 drafted,
2 no legal candidate (gate_blocked, capped, no_candidate — a clean stop), 1 error.

HOW TO RUN THE CHECK — exactly what the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --repo "$PWD" --module-only \\
        --out /tmp/u2a-selfcheck

It drives your module directly with a fake Gmail service that RECORDS every
create and delete, and wires gate_runner to the REAL
`server/pipeline/prose_gates.py`. Seven cases: warm drafts once; cold creates
nothing; pre-nominated creates nothing; a voice-gate failure creates then
DELETES and carries exactly two iterations; the daily and weekly ceilings each
create nothing and name themselves.

OUTPUT CONTRACT: your new file, uncommitted, plus ./fix-summary.md with
`# Fix Summary`, `## Summary`, `## Files Changed`, `## Verification`,
`## Assumptions`, under 900 words, pasting the real final check output under
Verification.

Do not weaken or edit the check. If blocked, explain under Assumptions."""


def u2b_spec() -> str:
    return f"""{BOUNDARY}

You own exactly ONE tracked path: `scripts/run_podcast_loop.sh`. You may also
create ./fix-summary.md. Change nothing else. A second worker is writing
`server/pipeline/guest_outreach_draft.py` in parallel — you WIRE it, you do not
write it, and it may not exist in your worktree. Write the call as specified.

WHAT YOU ARE WIRING. After the guest-acquisition loop's cross-model QA returns
`QA: PASS`, the runner must invoke the draft producer, then act on its exit code.
Mirror the referral-flywheel invocation that already exists in the `"QA: PASS")`
case: same `secret_exec.py --secret-env HUBSPOT_API_KEY=...` containment, same
`env -u PLACEHOLDER_MODE GMAIL_CREDENTIALS_PATH=... GMAIL_FULL_TOKEN_PATH=...`
shape, same `>> "$LOG" 2>&1` logging.

    Producer:  "$PYTHON_BIN" -m server.pipeline.guest_outreach_draft
               --receipt "$GUEST_DRAFT_RECEIPT" --ledger "$GUEST_DRAFT_LEDGER"
    Receipt:   $RECEIPT_DIR/guest-acquisition-${{DATE_TAG}}.draft.json
    Ledger:    $RECEIPT_DIR/guest-outreach-ledger.json

EXIT-CODE CONTRACT — this is the whole unit:

  rc 0  a draft was created. Recompute the verdict passing the producer receipt
        as corroboration:
          obe_loop_verdict.py --receipt "$RECEIPT" --apply --qa-verdict ...
            --sends-proof "$GUEST_DRAFT_RECEIPT" --reentry "$REENTRY_ATTEMPT"
        (The verdict computer already reads `drafts_created` from a sends-proof
        artifact — see `proven_sends` in obe_loop_verdict.py. Do not invent a new
        key.) Exit as the loop normally would.

  rc 2  no legal candidate: the voice gate blocked the letter, a prose gate
        refused, or a charter ceiling was reached. This is a CLEAN STOP, exit 0.
        Deliver ONE escalation naming what stopped it, built from the producer
        receipt's `status`, `violation`, `ceiling`, and the COUNT of `iterations`
        — never the letter body, never a recipient, never a raw id. The receipt
        is privacy-scrubbed by the producer; do not add anything to it.

  rc 1  the producer crashed. Loud failure: telegram + exit 1, exactly like the
        existing referral post-QA failure path.

Do not add a re-entry, do not touch the worker-minutes ledger, do not change the
thrash detector, the traps, the budget guard, the escalation digest markers, or
any exit code outside the guest-acquisition QA-PASS branch.

HOW TO RUN THE CHECK — exactly what the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --repo "$PWD" --runner-only \\
        --out /tmp/u2b-selfcheck

It EXECUTES the real runner through `scripts/loop_shadow_run.py` for three
scenarios — producer drafted, producer gate-blocked, producer crashed — with
Ringer, Telegram, the Linear card and secret_exec all stubbed, so nothing leaves
the machine. Grep-shaped work fails here: four earlier rounds of this job passed
checks that only read the script and were rejected in review. Also run
`bash -n scripts/run_podcast_loop.sh`.

OUTPUT CONTRACT: your edit, uncommitted, plus ./fix-summary.md with
`# Fix Summary`, `## Summary`, `## Files Changed`, `## Verification`,
`## Assumptions`, under 900 words, pasting the real final check output under
Verification.

Do not weaken or edit the check or the harness. If blocked, explain under
Assumptions."""


LANES = [
    ("u2a", "producer", "codex", 2400, ["server/pipeline/guest_outreach_draft.py"], "--module-only"),
    ("u2b", "wiring", "codex", 2400, ["scripts/run_podcast_loop.sh"], "--runner-only"),
]

tasks = []
for unit, suffix, engine, timeout, owned, mode in LANES:
    key = f"{ROUND}-{unit}-{suffix}"
    owned_args = " ".join(f"--owned {path}" for path in owned)
    tasks.append({
        "key": key,
        "engine": engine,
        "task_type": "code-feature",
        "timeout_s": timeout,
        "spec": u2a_spec() if unit == "u2a" else u2b_spec(),
        "check": (
            f"PYTHONDONTWRITEBYTECODE=1 python3 {HERE}/checks/wave2_export_check.py "
            f"--worktree \"$PWD\" --mode {mode} {owned_args} "
            f"--patch {WORKDIR}/{key}.patch "
            f"--summary fix-summary.md --exported-summary {WORKDIR}/{key}.summary.md"
        ),
        "expect_files": [f"{WORKDIR}/{key}.patch", f"{WORKDIR}/{key}.summary.md"],
        "verified": (
            "cold never drafts, a voice-rejected draft is deleted before the live bridge can card it, "
            "and the charter volume ceilings bite in the only place they exist"
            if unit == "u2a" else
            "the real runner invokes the producer and handles all three exit codes, proven by executing "
            "the runner through the shadow harness"
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

out = HERE / "manifest-r10-wave2.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<18} {task['engine']:<8} timeout={task['timeout_s']} spec={len(task['spec'])} chars")
