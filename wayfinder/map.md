<!-- label: wayfinder:map -->
# Wayfinder Map — Loop Factory v2: autonomous loops + visual factory

CANARY: blue paperclip

## Destination

Loop Factory v2 is specced AND proven on the podcast department: every node run
emits a versioned run record (tokens, cost, model + auth route, attempts,
evaluator results, approval status, disposition); every department rolls up to
one estate reporting contract that renders an interim heijunka/andon board;
watch loops support gated auto-patch healing (low-risk internal patches
auto-apply only after full QA + re-shadow, promotion-gated, receipts reviewed
by a human); the graph-engine adoption decision is locked. Podcast pilot
upgraded and shadow-verified with receipts. Won when ticket 19 closes.

Owner decisions that fixed this destination (Ankit, 2026-08-02, AskUserQuestion):
1. Destination = specs + pilot built (execution carried inside this map — see
   Notes override).
2. Pilot department = podcast.
3. Visual scope = reporting contract + interim generated board; AgentSpace is a
   later consumer of the same contract, not chosen here.
4. Autonomy = gated auto-patch within the hard rules (always-human floor,
   shadow-first, deny-by-default never relax).

## Notes

- **Tracker**: local-markdown fallback (no tracker configured). Tickets live in
  `wayfinder/tickets/NN-slug.md` with front-matter `status: open|closed`,
  `type: research|prototype|grilling|task`, `assignee:` (claim = assignee set),
  `blocked_by: [NN,...]`. Frontier = open + unassigned + all blockers closed.
  Frontier query: `grep -L "status: closed" wayfinder/tickets/*.md` then check
  front-matter. Research findings land in `wayfinder/research/<slug>.md`.
- **Execution override**: this map carries execution (tickets 15–19), per
  owner decision 1. Everything still lands shadow-first with receipts; no
  external effects; releases re-pinned via the normal pipeline.
- **ATLAS alignment**: `atlas:automation-loop-estate` (OB_mybcat, 2026-07-20).
  This map builds the missing "visual control / andon board" slab (nodes
  N004/N005/N010, edges E002/E006) plus the telemetry + eval layer. Update the
  ATLAS in OB as tickets close.
- **Hard rules**: `CLAUDE.md` hard rules 1–9 bind every ticket. Governance
  files stay human-only. Headless-only, subscription-only engines. A done
  claim needs executed proof (`python3 loopfactory.py check` / `validate` /
  `qa`).
- **Skills each working session should consult**: `ringer` (any model-calling
  runs), `atlas` (drift checks), repo runbooks (`factory-pipeline.md`,
  `process-change-qa.md`, `promotion-ladder.md`).
- **Concurrent effort (Ankit, 2026-08-02)**: the sales process is being
  revamped in a SEPARATE worktree/session in parallel with this map. Reconcile
  at the end: (a) if that tree touches this repo, merge deliberately — check
  for wayfinder/ and departments/ conflicts before landing either side; (b) the
  revamped sales process is expected to arrive as the first NEW v2 department
  after ticket 19 closes (see Out of scope), through the normal F0–F6 pipeline,
  not as a mid-map merge.
- **Grounding sources**: Hermes ELI5 gap packet (2026-08-02, in map history);
  `docs/GRAPH-ENGINE-NOTES.md`; `docs/KNOWN-LIMITS.md`; graph_agent repo;
  learning_github/knowledge_repo; JayZeeDesign/codebase-harness; Bee notes
  (Jason / AI Builder Club, 2026-08-02).

## Decisions so far

<!-- one line per closed ticket: gist + link -->

- [Survey graph_agent for v2 map-store adoption](tickets/01-graph-agent-engine-survey.md) —
  all six adopt-don't-rebuild capabilities verified live (smoke test passes);
  both known gaps (`rebuild` verb, CONTROL_STATE.json) are ~10-20-line fixes;
  owner/domain coupling is narrow and mechanical to strip — ticket 12 is fully
  decidable from the adopt/strip/gap table.
- [Survey knowledge_repo concept-to-process + eval scripts](tickets/02-knowledge-repo-scripts-survey.md) —
  authoring pipeline duplicates F0-F3 (SKIP), but `evaluate_node.py` /
  `qa_agent_package.py` / `models.json` are direct PORTs answering ticket 09
  (golden-set + holdout, weighted verdicts, deterministic-then-blind-cross-model
  split); `heal_agent_package.py`'s fix-class taxonomy composes with the heal
  ladder and feeds ticket 13 (classes that may never auto-apply).
- [Survey Jason's codebase-harness repo](tickets/03-codebase-harness-survey.md) —
  it's a repo-onboarding skill pack, not a telemetry/supervisor runtime; adopt
  its deterministic-checks-vs-independent-judge split (feeds ticket 09) and
  capped-retry-then-escalate + no-stale-proof rules (feeds ticket 13); nothing
  for ticket 07; watcher-of-watchers needs fresh design.
- [AgentSpace ingestion compatibility check](tickets/06-agentspace-compat-check.md) —
  local AgentSpace repo is an unrelated same-name OSS project; the real target
  is Google **Gemini Enterprise** (Agentspace renamed 2026): connector/data-store
  ingestion, push via Discovery Engine API or pull via NDJSON in GCS/BigQuery,
  records need a string `id` + flat structData, no generic webhook — ticket 10's
  contract should emit id-keyed flat JSON-lines; Linear is itself a connector,
  so the surfaces compose.
- [Inventory current run records + token-source options](tickets/04-telemetry-inventory.md) —
  today's three record layers (kernel receipts, manager telemetry, social node
  receipts) carry NO engine/model/token/cost data; Ringer's `tokens` field is a
  regex scrape that verifiably matches nothing for Claude/Gemini engines; the
  richest verified source is Claude CLI session JSONL `message.usage`; lowest-cost
  path = switch Claude engine to `--output-format json` and parse usage. No GLM
  plan wiring exists (only a disallowed OpenRouter-metered route). 2 of 12 v2
  fields ready, 8 partial with named paths, trigger + auth class have no source.
- [Idempotency + retry audit of podcast nodes](tickets/08-idempotency-retry-audit.md) —
  replay/crash safety is uneven: escalate_outbox.py proven crash-safe, but
  factory/manager.py writes STATE/runs/heartbeats WITHOUT the fcntl lock used
  elsewhere (confirmed live: two row shapes sharing one epoch counter), manager
  escalations lack fingerprint dedup, three sensor nodes write no receipt at
  all, observations.jsonl grows unbounded with duplicate rows, and the
  self-heal ladder isn't wired into the daily chain. Ranked 8-gap list with
  file:line cites = ticket 15's work order.
- [Decide the v2 run-record / telemetry contract](tickets/07-run-record-contract.md) —
  **LOCKED** (Ankit, all recommendations accepted): 18-field receipt per node
  run, append-only `runs-v2.jsonl` per department + id-keyed flat NDJSON estate
  feed; cost on flat-fee lanes = quota proxy, never dollars; `run_id` stamped
  on Linear cards; retention parked (90-day default); podcast scope = in-repo
  steps only. Contract detail: `drafts/07-run-record-proposal.md` (ACCEPTED).
- [Decide the v2 evaluator framework](tickets/09-evaluator-framework.md) —
  **LOCKED**: deterministic Tier-1 first (fail never reaches a model); Tier-2
  judgment by a different engine than the producer, mandatory only for
  promotion-counted + human-visible work; block/revise/allow weighted verdicts
  gate receipts; golden set required before Tier-2 gates (else advisory-only);
  registry in templates/ + overrides; estate drift alarms watch the evaluators
  themselves. Detail: `drafts/09-evaluator-proposal.md` (ACCEPTED).
- [Decide graph-engine adoption](tickets/12-graph-engine-decision.md) —
  **LOCKED: ADOPT graph_agent as map store, sequenced after ticket 19**; pilot
  stays on JSON (run-card tables will consume runs-v2 records); prep (2 small
  fixes + coupling strip) runs parallel in graph_agent's repo; migration is a
  fresh post-map effort. Detail: `drafts/12-graph-engine-proposal.md` (ACCEPTED).
- [Define the gated auto-patch healing class](tickets/13-gated-auto-patch-class.md) —
  **LOCKED (v2, automation-first per Ankit)**: five automated rungs before any
  human — retry → known-fix playbook → self-patch (full QA + re-shadow) →
  cross-model repair (different engine, fresh diagnosis, Ringer one-task) →
  contain-and-degrade — then L5 human with full dossier + one recommended
  action. Budget 10/dept/week; 3-fail demotion with 7-clean-day auto-reset;
  governance/kernel/external-effects floor unchanged. Ticket 18 scope now
  includes wiring the existing heal ladder into the daily chain. Detail:
  `drafts/13-auto-patch-proposal.md` (v2, ACCEPTED).
- [Prototype the interim heijunka/andon board](tickets/11-interim-andon-board.md) —
  **DESIGN LOCKED** (Ankit: "this is great"): v4 approved after 4 verified
  iterations (0 contrast failures, 0 overflow at 3 viewports); zones =
  objectives + trends up top → andon/approvals → activity + telemetry →
  loop-specific bottom. Generalized on Ankit's direction into **Board
  Template v1** — the standard grammar for ANY loop, loop content as data:
  `drafts/17-board-template-spec.md` (ticket 17's build contract).
  Reference: `prototypes/11-andon-board.html`.
- [Decide the estate reporting rollup contract](tickets/10-estate-rollup-contract.md) —
  **LOCKED**: estate PULLS department records read-only and emits
  `board-feed.ndjson` (5 kinds: dept_status, active_run, andon, approval,
  metrics), rebuildable from records alone; andon reuses manager finding codes;
  daily metrics on board, drill-down in runs-v2; approvals inline with card
  links. Detail: `drafts/10-rollup-proposal.md` (ACCEPTED).
- [Decide OAuth-expiry + engine-outage policy](tickets/14-auth-route-policy.md) —
  **LOCKED**: wrapper-detected block, never metered fallback; auth-block is an
  environment gate (no heal strikes); one outbox item + Linear card per lane
  with the re-auth command; only model-calling steps pause; **GLM removed from
  the engine roster** (no subscription wiring exists). Detail:
  `drafts/14-auth-policy-proposal.md` (ACCEPTED).
- [Retrieve Bee notes (Jason session) + diff vs Hermes packet](tickets/05-bee-notes-retrieval.md) —
  retrieval succeeded (conversation matches the saved transcript); four deltas,
  no new tickets: watchers-can-fail → second-order check into tickets 09/13;
  captain/first-mate/worktree orchestration state → ticket 07 context; specs in
  a queryable DB not flat files → strengthens ticket 12's adoption case;
  repo-expert standing role → fog. Transcript cutoff at 08:16 flagged, not
  guessed at.

## Not yet specified

- Migration path for the social department (and future departments) onto the
  v2 contract — graduates once the podcast pilot (15–19) proves it.
- Per-department cost/usage budget alarms and the babysitting-tax scoreboard
  (ATLAS N009) — graduates once telemetry (07/15) lands and real numbers exist.
- Estate health-loop thresholds (drift %, retry ceilings, staleness windows) —
  graduates with rollup contract (10) data.
- Backup/restore + kill-switch drill runbook (from the Hermes checklist) —
  graduates once the v2 run-record store (07) fixes what needs backing up.
- Remote memory / OB digest wiring (`factory/memory.py` seams) for department
  learnings — graduates after evaluator framework (09) defines what a
  "lesson" record is.
- New revenue / lead-follow-up department as v2's first native output —
  intentionally after this map proves v2 on podcast (see Out of scope).
- Real data sources for board objectives — publish-reliability and hopper
  counts (podcast pipeline records), guest-funnel stage counts (prospecting
  records/HubSpot), audience downloads/shares (podcast host analytics) must
  each get a feed adapter; graduates at ticket 17 wiring for what department
  records already hold, post-map for external analytics sources.
- Social Linear-lane map debt — create_review_card / linear_close_issue /
  linear_read_comments run in the daily flow but have no procedural-graph
  nodes (landed pre-map via PR #7/#8); currently held by documented
  untraced_allowed rationales (2026-08-02). Graduates with the social v2
  migration: author SG nodes + concept traces.
- GLM engine lane re-add — removed from the roster at ticket 14 (only
  forbidden metered wiring exists); graduates if/when real GLM
  subscription-plan CLI wiring lands.
- Run-record retention/rotation tuning — 90-day default parked at ticket 07;
  graduates when real runs-v2 volume data exists.
- External podcast drafting pipeline emits run-records (extend
  `dag-projection-v1` receipt) — parked at ticket 07's scope call; graduates
  after ticket 19 or when that pipeline is next touched.
- Standing "repo expert agent" role and the captain→first-mate→worktree-session
  orchestration mechanic (Bee delta, ticket 05) — how department workers get a
  repo-bound expert context and task-to-session state; graduates once ticket 07
  fixes what per-run state exists to bind to.

## Out of scope

- **AgentSpace platform selection and rollout** — owner decision 3 scoped this
  map to the contract + interim board; the surface choice is a fresh effort
  once the contract exists (ticket 06 only checks compatibility).
- **Standing the new revenue/lead-follow-up department** — owner decision 2
  picked podcast as the sole pilot; the revenue department returns as its own
  effort consuming v2.
- **graph_agent migration** — direction LOCKED at ticket 12 (adopt as map
  store), but the migration itself starts after ticket 19 closes, as a fresh
  effort; only the small prep (rebuild verb, CONTROL_STATE.json, coupling
  strip) runs in parallel, in graph_agent's own repo.
- **Widening external-send/write/spend autonomy** — promotion of external
  action classes stays on the existing promotion ladder with owner sign-off;
  not touched by this map (auto-patch is internal-only by definition, ticket 13).
