# Telemetry inventory: current run records + token-source options

Read-only research for ticket `wayfinder/tickets/04-telemetry-inventory.md`. No PHI, secrets, or
record content bodies below — field names and shapes only, verified by direct inspection unless
marked otherwise.

## (a) What run records/receipts capture TODAY, by layer

### 1. Kernel gateway receipts — `kernel/receipts.py`, `kernel/gateways/*`
Verified by reading `kernel/receipts.py` and `kernel/gateways/model.py`, `kernel/gateways/budget.py`.

- Permission-token shape (`issue_receipt`/`verify_receipt`): `{action_class, binding_hash, exp,
  nonce}`, base64url-encoded and HMAC/KMS-signed, `payload.signature` dot-joined. This is a
  **capability token minted before an effect**, not a usage record — it proves the caller sanitized
  its input and hasn't replayed a nonce. No engine, model, token, or cost field exists at this layer.
- `kernel/gateways/model.py::call_model()` only computes `model_binding = {prompt_hash (sha256),
  sanitized: bool}`, verifies the receipt, then calls an injected `runner(prompt) -> str`. It never
  sees or records which engine/model executed, nor any usage the runner's subprocess produced.
- `kernel/gateways/budget.py::BudgetBroker` ledger — verified live at
  `departments/social/state/kernel/budget.jsonl` (78 rows). Row keys observed across the whole file:
  `{amount, kind, now, rid}`. Only `kind == "model_calls"` ever appears; no row carries an `"event"`
  key (i.e. every row is an implicit `reserve`, never `commit` or `release`). The `dollars` and
  `worker_minutes` ceilings declared in `DEFAULT_CEILINGS`/charter `budget_ceilings` are configured
  but **never exercised** — the ledger only counts model-call reservations, never actual tokens or
  dollars.

### 2. Department manager cycle telemetry — `factory/manager.py`
Verified by reading `manager.py:122-198` (`sense()`) and live `departments/podcast/state/` files.

- `sense()` reads `approval_queue.jsonl` (`row.status` in {`pending_approval`, `sent`,
  `sent_shadow`, `held_recipient_mismatch`, `rejected`}, `row.queued_at`), `runs.jsonl`
  (`row.status`/`row.queued_at`/`row.timestamp`, only used to detect `"error"` /
  `"halted_incomplete_context"`), an outcomes file (`row.held`/`row.meeting_id`), and a budget file.
- Output snapshot: `{now, week_start, week_touches, pending, held_mismatch, rejected,
  carried_forward, last_run_at, last_run_ok, run_errors, conversions, budget_used,
  budget_unreadable}`.
- Persisted `STATE.json` (verified live, `departments/podcast/state/STATE.json`): `{department,
  epoch, last_cycle_at, autonomy_state, sensed{...above...}, open_findings:[{code, severity, detail,
  observed, setpoint}], escalations, escalations_undelivered}`. `sensed.budget_used` was `{}` empty.
- `heartbeats.jsonl` (verified live, podcast): `{ts, epoch, ok, findings, escalations,
  escalations_undelivered}`.
- `runs.jsonl` **at the manager level** (podcast, 1493 rows scanned) is the manager's own
  Sense/Compare/Record cycle log, not a per-worker-node execution record: keys seen across all rows
  were `{epoch, node, payload_summary{failed, observations, unknown}, shadow, timestamp,
  escalations, findings}` — `node` here is the manager's own tick, not "which node ran."
- This whole layer is deliberately model-free by design (manager.py module docstring: "Model-free:
  no network, no model calls in Sense/Compare/Record") — it will never carry engine/token/cost data
  unless something else writes it into `payload_summary` or a new field.

### 3. Per-node run records — `departments/social/runtime/record.py`
Verified by reading `record.py` in full (only department that uses this shared writer for its own
runtime nodes; podcast's nodes use a different, sensor-style pattern, see §5).

- `write_record()` appends to `<state>/runs.jsonl`: `{node, epoch, timestamp, shadow,
  payload_summary}`. `payload_summary` is caller-supplied free-form — nothing today puts engine,
  model, tokens, or cost into it.
- `STATE.json` gets `{department, epoch, last_node, last_run_at, last_payload_summary, shadow}`
  merged in.
- `heartbeats.jsonl` gets `{ts, epoch, node}`.
- No `run_id`, `engine`, `model`, `attempts`, `tokens`, `cost`, or `approval`/`disposition` field
  exists at this layer.

### 4. Social department's N/S node artifact receipts
Verified live from one receipt directory,
`departments/social/state/receipts/20260802T130000Z-2181219/` (17 files, key/type shapes only, no
values):

- `N1-index-installed.json`: `{index, row_count, status}`
- `N1-inventory(-source).json` / `S1-index.json`: `{items:[{body_path, item_id,
  last_resurfaced_at, prior_engagement{score}, published_at, source_type, thumbnail_url, title,
  url}], status}`
- `N2-candidate.json`: `{item{...}, rank_score, rationale}`
- `N3-brand-offer.json`: `{brand{audience, name, voice_notes}, offer{cta_url, description, name},
  status}`
- `N3-context.json` / `S3-sanitized.json`: `{assembled_at, body_text, brand{}, complete, item{},
  missing:[], offer{}, thumbnail_url, version}` (+`redactions`, `sanitized` on S3)
- **`N4-draft-r{N}(-raw).json`: `{body, cta_url, engine, round, sources:[{claim, source}], surface,
  thumbnail_url}`** — the only node artifact that names an engine at all, and it's a bare string
  (e.g. `"claude_subscription"`/`"codex_oauth"`, the engines.example.yaml key). No model name, no
  token count, no cost.
- **`N5-qa-r{N}.json`: `{defects:[{code, detail}], engine, pass}`** — the closest thing to
  "evaluator results" that exists today; still a bare-string `engine`, no model/tokens.
- `S1-resolved.json`: `{item{}, status, surface}`
- `S2-eligible.json`: `{cta_url, item{}, status}`
- `S6-kill.json`: `{status, tripped:[], ts}` (kill-switch state)
- `S7-breaker.json`: `{delivery_failure_streak, status, surface, ts}` (circuit breaker state)
- `S8-model-token.json`: `{nonce, receipt}` — this is the **kernel authorization token** from §1,
  not a usage record, despite the filename.

### 5. Podcast's dag_supervisor observation record
Verified by reading `departments/podcast/runtime/dag_supervisor.py` in full.

- `observations.jsonl` row: `{ts, sensor:"dag_supervisor", subject, status, evidence, detail,
  metrics}`. `incident_candidates.json` row: `{ts, sensor, subject, failure_class, severity,
  setpoint, observed, evidence:[...], one_question}`.
- This is purely a DAG-projection integrity checker (hash/staleness/skip-artifact validation) — no
  engine/token/cost concept exists in podcast's own runtime nodes. Confirmed:
  `departments/podcast/runtime/kernel_bridge.py` (2685 bytes) has **no** `request_model` /
  `request_send` functions at all — nothing in this department's in-repo runtime layer calls a
  model gateway. The actual drafting/editing model calls for podcast episodes happen in an external
  pipeline that this department only supervises via a signed DAG projection receipt (schema
  `"dag-projection-v1"`: `dag_hash`, `generated_at`, `steps`, `episodes[].audit`/`skip_artifacts`) —
  out of this repo's scope.

### 6. Release pin — `departments/*/releases/<hash>/manifest.json`
Verified live, `departments/social/releases/ac07729f5044185d/manifest.json`: `{hash, source_ref,
artifacts:[{path, sha256}]}`. `hash` is the release-pin id (the releases/ dirname); `source_ref` is
the git SHA it was cut from. This is a ready-made source for "factory/release version" in the
desired v2 record, but **nothing today stamps it onto a run record automatically** — a caller would
have to read the release manifest and thread it in.

### 7. Human-approval / disposition
Verified by reading `departments/social/runtime/create_review_card.py` in full.

- Ledger row appended to a caller-supplied `--ledger` path: `{ts, row_hash, department,
  kind:"human_review", card_identifier, status:"open"}`. Receipt written to `--out`:
  `{status:"card_created", identifier, url, row_hash, ts}`.
- `--run-id` is accepted as a CLI arg but is **only folded into `row_hash`**
  (`sha256("social-{item_id}-{run_id}")`) — it is not stored as its own readable field anywhere.
  There is no first-class `run_id` field in any record layer inventoried above; every layer
  identifies a "run" indirectly (a `(node, epoch, timestamp)` tuple, a hash, or a kernel nonce),
  never a single carried `run_id` string.
- `approval_queue.jsonl`'s status enum (§2) is the closest thing to a first-class
  approval/disposition value that exists today, and it's decoupled by design from the N/S node
  receipts in §4.

## (b) Per-engine token-source options, with confidence

Loop-factory's own engine declaration file, `departments/social/runtime/engines.example.yaml`
(verified), lists exactly two engines: `codex_oauth` (`codex exec --sandbox read-only "{prompt}"`)
and `claude_subscription` (`claude -p --disable-slash-commands
--exclude-dynamic-system-prompt-sections --output-format text --no-session-persistence
"{prompt}"`). `draft_post.py::_run_argv()` captures only `completed.stdout.strip()` — no usage/cost
parsing exists anywhere in `draft_post.py`/`qa_post.py`, and `--output-format text` is requested, so
even if the CLI emitted structured usage data it would be inside an opaque text blob being parsed
only for the draft JSON payload.

Ringer's engine config was checked at `/home/ankit114/repos/ringer/ringer.py` (the actual tool;
`/mnt/d_drive/repos/ringer*` paths in this environment are project workdirs/manifests, not the tool
source) plus `config.sample.toml` and the `engines/*.sh` OAuth wrappers.

| Engine lane | Source | Confidence | Notes |
|---|---|---|---|
| Claude subscription via loop-factory's own `draft_post.py` | none in-repo | verified-absent | `--output-format text`, stdout captured only for the draft payload. |
| Claude subscription via Ringer `[engines.claude]` | `parse_token_count()` regex scrape (`DEFAULT_TOKEN_REGEX = r"tokens\s+used\s*:?\s*([0-9][0-9,]*)"`) | verified-present-but-non-matching | `config.sample.toml` sets `token_regex = ""` for this engine, which falls back to the default pattern (`parse_token_count`'s `if token_regex:` truthy check treats `""` as falsy). Claude Code's plain-text `-p` output does not print a "tokens used: N" line, so this returns `None` in practice — the same "harmless no-match" pattern the config comments call out for the Grok engine. |
| Claude Code session transcript (`~/.claude/projects/<project>/<session-id>.jsonl`) | native CLI session log | **verified** (structure inspected directly, this session's own transcript, keys only) | Every `type:"assistant"` row's `message.usage` object contains `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, `service_tier`, `cache_creation{ephemeral_1h_input_tokens, ephemeral_5m_input_tokens}`, plus `message.model`, `session_id`, `requestId`, `timestamp`, `cwd`, `gitBranch`, `effort`. Richest available source in this environment — but it depends on session persistence being on, and both loop-factory's own engines.example.yaml (`--no-session-persistence`) and Ringer's `claude-oauth.sh` path don't request it for headless drafting calls. |
| Claude Code `--output-format json` | CLI's own trailing result-JSON envelope | probable, not verified in this repo | Documented Claude Code CLI behavior: `-p --output-format json` returns a final JSON object on stdout with a usage/cost summary, independent of session-transcript writing. Neither engines.example.yaml nor Ringer's config.sample.toml currently requests this format for the claude engine (both request `text`) — this would be a config + parser change, not new capability to build. |
| Codex OAuth via `draft_post.py` | none in-repo | verified-absent | Same text-capture path as Claude. |
| Codex OAuth via Ringer `[engines.codex]` | `parse_token_count()`, non-blank `token_regex` (explicit in both `built_in_codex_engine()` and config.sample.toml) | probable | A non-blank regex being set (vs. left blank for claude/gemini) implies some past evidence that Codex's `exec` output prints a "tokens used: N"-shaped line, but this was not independently re-verified against current Codex CLI output in this task. |
| GLM coding plan | no wiring found | unknown / no source | Neither engines.example.yaml nor Ringer's config.sample.toml declares a native GLM subscription-plan CLI engine. The only GLM reference found is `[engines.pi-openrouter]` (`model_default = "openrouter/z-ai/glm-5.2"`), which routes through OpenRouter's **metered API** — a different billing lane than the subscription "GLM coding plan" this ticket asks about, and one this repo's CLAUDE.md treats as forbidden for production ("per-token API lanes are forbidden — escalate instead of spending"). No native GLM-plan wrapper was found under `ringer/engines/` or loop-factory's own runtime dirs. |
| Ringer's own per-task counter | `TaskRuntime.tokens` / `WorkerResult.tokens` (verified fields, `ringer.py:979,996`), summed into a run-level `{"tokens": sum(...)}` (`ringer.py:1233`) and per-attempt `"worker_tokens"` (`ringer.py:7399`) | field verified; value verified-empty-in-practice for claude/gemini | The plumbing is real end-to-end, but the value is only ever non-`None` when `parse_token_count` finds a match, which the row above shows is a no-match case for the shipped claude/gemini configs. |
| OpenRouter-catalog cost math | `catalog_per_m`/`catalog_decimal`/`refresh_openrouter_catalog` in ringer.py | verified-present, not applicable here | Ringer maintains a live OpenRouter pricing catalog (per-million-token $ rates) that could combine with a token count to produce a dollar figure — but only for OpenRouter-routed engines, which are the metered/API lane this repo's rules steer away from. There is no dollar-cost concept anywhere for Claude subscription / Codex OAuth / GLM plan lanes, because those are flat-fee, not per-token-billed: a v2 "cost" field for these three lanes will need to mean plan-quota/budget-ceiling consumption, not a computed dollar amount. |

## (c) Gap list vs. the desired v2 record

Desired: `{run_id, factory/release version, trigger, step, engine+model, auth class, tokens in/out,
cost, attempts/retries, evaluator results, approval status, artifacts, errors, disposition}`

| Field | Exists today? | Ready source | Notes |
|---|---|---|---|
| `run_id` | No first-class field | Partial — folded into a one-way sha256 hash in `create_review_card.py`, never stored plainly | `runs.jsonl` identifies a run by `(node, epoch, timestamp)` instead; would need a literal field introduced into `write_record()`/`manager.py` and threaded through every node call |
| release version | Not stamped on any run record | **Yes** — `releases/<hash>/manifest.json{hash, source_ref}` already exists | Needs threading only: read the pinned release hash at cycle start, pass into `write_record()`'s payload or a new top-level field |
| `trigger` | Not present anywhere inventoried | No ready source found | Nothing distinguishes "cron tick" vs "manual invoke" vs "escalation retry" today |
| `step`/node | Yes | Verified — `node` field on every `runs.jsonl` row (manager-cycle and per-node writer both) | Already first-class |
| engine + model | Partial | `engine` bare string on N4-draft/N5-qa artifacts (e.g. `"claude_subscription"`) | No separate `model` field anywhere — engine name and model are conflated into one string; `_normalize_draft()` would need to also capture a resolved model identifier |
| auth class | Not present | No ready source in loop-factory | Ringer's wrapper scripts (`claude-oauth.sh`, `codex-oauth.sh`) verify OAuth/first-party auth internally (e.g. `auth_status` JSON check, claude-oauth.sh:66-91) but don't emit that verification result as a stored field anywhere |
| tokens in/out | Not present in loop-factory; partial in Ringer (one combined `tokens` int, not split in/out) | Ringer's `tokens` field when its regex matches (currently doesn't for claude/gemini); Claude Code's own session-transcript `usage.{input_tokens,output_tokens,...}` (richer, verified, but needs session persistence loop-factory/Ringer both currently suppress or don't request) | Best path found: switch the claude engine to `--output-format json` and parse the trailing result object instead of scraping text |
| cost | Not present for any subscription/OAuth lane | Only exists conceptually for OpenRouter-routed engines via Ringer's pricing catalog | For flat-fee lanes, "cost" likely needs to mean plan-quota consumption, not a dollar figure — no quota-read API was found in this task |
| attempts/retries | Partial | Ringer's `TaskRuntime.attempts` (verified, incremented per worker retry); loop-factory's own draft/QA nodes track a `round` number instead (capped by charter `max_edit_rounds`) | Two different existing counters (raw retry vs. cross-model edit-loop round) that would need reconciling, not built from scratch |
| evaluator results | Partial | `N5-qa-r{N}.json {defects:[{code,detail}], engine, pass}` (verified) is a real evaluator-result record already, for social | No equivalent found for podcast — its in-repo runtime has no drafting/QA nodes at all |
| approval status | Partial | `approval_queue.jsonl` status enum (verified, consumed by `manager.sense()`); separately, `create_review_card.py`'s ledger `status:"open"` for the Linear human-gate path (verified) | Two disconnected approval-status representations today, not unified |
| artifacts | Partial | Release `manifest.json` `artifacts[]{path,sha256}` exists at release-pin granularity (verified); per-run node output files exist on disk (N4-draft, N5-qa, etc.) | No per-run manifest of "what files this run produced" — only the fixed set of files a node happens to write |
| errors | Partial | `manager.py`'s `run_errors` counts rows with status in `{"halted_incomplete_context","error"}` (verified); `draft_post.py` writes `{status:"blocked"/"missing", reasons/reason}` on failure (verified) | Exists but scattered across at least two shapes — a count at manager layer, a structured reason at node layer |
| disposition | Not present as one field | Closest analogs: draft_post.py's blocked/missing status strings, `S6-kill.json{status,tripped}`, `S7-breaker.json{status,delivery_failure_streak}` | No single "final disposition" field rolls these up per run |

## Sources consulted

- `kernel/receipts.py`, `kernel/gateways/model.py`, `kernel/gateways/budget.py`
- `factory/manager.py` (lines 1-220)
- `departments/podcast/state/{STATE.json, heartbeats.jsonl, runs.jsonl}` (live, shapes/keys only)
- `departments/social/state/kernel/budget.jsonl` (live, 78 rows, keys/enum values only)
- `departments/social/state/receipts/20260802T130000Z-2181219/*.json` (live, 17 files, shapes only)
- `departments/social/runtime/{record.py, draft_post.py, engines.example.yaml, kernel_bridge.py,
  create_review_card.py}`
- `departments/podcast/runtime/{dag_supervisor.py, kernel_bridge.py}`
- `departments/social/releases/ac07729f5044185d/manifest.json` (live, shape only)
- `/home/ankit114/repos/ringer/ringer.py` (`EngineConfig`, `TaskRuntime`, `WorkerResult`,
  `parse_token_count`, `DEFAULT_TOKEN_REGEX`, `built_in_codex_engine`, catalog-pricing helpers)
- `/home/ankit114/repos/ringer/config.sample.toml`, `engines/{codex-oauth.sh, claude-oauth.sh}`
- `~/.claude/projects/-mnt-d-drive-repos-loop-factory/73b975bb-1839-46eb-b538-bad5d60489f9.jsonl`
  (this session's own transcript; keys/types only, no message content read)
- `~/.claude/`, `~/.codex/` top-level listings (presence checks only, via `ls`)
