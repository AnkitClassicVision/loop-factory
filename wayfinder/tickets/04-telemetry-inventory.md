---
title: Inventory current run records + token-source options
status: closed
type: research
assignee: wf-04-telemetry-inv
blocked_by: []
---

## Question

What do Loop Factory run records/receipts capture TODAY per node run (podcast
+ social departments: records under departments/*/state and runs, manager
heartbeats, ringer manifests/receipts), and — for each headless engine lane
(Claude subscription, Codex OAuth, GLM plan, Ringer workers) — where can
token/cost/usage counts actually be read from? Deliver: (a) current-fields
inventory with file pointers, (b) per-engine token-source options with
confidence, (c) the gap list vs the desired v2 record (tokens, cost, model,
auth class, attempts, evals, approval, disposition). This grounds ticket 07.

Boundary: read-only; no PHI/secrets/message bodies in the findings — field
names and shapes only.

## Resolution

Full findings: `wayfinder/research/04-telemetry-inventory.md`.

Today's records are three disconnected layers, none of which carry engine, model,
tokens, or cost: (1) kernel receipts (`kernel/receipts.py`, `gateways/model.py`,
`gateways/budget.py`) are capability tokens and a model-call *count* ledger, not
usage records — the budget ledger only ever reserves `kind:"model_calls"`, never
actual dollars/minutes; (2) manager-cycle telemetry (`factory/manager.py`,
`STATE.json`, `heartbeats.jsonl`) is deliberately model-free by design; (3) social's
per-node receipts carry a bare `engine` string (e.g. `"claude_subscription"`) on
draft/QA artifacts only, no model name, no tokens. Podcast's in-repo runtime has no
model-calling nodes at all (drafting lives in an external pipeline this department
only supervises).

For token sourcing: loop-factory's own `draft_post.py` requests
`--output-format text` and never parses usage. Ringer (`/home/ankit114/repos/ringer/
ringer.py`) has a real `tokens` field end-to-end, but it's populated by a regex
scrape (`parse_token_count`/`DEFAULT_TOKEN_REGEX`) that is a verified no-match for
the shipped Claude and Gemini engine configs, and only "probable" (unverified this
session) for Codex. The richest verified source found is the Claude Code CLI's own
session-transcript JSONL (`~/.claude/projects/.../*.jsonl`), whose `message.usage`
object carries full input/output/cache token breakdown plus model/session id — but
both loop-factory's and Ringer's Claude wrappers currently suppress or don't request
session persistence for headless calls. No native GLM-coding-plan engine wiring was
found anywhere (only an OpenRouter-metered GLM route, a different, disallowed
billing lane). Switching the Claude engine from `--output-format text` to `json` and
parsing the trailing result object is the lowest-cost path to real token capture.

Gap list (12 desired v2 fields): 2 have a ready source today (release version via
`releases/<hash>/manifest.json`; step/node), 8 are partial with a named path forward
(engine+model, tokens, attempts/retries, evaluator results, approval status,
artifacts, errors, run_id), and 2 have no source at all (trigger, auth class; cost
has no source for flat-fee subscription lanes specifically). This grounds ticket 07.
