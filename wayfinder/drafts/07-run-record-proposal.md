# Ticket 07 draft — v2 run-record ("receipt") proposal

CANARY: blue paperclip

Status: **ACCEPTED — this is the locked v2 run-record contract.** Ankit
accepted all five recommendations 2026-08-02 (see ticket 07 Resolution).
Grounded field-by-field in the ticket-04 inventory
(`wayfinder/research/04-telemetry-inventory.md`). Changes to this contract now
follow process-change QA, not casual edits.

## What this is (plain English)

One JSON line gets appended every time any node of any department runs — the
receipt. Same shape everywhere. The board, the metrics, the evals, and the
"what is happening with this job right now" question all read from these
lines and nothing else. No PHI, no secrets, no message bodies — pointers and
hashes instead of content, same rule the receipts dirs follow today.

## The proposed record (one JSON object per node run)

| # | Field | What it holds | Source today (per ticket 04) |
|---|---|---|---|
| 1 | `schema` | literal `"run-record/v2"` + integer `rev` | new — versioning is the point |
| 2 | `run_id` | one ULID minted at trigger time, carried through every step of that run | NEW — today it only exists inside a one-way hash |
| 3 | `department` / `node` / `epoch` / `ts` | which dept, which step, manager epoch, ISO time | exists (`runs.jsonl` fields) |
| 4 | `attempt` + `round` | `attempt` = retry of the same step; `round` = cross-model edit-loop pass | both exist, in two different places (Ringer attempts vs charter `max_edit_rounds` round) — recorded as two fields, not merged |
| 5 | `release` | `{hash, source_ref}` of the pinned release that ran | ready — `releases/<hash>/manifest.json`, just needs threading in |
| 6 | `trigger` | `{kind: time\|event\|goal\|manual\|escalation, id, dedupe_key}` | NEW — nothing distinguishes cron tick from manual run today; kinds per the Bee taxonomy |
| 7 | `engine` + `model` | engine lane key (e.g. `claude_subscription`) AND the resolved model id (e.g. `claude-sonnet-5`) as separate fields | engine exists as a bare string on N4/N5 artifacts; model is NEW (comes free once engines emit JSON) |
| 8 | `auth_class` | `oauth_cli \| service_oauth \| local_model \| blocked` | NEW — wrappers already verify OAuth internally, they just don't record it |
| 9 | `usage` | `{input_tokens, output_tokens, cache_read, cache_creation}` | NEW capture, named path: switch Claude engine `--output-format text` → `json` and parse the trailing result object (ticket 04's lowest-cost path); Codex likely similar; a step with no model call writes `null` |
| 10 | `cost` | `{lane: flat_subscription \| metered_forbidden, model_calls}` — for flat-fee lanes "cost" means quota consumption (calls + tokens), never dollars; a metered lane appearing here is itself a violation flag | budget ledger counts model_calls today; dollars don't exist for subscription lanes by design |
| 11 | `duration_ms` | wall time of the step | new, trivial |
| 12 | `status` | ONE enum: `ok \| blocked \| error \| halted \| killed \| escalated \| skipped` | today scattered across ≥4 shapes (draft blocked/missing, kill switch, breaker, manager error count) — this unifies them |
| 13 | `errors[]` | `{code, detail}` list, empty when ok | partial (draft_post reasons, manager counts) |
| 14 | `artifacts[]` | `{path, sha256}` of every file this run produced | pattern exists at release granularity; per-run is new |
| 15 | `receipts[]` | kernel token nonces/refs used by this step | exists in kernel layer, not threaded onto run records |
| 16 | `evaluator` | `{pass, defects:[{code,detail}], engine, model}` | exists for social (`N5-qa` shape) — this adopts that shape estate-wide; ticket 09 owns what goes IN it |
| 17 | `approval` | `{required, status, card_ref}` — status uses the existing approval_queue enum | two disconnected representations today (queue + Linear ledger); this unifies via `card_ref` |
| 18 | `external_actions_taken` | integer, MUST be 0 in shadow | the shadow proof, first-class on every record |

## Storage — the main fork (question 1)

- **Option A (recommended): append-only JSONL.** Each department writes
  `state/runs-v2.jsonl`; the estate rollup (ticket 10) re-emits id-keyed flat
  NDJSON — exactly the shape Gemini Enterprise, Linear, or a static HTML board
  can ingest (ticket 06 constraints). Zero new dependencies; the record SHAPE
  is the contract, so storage can migrate later without breaking anything.
- **Option B: SQLite store now.** Stronger queries and unique constraints
  (dedupe enforcement in the store), aligns with graph_agent if ticket 12 says
  adopt — but couples 07 to 12 and adds a write-contention concern the JSONL
  single-writer pattern already avoids.

Recommendation: **A now**, revisit at ticket 12's decision. If 12 adopts
graph_agent, its run-card tables become a CONSUMER of these JSONL records,
not a replacement.

## Questions for the ticket-07 session (the only real forks)

1. **Storage**: Option A (JSONL now) or B (SQLite now)? Recommend A.
2. **Cost meaning**: for flat-fee lanes, accept quota-proxy (`model_calls` +
   tokens) as "cost", no dollar figure? (No dollar source exists — ticket 04.)
   Recommend yes.
3. **run_id linkage**: should `run_id` also be stamped on Linear review cards
   (`create_review_card.py` already accepts `--run-id`, currently hash-only)
   so board ↔ card cross-navigation works? Recommend yes — one-line change.
4. **Retention**: runs-v2.jsonl rotation policy (size/age) — pick a number, or
   park to fog until real volume data exists? Recommend park with a 90-day
   default.
5. **Podcast's external pipeline** (scope call that shapes ticket 15): podcast's
   in-repo nodes make NO model calls — drafting lives in the external pipeline
   it supervises via the DAG receipt. Does v2 require that external pipeline to
   emit run-records too (extend the `dag-projection-v1` receipt), or does
   podcast v2 record only its in-repo supervision/sensor steps for now?
   Recommend: in-repo steps now + a fog item for the external pipeline, so
   ticket 15 stays bounded.

## What is deliberately NOT in the record

Prompt text, draft bodies, transcripts, PHI, secrets, OAuth tokens, raw CRM
IDs — pointers/hashes only (existing S3-sanitized + receipts discipline).
