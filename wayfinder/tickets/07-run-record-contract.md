---
title: Decide the v2 run-record / telemetry contract
status: closed
type: grilling
assignee: coordinator-fable
blocked_by: [04]
---

## Question

What is the versioned, per-node-run record every department emits in v2 —
fields (run_id, factory/release version, trigger, step, engine + model, auth
class from {oauth_cli, service_oauth, local_model, blocked}, tokens in/out,
attempts/retries, evaluator results, approval status, artifacts, errors,
disposition), storage shape (JSONL per department + estate rollup vs SQLite),
schema versioning, and what is deliberately excluded (secrets, PHI, raw
bodies)? Must answer "what is happening with this particular job right now"
from records alone, and stay rebuildable from the receipt log.

Resolve with Ankit against ticket 04's inventory. This contract is the spine
of tickets 09, 10, 14, 15.

Asset: a coordinator-drafted proposal is ready for reaction at
`wayfinder/drafts/07-run-record-proposal.md` (18 fields with per-field source
status + 5 numbered forks). The session starts from that draft, not from
scratch.

## Resolution

Ankit accepted all five recommendations verbatim (2026-08-02, this session):

1. **Storage = Option A**: append-only `state/runs-v2.jsonl` per department;
   the estate rollup re-emits id-keyed flat NDJSON (ticket 06 compatible).
   If ticket 12 adopts graph_agent, its run-card tables become a CONSUMER of
   these records, not a replacement.
2. **Cost on flat-fee lanes = quota proxy** (`model_calls` + tokens), never a
   dollar figure; a metered lane appearing in a record is itself a violation
   flag.
3. **`run_id` is stamped on Linear review cards** (extend
   `create_review_card.py`'s existing `--run-id` beyond the hash).
4. **Retention parked to fog** with a 90-day default until volume data exists.
5. **Podcast scope = in-repo steps only**; the external drafting pipeline
   emitting run-records (extending `dag-projection-v1`) goes to fog.

The locked contract detail (18 fields + sources) lives in
`wayfinder/drafts/07-run-record-proposal.md`, header updated to ACCEPTED.
Unblocks tickets 09, 10, 14 (and 15 once 08 + 14 close).
