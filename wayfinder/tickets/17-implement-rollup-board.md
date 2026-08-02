---
title: Implement estate rollup + interim board
status: open
type: task
assignee:
blocked_by: [10, 11, 15]
---

## Question

Implement the ticket-10 rollup: departments emit the reporting contract,
estate aggregates it, and the ticket-11 board design renders from LIVE
rollup data (podcast real, social best-effort from existing records).
Executed proof: board regenerates from records alone (rebuild-from-receipts
test), estate cycle green, a synthetic blocker visibly surfaces as an andon
signal. Ringer-routed for the build; board is factory-layer code (no
department-specific logic in factory/).

Build contract: `wayfinder/drafts/17-board-template-spec.md` (Board Template
v1 — the standard zone grammar for any loop, locked by Ankit 2026-08-02).
Visual reference: `wayfinder/prototypes/11-andon-board.html` (v4, verified).
