# 008 — Lock control-graph execution semantics

Status: CLOSED · Type: grilling (HITL) · Resolved: 2026-08-02 by Ankit

## Question

Adopt the executable control-graph design — typed edges + deterministic factory-owned runner + receipts as transition tokens — and decide the migration posture: incremental (runner drives existing node scripts) vs replacement (rewrite department runtimes).

## Proposal (from tickets 001/002/003)

- Extend `subgraphs.json`: nodes declare impl, runtime_mode, io contracts, receipt schema, action class, failure/state policy; explicit edges declare deterministic predicates over the predecessor receipt + normal/refusal/escalation/terminal targets.
- New runner in `factory/`: loads only the release-pinned graph, executes one node, validates its receipt, persists run state (a real run-level state machine replacing sequential bash), evaluates edge predicates, advances **only on proof**. Effects stay behind the kernel dispatcher; deny-by-default unchanged.
- Step receipts become HMAC-signed (closing the forgeable-JSON gap) so a transition token is trustworthy.
- **Recommended posture: incremental** — runner wraps existing node scripts as impls; podcast/social shell drivers retire node-by-node. Consistent with "consolidate the control plane, don't build a second graph system."

## Open sub-questions

1. Run-state store: extend `state/runs.jsonl` semantics or a new `run_state.json` per run?
2. Trigger modes in v1: time-based only, or time + event (goal-based later)?
3. Duplicate-trigger idempotency: orchestrator-level run lock keyed on (loop_id, trigger fingerprint)?

## Resolution

**A — incremental** (Ankit, 2026-08-02): the runner drives existing node scripts as impls; departments migrate node by node. **With an explicit escape hatch reserved by Ankit: the right to rebuild the process from scratch if incremental proves wrong.** Concretely: the typed-graph schema, receipts-as-transition-tokens semantics, and telemetry contract are designed runner-agnostic, so a later from-scratch runner rewrite would keep every graph, receipt, and record — the rewrite reservation costs nothing now.

Amendment (resolved 2026-08-02 by explore-parallel-lanes): PR #8's dag-supervisor is a podcast-specific projection AUDITOR (validates schema, canonical hash, freshness, skips — departments/podcast/runtime/dag_supervisor.py), NOT a runner — no cycle detection, no readiness/topological transitions, no dispatch, no receipt consumption. Ticket 008 implementation therefore: **build the v2 runner beside it in factory/ + kernel/**, reusing its canonical hashing, freshness logic, record locking, and alarm routing; keep the supervisor as an independently scheduled verifier. **Added acceptance criterion: the runner must export a versioned, SIGNED execution projection that the independent supervisor can verify without sharing execution authority** (execution authority and audit never merge into one component).
