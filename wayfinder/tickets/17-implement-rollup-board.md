---
title: Implement estate rollup + interim board
status: closed
type: task
assignee: coordinator-fable
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

## Resolution

Complete (2026-08-02). factory/boardfeed.py (pull-only deterministic
aggregator, locked contract, unknown-never-0, violations become andons,
malformed counted) + factory/board.py (Board Template v1 renderer,
department-agnostic, metered lanes render as alerts, fallback lists for
unknown groups). Executed proof: rebuild-from-records (deterministic
byte-identical test + live feed 86 lines / 2 departments / 0 malformed);
boards regenerate automatically at the end of podcast_daily.sh; a real
andon (pace_under) surfaced live from manager findings. Estate cycle green
via the daily-chain run. Coordinator fixes: script-node runs count in
rollups without lane rows; usage:None sums as measured zero.
