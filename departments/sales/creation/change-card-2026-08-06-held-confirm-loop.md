# Change card — held-confirm loop (2026-08-06)

**What changes:** new SG-HELD node `runtime/held_confirm_card.py` (cards-v2,
emits_ask, 48h SLA) plus a confirmation overlay in `runtime/held_sensor.py`.
When a booked subject's calendar event has passed with `attended=true` but the
source cannot attest a decision-maker (the HubSpot meetings fetcher hard-falses
`decision_maker_present` by design), the node writes ONE pending row to
`state/held_confirm_queue.jsonl` and ONE cards-v2 packet to the human outbox
asking the owner: did this call hold >= 20 minutes with a decision-maker
present? One ask per SUBJECT, for its most recent eligible event only — one
confirmed event holds the subject; a declined answer falls back to the next
event on a later run, and no new ask stacks while one is pending. (First live
shadow without this rule: 26 cards for 2 recurring-call subjects.) The answer (applied with `factory/human_in_the_loop.py apply
--queue .../held_confirm_queue.jsonl --decision-id sales-held-<event_id>
--verdict APPROVE|REJECT`) becomes a durable row in
`state/held_confirmations.jsonl`. `held_sensor` treats a confirmed event as
decision-maker-attested; everything else about held eligibility is unchanged.
Daily chain gains the node between `booked_sensor` and `held_sensor`; roster
gains the obligation (schema rev stays 1).

**Why:** the funnel's held stage is starved by construction. C1 locked HELD as
attended + decision-maker present + >= 20 minutes, and the calendar source can
only ever prove the first. Without an owner attestation loop, real bookings
(4 as of the 2026-08-06 ledger, epoch 7) can never become real held receipts.
This is the handoff's named blocker: "the ONLY blocker to real held receipts."

**Evidence chain note:** the held receipt keeps the CALENDAR minutes and, for
confirmed events, adds `confirmed_by: sales-held-<event_id>` — the decision id
traces to the card whose approve token attests ">= 20 minutes with a
decision-maker present". A receipt with calendar minutes < 20 plus a
`confirmed_by` is therefore owner-attested, not fabricated; the attestation
artifact is the confirmation row, referenced by id.

**Intent unchanged:** HELD's meaning (C1, locked at readback) does not move.
Deny-by-default stands: no confirmation means no held transition, and silence
never confirms (C6, Q12: 48h SLA, silence never approves). Approve is
safe-forward per the 2026-08-05 card-polarity rule; "did not hold" rides
REJECT/FIX. Map edits: SG-HELD gains node N3 (concept C6, interview Q12) and
C6 joins its concept_refs; the S4 rationale now names the outbox escalation.

**QA path (process-change-qa.md):** maps patched first → `loopfactory.py
validate --name sales` PASS → node + overlay authored with tests →
`loopfactory.py check` PASS → re-shadow changed nodes (simulated outbox,
delivered_count==0, external_actions_taken==0) → `release pin --flip` →
`loopfactory.py qa --name sales` drift-clean.

**Owner decision trail:** Ankit, 2026-08-06, handoff
`~/handoffs/2026-08-06-loop-factory-evening.md` ONE next pickup action:
"Build the cards-v2 held-confirm card (podcast card pattern, sales outbox)";
resume command executed this session. Held definition: intent-interview Q1
(INTENT LOCKED 2026-08-06). Card format: Q12 + owner card directive.
