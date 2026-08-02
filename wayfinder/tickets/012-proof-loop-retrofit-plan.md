# 012 — Revenue proof-loop stand-up plan (births the implement fan-out)

Status: OPEN · Type: task (AFK, polly-driven) · Claimed: — · Blocked by: — (008 + 013 both resolved 2026-08-02; FRONTIER — next major work item after the two stabilization PRs merge)

## Question

Turn the locked decisions into the ordered implement-ticket set that makes the NEW Revenue loop fully operational on v2 machinery (005 resolved: new Revenue loop; 007 resolved: telemetry schema locked): v2 runner + typed graph, telemetry wiring at call_model/run recorder, score capture, F2/F3 authoring from the intent-locked interview, watch-loop + deadman live, export feed passing its contract test, synthetic-data + failure-injection test pass (forced auth expiry, duplicate trigger, killed worker, rejected approval, rebuild-run-view-from-receipts), shadow verification, release pin.

Each implement ticket = one coding sub-agent in its own worktree + different-vendor cross-review + its own PR; Ankit merges. This ticket resolves into that plan, not into code.

## Resolution — THE PLAN (claimed by polly 2026-08-02; owner granted continuous execution: "go on 12, don't stop till done")

### Wave 1 (parallel, dispatched 2026-08-02) — the v2 spine
- **W1 `graph-runner`** (claude_code, branch polly/graph-runner): typed subgraphs v2 schema + deterministic receipt-gated runner + run state machine + idempotent run locks + signed execution projection + factory_version hook. Cross-review: codex.
- **W2 `telemetry-spine`** (codex, branch polly/telemetry-spine): OTel-GenAI step telemetry at call_model + prices.json + BudgetBroker actuals + score records + SQLite rollup + NDJSON export + board contract test. Cross-review: claude_code.

### Wave 2 (after W1+W2 merge) — the Revenue department
- **W3 `revenue-department`**: F0 scaffold + F2 charter/concept-map/procedural-graph/subgraphs-v2 authored from the intent-locked interview artifact + F3 runtime nodes (read-HubSpot stub, classify-lead-type, draft-packet, deterministic evaluator, HIL outbox) — all shadow, synthetic fixtures only, zero live HubSpot calls in tests. Includes suppression-inventory discovery task design.
- **W4 `export-contract-hardening`**: entity export vs the visual-workspace contract; rebuild-from-JSONL determinism proof.

### Wave 3 — proof
- **W5 `revenue-shadow-proof`**: synthetic-data run + failure injection (forced auth expiry, duplicate trigger, killed worker, rejected approval, rebuild-run-view-from-receipts) + shadow verification + release pin. Definition-of-done per runbooks/factory-pipeline.md, all nine items, external_actions_taken=0.

### Standing notes
- Ticket 009 proposed-default recorded (factory_version tuple in release manifest) — owner ratifies at the Wave-3 gate.
- NEW external lane observed 2026-08-02: /mnt/d_drive/ringer-work/podcast-telemetry-v2-r1/observations-rotation (podcast telemetry rotation) — W2 scoped to kernel/factory level, departments/*/runtime untouched; ticket 014 rules apply.
- PR #10 merge + watchdog install: owner merges; polly runs the installer post-merge under owner's standing permission (this session, 2026-08-02).
