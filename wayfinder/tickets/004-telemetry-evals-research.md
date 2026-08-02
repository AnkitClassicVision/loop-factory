# 004 — Telemetry, evals, and export research

Status: CLOSED · Type: research (AFK) · Resolved: 2026-08-02 by claude_code explore sub-agent (full report + addendum in sub-agent session cf508aa5a0a2400190dd4c64aae6930a)

## Question

Best-practice per-step telemetry schema, eval/QA capture pattern, local-first storage, and the export contract a visual workspace (Gemini Enterprise / ex-Agentspace) needs.

## Resolution

**Repo ground truth:** runs.jsonl today = `epoch, node, payload_summary, shadow, timestamp` — zero token/cost/latency/model fields anywhere. Every model call already flows through one choke point: `call_model` in `kernel/gateways/model.py:21`. That is where telemetry attaches.

1. **Adopt OTel GenAI semantic conventions** (now in their own repo, stability = Development → pin a `schema_version` per record): `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` / `gen_ai.response.model`, `gen_ai.usage.input_tokens` / `output_tokens`, `gen_ai.response.finish_reasons`, tool attrs, `gen_ai.agent.id/name`, `gen_ai.conversation.id`, `error.type`. Metrics: `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`.
2. **Cost and auth route are NOT standardized** — custom fields: `loopfactory.cost_usd` (tokens × local price table) and `loopfactory.auth.route` (oauth_cli | service_oauth | local_model | vault_api_key | blocked).
3. **Scores are separate records, never fields mutated into a step** (Langfuse pattern: many scores per step, async judges, signed receipts immutable, producer authority differs). Align names to the verified `gen_ai.evaluation.*` attrs (name, score.value, score.label, explanation) + `source (script|judge|human)`, `judge_model`, `config_version`, explicit `target_ref`.
4. **Storage: JSONL receipts as signed source of truth + derived, rebuildable SQLite rollup** (stdlib, zero deps) for board queries; export NDJSON/CSV. OTLP collector rejected (separate Go binary, poor fit for deny-by-default receipts).
5. **Gemini Enterprise (Agentspace's successor):** ingests via connectors — BigQuery and Cloud Storage verified first-party; structured data supports custom schemas. No native BI board builder, but **A2UI** (Preview, spec v0.8) lets a registered A2A agent render dashboards inside the surface. Plan: own board off the SQLite rollup now; feed BigQuery/GCS connector when the tenant exists; A2UI later.
6. **Lean mechanisms to borrow:** heijunka box → slot-leveled scheduling against real quota capacity; andon → receipt/QA failure auto-creates incident + red station + time-to-clear metrics; WIP limits → hard caps on concurrent runs/steps per department; takt → cadence-deviation alerts; visible slack on the board.

**Entities for the export contract** (feeds ticket 011): department, factory@version, run, step, receipt, score, approval, incident — field lists in the sub-agent report.
