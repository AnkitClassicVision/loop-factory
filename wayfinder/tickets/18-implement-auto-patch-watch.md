---
title: Implement the gated auto-patch watch loop
status: closed
type: task
assignee: coordinator-fable
blocked_by: [13, 16]
---

## Question

Implement ticket 13's locked v2 contract: the full L0–L5 repair ladder
(retry → known-fix playbook → self-patch → cross-model repair via Ringer
one-task manifest → contain-and-degrade → human with dossier), including
wiring the EXISTING heal ladder into the daily chain (audit gap #7 — the
prerequisite plumbing), the 10/week budget, 3-fail demotion with 7-clean-day
auto-reset, receipts at every rung, and rollback to last-good pin. Executed proof in
shadow: one synthetic qualifying failure heals end-to-end with receipts; one
NON-qualifying failure (e.g. touching a governance file) is refused and
escalates to the outbox. Promotion of the class itself = owner sign-off per
promotion-ladder.md.

## Resolution

Complete (2026-08-02, round 4). factory/selfheal.py implements the locked
v2 ladder as a deterministic state machine: L0 retry -> L1 playbook -> L2
self-patch proposal -> L3 cross-model proposal -> L4 contain-and-degrade ->
L5 dossier; always-human fix classes route straight to L5; 10/week budget
breaches contain at L4; 3 cumulative failures demote with 7-clean-day
auto-reset; HARD FLOOR: propose-only (auto_apply: false on every card) —
actual auto-application requires owner promotion per the ladder, which has
not occurred. The existing heal lane (select/apply/verify) is now WIRED
into the daily chain (audit gap #7): live proof this session — a real open
incident got a playbook selected, a shadow proposal, and an honest verify
FAIL, with v2 records from all three heal nodes. Rotation runs daily
(live: 11,210 duplicate rows deduped). Synthetic end-to-end drills ride
ticket 19.
