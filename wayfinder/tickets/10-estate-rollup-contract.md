---
title: Decide the estate reporting rollup contract
status: closed
type: grilling
assignee: coordinator-fable
blocked_by: [07]
---

## Question

What does every department report UP in v2 — the heartbeat/brief schema the
estate aggregates for humans: active runs + current step, blockers, approval
inbox items, failure/throughput counts, token/cost rollups, drift flags,
autonomy state — at what cadence, stored where, and consumable by any surface
(interim board now, Agentspace/Linear later per ticket 06 constraints)?
This is the andon-board data feed: the missing "visual control" slab from
atlas:automation-loop-estate. Decide with Ankit on top of ticket 07's record
contract.

Asset: coordinator-drafted proposal ready for reaction at
`wayfinder/drafts/10-rollup-proposal.md` (5-kind NDJSON feed + 4 forks with
recommendations).

## Resolution

Ankit accepted the full proposal (2026-08-02): estate aggregator emits
`estate/state/board-feed.ndjson` — id-keyed flat append-only lines, five kinds
(dept_status, active_run, andon, approval, metrics), regenerated every estate
cycle + on demand, provably rebuildable from department records alone. Forks:
(1) PULL model — estate reads department state read-only, departments write no
new code; (2) andon reuses existing manager finding codes + severity, kill/
breaker states surface top-band; (3) board shows per-department daily metric
totals, drill-down stays in runs-v2.jsonl; (4) approval inbox renders inline
with Linear card links. Feed detail: `wayfinder/drafts/10-rollup-proposal.md`
(ACCEPTED). Unblocks ticket 11 (board prototype).
