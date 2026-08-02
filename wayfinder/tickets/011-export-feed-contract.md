# 011 — Export feed + contract test (visual-workspace readiness)

Status: OPEN · Type: grilling → task · Claimed: — · Blocked by: [007 — Lock the telemetry + score schema]

## Question

Define the export feed (entities: department, factory@version, run, step, receipt, score, approval, incident) and the **contract test** that proves "Agentspace-ready" — the destination's definition of plug-in readiness.

## Shape (from ticket 004)

- SQLite rollup → NDJSON export per entity; schemas versioned; sanitized (no secrets/PHI/raw bodies — receipts are already sanitized by rule).
- Contract test = deterministic check that a fresh export renders every board view: department control room, active runs + current step, blockers, approval inbox ordered by SLA, throughput/failure aggregates, cost per loop. Rebuild-from-JSONL must reproduce the rollup byte-stably.
- Target connector when the tenant exists: Gemini Enterprise BigQuery or Cloud Storage connector (both verified first-party); A2UI agent rendering is a later effort.

## Resolution

(pending)
