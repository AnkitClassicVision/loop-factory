#!/usr/bin/env python3
"""Generate manifest-r11-wave2-fixes.json — repair the four r10 review findings.

Findings and evidence: ringer/loop-drive-contract/REVIEW-r10-wave2.md.
All four are proven RED by execution before this manifest was generated:
the module half now fails on the changed signature, and the runner half fails
`guest-producer-reenter-then-draft` with exit 0 / verdict FAILED / 1 invocation.

Engines (Ankit's routing rule, 2026-08-10): codex/sol does the typing; the
adversarial reviewer that replaces Fable is chosen from his named list on the
local scoreboard's evidence — see build_r12 for the review round.

Edit LANES, then:  python3 build_r11.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
CHECK = f"{HERE}/checks/u2a_producer_check.py"
ROUND = "r11"
WORKDIR = f"/mnt/d_drive/ringer-work/loop-drive-contract-{ROUND}"

BOUNDARY = f"""You are a fix worker. Your current working directory IS a dedicated git
worktree of the repository {PODCAST}, detached at HEAD — edit files here directly.
HEAD does NOT contain the wave-2 code; you are writing it, informed by a review of
a rejected first attempt.

BOUNDARY, read this before anything else.

This repository sends real email. Two systemd services are LIVE: a bridge that
turns podcast Gmail drafts into approval cards every 30 minutes with `--execute`,
and an approved-send executor that SENDS every 10 minutes with `--execute`. A
Gmail draft that exists and passes the bridge's QA chain WILL be sent to a real
person without further human approval (owner promotion 2026-07-22). Treat every
line you write as one step from a live send.

Never execute `scripts/run_podcast_loop.sh` directly. Never ssh. Never send a
Telegram message. Never run anything under server/ that acts against Gmail,
HubSpot, or Linear. Never create or edit systemd units. Never touch
~/.config/ringer or .git. Do not load skills; do not call any MCP tool or App. Do
not git commit, branch, checkout, stash or push — leave work uncommitted; the
validator exports a patch. There IS a safe way to execute the runner, under HOW
TO RUN THE CHECK."""


def u2a_spec() -> str:
    return f"""{BOUNDARY}

You own exactly ONE new tracked path: `server/pipeline/guest_outreach_draft.py`.
You may also create ./fix-summary.md. Change nothing else.

A first attempt at this module was built and REJECTED in review. Two of its
defects are yours to avoid; everything else it did was right and is described
below so you can rebuild it deliberately rather than rediscover it.

REJECTED DEFECT 1 — the gate that could never fail. The first attempt wrote its
own "source-truth manifest" stamped with the current time, then asked the
freshness gate whether that file was fresh. It always was. A gate whose input the
checked party authors is decorative. **The source-truth evidence is supplied BY
THE CALLER; this module must never write it.** Signature therefore takes
`source_truth_path: Path`, and the source_truth gate payload is
`{{"manifest_path": str(source_truth_path), "max_age_days": 7}}`.

REJECTED DEFECT 2 — the dropped HubSpot BCC. The first attempt called
`create_draft(..., bcc=None)`, which would silently keep every guest touch out of
the CRM. Import and pass the same `HUBSPOT_BCC` constant that
`server/pipeline/referral_touch_automation.py` already defaults to. Do not
hardcode the address a second time.

WHAT YOU MUST BUILD — `run_guest_outreach_draft`, exact signature:

    run_guest_outreach_draft(
        *, candidates: list[dict], gmail_service, voice_gate, gate_runner,
        ledger_path: Path, receipt_path: Path, source_truth_path: Path,
        now=None, ceilings=None, sent_today: int = 0,
        new_contacts_today: int = 0, touches_this_week: int = 0,
    ) -> dict

  candidates    each {{"alias","temperature","channel","podcast_status",
                      "email_present","cleared_by_human","to","subject","body"}}
  gmail_service duck-typed: `.create_draft(*, to, subject, body, bcc=None) -> str`
                and `.delete_draft(draft_id)`. Do NOT import googleapiclient here;
                the caller injects the service.
  gate_runner   (gate_name: str, payload: dict) -> {{"ok": bool, "violation": str|None}}
  voice_gate    (draft_id: str) -> {{"verdict": "pass"|"fail", "iterations": list,
                                    "receipt_path": str}}
  ceilings      defaults to the charter: outbound_per_day 12, new_contacts_per_day 5,
                weekly_touches 300

ORDER OF OPERATIONS, and the order IS the safety property:

  1. CEILINGS FIRST, before touching any candidate. If sent_today >=
     outbound_per_day, or new_contacts_today >= new_contacts_per_day, or
     touches_this_week >= weekly_touches: return status "capped" with `ceiling`
     naming which one; create NOTHING. **This is the only place these ceilings
     exist.** Measured 2026-08-10: the live bridge enforces only a 7-day
     per-recipient cooldown and 5 cards per run. 12/day, 5 new contacts/day and
     300/week are enforced NOWHERE downstream.
  2. GATES BEFORE GMAIL, per candidate, in order, each through gate_runner:
     `source_truth_resolved_before_intake` (payload above, from the CALLER's
     path), then `neutralize_preexisting_nominated_before_draft`, then
     `channel_rule_cold_postcard_linkedin_warm_email_text` (both payload
     `{{"candidate": candidate}}`). Any block: record it, create no draft, move on
     to the next candidate. A COLD candidate must never produce a Gmail draft.
  3. DRAFT once, for the first candidate that clears every gate, WITH the HubSpot
     BCC.
  4. VOICE GATE on that draft id. On "fail": DELETE the draft, set
     `draft_deleted: true`, carry the `iterations` array into the receipt, return
     status "gate_blocked". A rejected draft left alive gets carded by the live
     bridge within 30 minutes and autosent.
  5. On "pass": status "drafted", `drafts_created: 1`.

RETURN AND RECEIPT (write the same dict to receipt_path, mode 0600):
  schema "obe.guest.outreach.draft.v1"; status
  drafted|gate_blocked|no_candidate|capped|error; drafts_created int;
  **sent: false ALWAYS**; candidate_key; draft_deleted bool; gates dict;
  violation str|None; iterations list on gate_blocked; ceiling str|None on capped.

PRIVACY, enforced by the check: this receipt is pasted into Telegram. No email
addresses, no raw contact/draft ids, no letter body. Aliases and gate names only.

Module CLI (`python3 -m server.pipeline.guest_outreach_draft`) exits 0 drafted,
2 no legal candidate (gate_blocked|capped|no_candidate), 1 error.

HOW TO RUN THE CHECK — exactly what the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --repo "$PWD" --module-only \\
        --out /tmp/u2a-selfcheck

Eight cases: warm drafts once WITH a HubSpot BCC; a STALE caller-supplied
source-truth receipt blocks and drafts nothing; cold drafts nothing;
pre-nominated drafts nothing; a voice failure creates then DELETES and carries
exactly two iterations; the daily and weekly ceilings each draft nothing and name
themselves. The fake Gmail service records every create and delete, and the gates
are the REAL `server/pipeline/prose_gates.py`.

OUTPUT CONTRACT: your new file, uncommitted, plus ./fix-summary.md with
`# Fix Summary`, `## Summary`, `## Files Changed`, `## Verification`,
`## Assumptions`, under 900 words, pasting the real final check output under
Verification. Do not weaken or edit the check."""


def u2b_spec() -> str:
    return f"""{BOUNDARY}

You own exactly ONE tracked path: `scripts/run_podcast_loop.sh`. You may also
create ./fix-summary.md. Change nothing else. A second worker writes
`server/pipeline/guest_outreach_draft.py` in parallel — you WIRE it; it may not
exist in your worktree.

A first attempt was built and REJECTED in review for two defects. Both are about
WHERE your code sits, not what it says.

REJECTED DEFECT 1 [the serious one] — it bypassed the re-entry contract. The
first attempt put the guest-acquisition branch AHEAD of the re-entry `while` loop
and ended every path with its own `exit 0`. Executed through the harness, that
produced `exit_code 0` on a `verdict FAILED` run with `reentry_allowed true` and
ONE ringer invocation: for the very loop the re-entry contract exists to serve,
re-entry never ran and a failed run reported success. **Your branch must sit
where the referral post-QA action sits — inside the `"QA: PASS")` case, AFTER the
re-entry loop has settled the verdict — and must not introduce an exit that
preempts the runner's existing exit paths.**

REJECTED DEFECT 2 — it hardcoded `--reentry 0` when recomputing the verdict,
stamping REENTRY: 0 onto a receipt that had genuinely re-entered. That is the
same defect class fixed hours earlier on the cross-loop path. Pass
`--reentry "$REENTRY_ATTEMPT"`.

WHAT YOU ARE WIRING. In the `"QA: PASS")` case, for `guest-acquisition` only,
mirroring the referral invocation's containment exactly (`secret_exec.py
--secret-env HUBSPOT_API_KEY=...`, `env -u PLACEHOLDER_MODE
GMAIL_CREDENTIALS_PATH=... GMAIL_FULL_TOKEN_PATH=...`, `>> "$LOG" 2>&1`):

    "$PYTHON_BIN" -m server.pipeline.guest_outreach_draft
      --receipt "$GUEST_DRAFT_RECEIPT" --ledger "$GUEST_DRAFT_LEDGER"
    GUEST_DRAFT_RECEIPT=$RECEIPT_DIR/guest-acquisition-${{DATE_TAG}}.draft.json
    GUEST_DRAFT_LEDGER=$RECEIPT_DIR/guest-outreach-ledger.json

EXIT-CODE CONTRACT:

  rc 0  a draft exists. Recompute the verdict with the producer receipt as
        corroboration:
          obe_loop_verdict.py --receipt "$RECEIPT" --apply --qa-verdict ...
            --sends-proof "$GUEST_DRAFT_RECEIPT" --reentry "$REENTRY_ATTEMPT"
        (`proven_sends` already reads `drafts_created` from a sends-proof
        artifact; do not invent a key.) Then let the runner exit as it already
        does for a successful loop.
  rc 2  no legal candidate — gate blocked, or a charter ceiling reached. A clean
        stop. Deliver ONE escalation built from the producer receipt's `status`,
        `violation`, `ceiling` and the COUNT of `iterations`; never the letter
        body, a recipient, or a raw id. Do not fail the unit.
  rc 1  the producer crashed: telegram + exit 1, exactly like the existing
        referral post-QA failure path.

Change nothing else: not the re-entry loop, not the worker-minutes ledger, not
the thrash detector, not the traps, not the budget guard, not the escalation
digest markers, not any other loop's exits.

HOW TO RUN THE CHECK — exactly what the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --repo "$PWD" --runner-only \\
        --out /tmp/u2b-selfcheck

It EXECUTES the real runner through `scripts/loop_shadow_run.py` for four
scenarios: producer drafted, producer gate-blocked, producer crashed, and
**re-enter-then-draft** (which must show TWO ringer invocations and a receipt
stamped REENTRY: 1, not 0). Ringer, Telegram, the Linear card and secret_exec are
all stubbed; nothing leaves the machine. Also run
`bash -n scripts/run_podcast_loop.sh`.

OUTPUT CONTRACT: your edit, uncommitted, plus ./fix-summary.md with
`# Fix Summary`, `## Summary`, `## Files Changed`, `## Verification`,
`## Assumptions`, under 900 words, pasting the real final check output under
Verification. Do not weaken or edit the check or the harness."""


LANES = [
    ("u2a", "producer", "codex", 2700, ["server/pipeline/guest_outreach_draft.py"], "module"),
    ("u2b", "wiring", "codex", 2700, ["scripts/run_podcast_loop.sh"], "runner"),
]

tasks = []
for unit, suffix, engine, timeout, owned, mode in LANES:
    key = f"{ROUND}-{unit}-{suffix}"
    owned_args = " ".join(f"--owned {path}" for path in owned)
    tasks.append({
        "key": key,
        "engine": engine,
        "task_type": "code-fix",
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
            "a stale caller-supplied source truth blocks, cold never drafts, the HubSpot BCC rides every "
            "draft, a voice-rejected draft is deleted, and both charter ceilings bite"
            if unit == "u2a" else
            "the guest branch no longer preempts the re-entry contract: a re-entered run that drafts shows "
            "two invocations and keeps its REENTRY count, proven by executing the real runner"
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

out = HERE / "manifest-r11-wave2-fixes.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<20} {task['engine']:<8} timeout={task['timeout_s']} spec={len(task['spec'])} chars")
