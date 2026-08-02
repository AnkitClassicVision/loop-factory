# 007 — Lock the telemetry + score schema

Status: CLOSED · Type: grilling (HITL) · Resolved: 2026-08-02 by Ankit

## Question

Sign off the per-step telemetry record and score record schemas (field names, where they attach, price-table ownership) so every later ticket writes the same shape.

## Proposal (from ticket 004 research)

- **Step telemetry** attaches at the existing choke points: `call_model` in `kernel/gateways/model.py` + the fenced run recorder; fields use OTel GenAI names (`gen_ai.provider.name`, `gen_ai.request/response.model`, `gen_ai.usage.input_tokens/output_tokens`, `gen_ai.operation.name`, `error.type`, duration) + custom `loopfactory.cost_usd`, `loopfactory.auth.route`, `schema_version`.
- **Budget actuals**: callers commit actuals via `BudgetBroker.commit` so the manager's budget sensing goes live.
- **Scores** are separate append-only records aligned to `gen_ai.evaluation.*` with `source (script|judge|human)`, `judge_model`, `config_version`, `target_ref` — never mutated into signed receipts.
- **Storage**: JSONL = signed source of truth; SQLite rollup derived + rebuildable; NDJSON export.

## Open sub-questions

1. Who owns the model price table (repo file vs kernel constant) and its update cadence?
2. Do step receipts get HMAC-signed in the same change (audit found them forgeable), or is that ticket 008's scope?

## Resolution

**Signed off as proposed** (Ankit, 2026-08-02): OTel GenAI attribute names + `loopfactory.cost_usd` / `loopfactory.auth.route` / `schema_version` customs; telemetry attaches at `call_model` + fenced run recorder; budget actuals committed via `BudgetBroker.commit`; scores as separate append-only `gen_ai.evaluation.*`-aligned records; JSONL source of truth + derived SQLite rollup + NDJSON export. Sub-question dispositions: (1) model price table = versioned repo file, update cadence decided at implementation review; (2) step-receipt HMAC signing folded into ticket 008's runner scope (a transition token must be trustworthy). Unblocks [011 — Export feed + contract test](011-export-feed-contract.md).
