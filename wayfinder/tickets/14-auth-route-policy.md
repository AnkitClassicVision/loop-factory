---
title: Decide OAuth-expiry + engine-outage policy
status: closed
type: grilling
assignee: coordinator-fable
blocked_by: [07]
---

## Question

When a subscription/OAuth engine lane expires or an engine is down mid-run,
what happens: block-and-escalate semantics (never silently fall back to a
per-token API lane — hard rule 9), where the auth class + block event is
recorded in the run record (ticket 07), how the manager/heal ladder treats an
auth-blocked step vs a failed step, and what the human-in-the-loop outbox
message contains? Decide with Ankit; encode as charter-level policy all
departments inherit.

Asset: coordinator-drafted proposal ready for reaction at
`wayfinder/drafts/14-auth-policy-proposal.md` (5-point policy + 3 forks with
recommendations).

## Resolution

Ankit accepted the full proposal (2026-08-02): detect at wrapper; record
`status: blocked` / `auth_class: blocked` with AUTH_EXPIRED|ENGINE_DOWN codes;
never fall back to a metered lane; auth-block is an environment gate (no
heal-ladder strikes, no demotion); one outbox item per department per lane
with the exact re-auth command, plus a Linear card; manager re-probes each
cycle and queued work resumes idempotently. Forks: (1) only model-calling
steps pause — sensors/manager keep running; (2) escalation = outbox AND Linear
card; (3) **GLM is removed from the engine roster** (only forbidden metered
wiring exists — parked to fog for re-add if real subscription-plan wiring
lands). Policy detail: `wayfinder/drafts/14-auth-policy-proposal.md`
(ACCEPTED). Unblocks ticket 15 fully (07 ✓, 08 ✓, 14 ✓).
