# Ticket 10 draft — estate reporting rollup contract

CANARY: blue paperclip

Status: **ACCEPTED — locked contract** (Ankit, 2026-08-02, all forks as
recommended; see ticket 10 Resolution). Grounded in the locked ticket-07
record contract, ticket 06's ingestion constraints (id-keyed flat NDJSON so
Gemini Enterprise / Linear / static HTML can all consume it), and the existing
manager STATE/heartbeat shapes (ticket 04 §2). Changes now follow
process-change QA.

## What this is (plain English)

The one feed every surface reads. The estate aggregator reads each
department's own records and re-emits a single flat event stream —
`estate/state/board-feed.ndjson`. The interim board (ticket 11), and later
Gemini Enterprise or Linear, render from this feed and nothing else. If the
feed can't show it, humans can't see it — so the feed is the andon board's
single source of truth.

## Feed shape

Every line: `{id, kind, ts, department, data{...flat...}}` — string `id`
(dedupe key: `kind:department:subject:ts-bucket`), append-only, no nesting
beyond `data`, no PHI/secrets/bodies. Five kinds:

| kind | data carries | source (all existing or ticket-07 fields) |
|---|---|---|
| `dept_status` | autonomy_state, epoch, last_cycle_at, ok, open_findings count, escalations | STATE.json + heartbeats |
| `active_run` | run_id, node, status, attempt/round, engine+model, started_at | runs-v2.jsonl (ticket 07) |
| `andon` | severity, finding code, detail, observed vs setpoint; kill-switch + breaker states surface here as top-band signals | manager open_findings + S6-kill/S7-breaker |
| `approval` | card_ref, status, queued_at, age | approval_queue + Linear ledger (unified per 07) |
| `metrics` | per-department daily rollup: runs, ok/error counts, tokens in/out, model_calls, evaluator pass rate | aggregated from runs-v2.jsonl |

Cadence: regenerated every estate cycle + on demand; the rebuild-from-receipts
test (ticket 17) proves the feed derives from department records alone.

## v1.1 addendum — objectives + funnels (Ankit-directed, 2026-08-02, ticket-11 iteration)

The board is objective-driven, so the `metrics` kind carries two additional
row shapes:
- **objective rows**: `{objective_id, label, setpoint, minimum, target,
  observed, unit}` — sourced from charter setpoints (the manager already
  senses observed-vs-setpoint; this surfaces it). First instances: podcast
  publish reliability (target 100%) and hopper depth (min 2, target 6).
- **funnel_stage rows**: `{funnel_id, stage, order, count, period}` — for
  pipelines that feed an objective (first instance: guest funnel — outreach →
  first response → conversation → pre-call → interview booked →
  info+headshot → recorded).
Audience metrics (downloads, shares, promo counts) ride the existing metrics
shape. Unmeasured values emit as "unknown", never 0.

## v1.2 addendum — per-lane telemetry rows (Ankit-directed, 2026-08-02)

The board carries a Telemetry zone, so `metrics` also emits per-engine-lane
rows: `{lane, model, auth_class, calls, tokens_in, tokens_out, period}` —
aggregated from runs-v2 records. A row with `auth_class` outside
{oauth_cli, service_oauth, local_model} or a metered lane with calls > 0 is
rendered as an andon incident, not a stat (hard rule 9 made visible).

## Forks (4)

1. **Push vs pull**: estate PULLS by reading department state read-only
   (recommended — zero new department code, single writer, matches
   deny-by-default) vs departments PUSH packets upward. Recommend **pull**.
2. **Andon taxonomy**: reuse the manager's existing finding codes + severity
   untouched for v1 (recommended), vs designing a new taxonomy now.
   Recommend **reuse** — redesign is drift risk with no data yet.
3. **Metrics granularity on the board**: per-department daily totals only for
   v1; per-node/per-run drill-down stays in runs-v2.jsonl for when you click
   through. Recommend **yes** — keeps the feed small and the board legible.
4. **Approval inbox rendering**: inline list of open approvals on the board
   with links out to the Linear cards (recommended), vs link-only.
   Recommend **inline + links** — the board should answer "what needs a human
   right now" without a second hop.
