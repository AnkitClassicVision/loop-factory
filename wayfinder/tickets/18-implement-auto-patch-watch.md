---
title: Implement the gated auto-patch watch loop
status: open
type: task
assignee:
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
