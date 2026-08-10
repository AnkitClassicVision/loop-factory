# Coordinator review of wave 2 (r10), 2026-08-10 — NOT LANDED

CANARY: blue paperclip

Both lanes produced working code against green executed checks (receipts
`r10b-u2a-verify`, `r10b-u2b-verify`, both PASS attempt 1). Both are REJECTED in
coordinator review. This is instances 8 and 9 of the job's defining pattern: the
defect is invented by the FIX, so a check written from the DEFECT cannot see it.

Patches preserved at `/mnt/d_drive/ringer-work/loop-drive-contract-r10b/`.
Worktrees preserved at `/mnt/d_drive/ringer-work/loop-drive-contract-r10/`.

## F1 [HIGH] — U2a's source-truth gate validates a manifest the module just wrote

`server/pipeline/guest_outreach_draft.py`, in the r10 patch:

    _private_write(ledger_path, {"generated_at": <now>})
    ...
    payload = {"manifest_path": str(ledger_path), "max_age_days": 1} \
        if gate_name == "source_truth_resolved_before_intake" else ...

The module stamps a file with the current timestamp and then asks the freshness
gate whether that file is fresh. It always is. `source_truth_resolved_before_intake`
can never block. One of the four gates the entire U7 unit exists to make real is
decorative in its first consumer.

Correct input per `DECISION-source-truth-freshness.md`: the deterministic
REVALIDATION receipt, produced by something other than this module. The producer
must accept `source_truth_path` as a parameter and must NOT write it.

Why my check missed it: `u2a_producer_check` wires `gate_runner` to the real
`prose_gates` and asserts that cold and pre-nominated candidates block. It never
asserts that the source-truth gate CAN block. Blind spot, mine.

**New assertion required before the fix round runs:** a case where the
source-truth input is stale (or absent) must produce `status: gate_blocked` with
`source_truth_resolved_before_intake` named, and create zero drafts. Prove it red
against the r10 patch first — it will pass today, which is the bug.

## F2 [MEDIUM-HIGH] — U2b hardcodes `--reentry 0`, resetting a real re-entry count

In the new guest-acquisition branch, rc 0 path:

    obe_loop_verdict.py --receipt "$RECEIPT" --apply \
      --qa-verdict "${QA_VERDICT#QA: }" --sends-proof "$GUEST_DRAFT_RECEIPT" --reentry 0

That branch runs AFTER the re-entry while-loop, where `$REENTRY_ATTEMPT` holds the
final attempt count. Hardcoding 0 stamps `REENTRY: 0` onto a receipt that may have
taken several attempts — byte-identical in effect to r8 finding 5 on the
cross-loop path, which was fixed hours earlier in commit 9e13ab4. The same defect
class returned through a new call site.

Fix: pass `--reentry "$REENTRY_ATTEMPT"`.

**New assertion required:** a scenario that re-enters and then drafts must end
with `REENTRY: 1` (or higher), not 0.

## F3 [MEDIUM] — the HubSpot BCC is dropped

The producer calls `create_draft(..., bcc=None)`. The referral primitive it was
told to reuse defaults to `bcc=HUBSPOT_BCC`
(`server/pipeline/referral_touch_automation.py:279`), and the owner's standing
rule is that every outbound sales/outreach/partner email BCCs the HubSpot address
so the touch logs to CRM. Passing `bcc=None` silently opts guest outreach out of
CRM logging — the funnel would move without the CRM knowing.

Fix: default to the same `HUBSPOT_BCC` constant. Do not hardcode the address in a
second place; import it.

**New assertion required:** the fake Gmail service must record the bcc argument,
and a drafted candidate must carry a non-empty bcc.

## What was RIGHT, and should not be rebuilt

- U2a's ceiling check runs first, before any candidate is touched, and returns
  `capped` naming which ceiling. Both ceilings proven by execution.
- U2a deletes a voice-gate-rejected draft and carries both iterations. This is
  the single most important behavior in wave 2 and it works.
- U2a never reports `sent: true`, and its receipt is privacy-scrubbed (the check
  greps for `@`).
- U2b's containment mirrors the referral path exactly (`secret_exec`, env
  scrubbing, logging), handles all three exit codes, and builds its escalation
  from the receipt's status/violation/ceiling/iteration-COUNT — no bodies, no
  addresses, no raw ids.
- U2b correctly passes `--sends-proof "$GUEST_DRAFT_RECEIPT"`, which is what lets
  a created draft compute DROVE through the existing `proven_sends` path rather
  than a new key.

## Next round

One manifest, two lanes, same `run_name`. Write the three new assertions FIRST and
prove each red against the r10 patches, then let workers fix. Do not re-derive the
parts listed under "What was RIGHT".
