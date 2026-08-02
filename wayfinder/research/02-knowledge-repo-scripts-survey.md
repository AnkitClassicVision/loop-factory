# Research: knowledge_repo scripts/templates survey (ticket 02)

Source: `/mnt/d_drive/repos/learning_github/knowledge_repo/scripts/*` and
`concepts/_template*`. Judged against three loop-factory consumers: (a) the
evaluator framework (ticket 09, still open), (b) the F1-F3 authoring flow
(`runbooks/factory-pipeline.md`), (c) node improvement/heal loops
(`factory/heal_ladder.py`).

## What knowledge_repo actually is

A separate authoring paradigm called "AAC Agent Package" (AAC 2.5): vague idea
→ `atlas/atlas.json` (ATLAS territory map, evidence-state) → SQLite-backed
"concept flow map" (Justin Sung river-map, one DB per package under
`<slug>_concept_map/knowledge/knowledge.db`) → AAC process graph (JSON node
cards under `process/nodes/*.aac.json`) → validate → QA → self-heal →
export. One command (`run_pipeline.py`) runs synthesize → cards → validate →
QA → heal-loop → re-validate → export. Everything is designed to run
"human-over-the-loop": the pipeline never blocks on inline human approval
except for named highest-risk gates (golden grading, residue signing, lane
promotion, irreversible actions).

Loop-factory already has its own F1-F3 authoring paradigm: markdown
`concept-map.md` (Q#-traced, human-authored, locked at INTENT LOCK) +
markdown `procedural-graph.md` + machine `subgraphs.json`, linted by
`factory/graphs.py` (guard-order + traceability, not a full 3-layer
crosswalk), charter-driven via `charter_loader.py`. **The two authoring
substrates are not compatible or mergeable** — one is SQLite+Mermaid+AAC-JSON,
the other is markdown+YAML+JSON subgraphs. Recommendation below is judged on
that basis: port the *patterns/mechanisms*, skip the *substrate*.

## Per-script findings

### `atlas_to_concept.py` — SKIP (substrate mismatch)
Reads `atlas/atlas.json` (ATLAS territory map), deterministically synthesizes
a SQL patch that populates a SQLite concept-map DB (trunk = spine_path,
branches = clusters, chunked 2-3, bridge nodes for >3-member clusters),
writes `atlas/crosswalk.json` (ATLAS id ↔ concept node id), then shells out to
the concept package's own `apply_sql.py` / `lint_db.py` / `export_map.py`.
Refuses (exit 2, listing exact fixes) rather than emit a map that would fail
lint — spine not locked, cluster member also on spine, single-member cluster
with no proof_slot, etc.
**Contract**: in = `atlas.json` (spine, spine_path 3-9 ids, clusters, nodes
tagged confidence/provenance/status/proof_slot, edges typed
serves/prereq/part-of/causes/feeds/differentiated-by/shaky/tension). out =
SQL patch + `crosswalk.json`, applied+linted+exported as a side effect.
**Recommendation: SKIP the script itself** — loop-factory's concept map is
markdown, not SQLite, and F1's concept-map.md is human-authored/locked at
INTENT LOCK, not machine-synthesized from a prior evidence-state layer.
Porting this would require also porting the SQLite concept-map package
(`_template_concept_map`), which duplicates a decision loop-factory already
made. **Worth stealing as a pattern**: (1) the generator-refuses-with-exact-fix
discipline (concrete, testable refusal messages rather than silent best-effort
generation) is a good model for `loopfactory.py validate`; (2) the
confidence/provenance/status/proof_slot node-tagging vocabulary is a sharper
evidence-state model than loop-factory's current concept-map.md (which has no
confidence or proof-slot column) — worth proposing as a concept-map.md.tmpl
enhancement in a separate ticket, not this one.

### `concept_to_process.py` — SKIP (substrate mismatch), pattern port for cards
Derives an AAC "process graph + cards" from the concept map + ATLAS:
`workflow.aac.json` (graph: nodes/edges/sinks/owners/automation_policy) +
one `process/nodes/<id>.aac.json` card per node + prompt stub per C/A node +
golden-set skeleton. Runtime-mode suggestion (D/C/A/H) from keyword regex
against node purpose text (deterministic heuristic, human confirms). Every
node card carries an `objective` block (metric + target + holdout_ref +
auto_adopt policy) and a `supervision` block (human_over_loop vs
inline_human_gate, with a required justification for inline gates). Never
overwrites an existing `workflow.aac.json` or card unless `--force`.
**Contract**: in = `atlas.json`, `crosswalk.json`, concept-map export
(`latest.rivermap.json`), optional `source_packet.aac.json`. out =
`workflow.aac.json`, `process/nodes/*.aac.json`, `process/prompts/*.md`,
`process/evals/<slug>.golden.json`, updated `crosswalk.json.process_nodes`.
**Recommendation: SKIP as a script** — loop-factory's F3 (hand-author runtime
nodes per the graph's script-vs-LLM triage, from `runbooks/factory-pipeline.md`
step 4) is already a human-authored step against `subgraphs.json`, not a
generator. **Port the card schema concepts**: the `objective` block
(primary metric + target + guardrails + holdout_ref + auto_adopt rule) is
close to what ticket 09 needs to define for the evaluator registry, and the
`runtime_mode` D/C/A/H keyword-suggestion table (send/pay/delete → A;
approve/review/sign → H; classify/draft/summarize → C; pull/fetch/route → D)
is a reusable heuristic for F1-F3's "script-vs-LLM triage" (Q8 in
`concept-map.md.tmpl`) — cite it in the interview question bank as a starting
heuristic, not a generator.

### `evaluate_node.py` — PORT (direct hit for ticket 09)
Scores one node's current (model, prompt_version) against its golden set.
Golden-set truth rule is load-bearing and worth copying verbatim: `grade=right`
→ `proposed` is truth; `grade=edit`/`wrong` → `corrected` is truth if present;
`wrong` with no correction only counts correct if the node refuses (a
proposed-and-wrong answer scores 0, it can never accidentally score as
right). Split discipline: OPEN split for iteration, HOLDOUT split sealed once
in `.holdout/evals/<node>.holdout.json` and only read with `--split holdout`.
Executors are pluggable and explicitly logged as stubs
(`replay_proposed`/`replay_truth`) until live model executors land — this
"executor provenance in the report" habit prevents a stub harness result from
being mistaken for a real model-quality measurement.
**Contract**: in = `<pkg>/process/nodes/<node>.aac.json` (needs `eval_ref`/
`objective.primary.eval_ref`), golden-set JSON, holdout-keys JSON. out =
`exports/evals/<node>__<split>__<executor>.json` with `golden_accuracy`,
`n_graded`, `per_example` results, `model_cost_rank` (via `models.json`).
**Recommendation: PORT the scoring logic + split discipline directly into the
v2 evaluator's deterministic-first ladder.** This is exactly ticket 09's
"deterministic checks first" layer for judgment nodes: golden-set replay,
open/holdout split sealing, executor-provenance tagging. It slots naturally
below loop-factory's schema/required-field/duplicate/date/permission checks —
those gate structure, this gates a judgment node's actual output quality
against graded examples. Needs adaptation: golden sets and node cards must
live in loop-factory's run-record/receipt shape (ticket 07), not
`process/nodes/*.aac.json`; `models.json`'s cost-rank ladder needs
loop-factory's actual model roster.

### `improve_node.py` — PORT (pattern), adapt heavily for heal-loop wiring
Karpathy-style optimization loop for one C/A node: seals a holdout split on
first run (every 3rd graded example, never touched again outside the gate),
evaluates champion (current model/prompt) vs cheaper challengers from
`models.json` on OPEN only, ranks by (golden_accuracy desc, cost_rank asc),
and if a challenger wins, re-runs BOTH champion and challenger on the sealed
HOLDOUT once as a no-regression gate before adopting. Adoption requires
`auto_adopt: cheaper_or_better_with_holdout_pass` in policy AND holdout pass;
otherwise queues a proposal instead of touching the card. Adopted changes
append to `exports/improvement_ledger.jsonl` and re-trigger QA
(`re_enter_gates`). Never touches golden grades, hard_refuse, lanes, or gates.
**Contract**: in = node card + graded golden set + `models.json` ladder. out
= mutated node card (model field) on auto-adopt, OR
`exports/improvement_proposals/<node>.json`, + ledger entry.
**Recommendation: PORT the decision logic (holdout-gated cheaper-or-better
adoption with a sealed no-regression check) into node improvement loops** —
this is a stronger and more auditable version of "try a cheaper model" than
anything currently in loop-factory. Directly useful for the heal ladder's L2
(Ringer heal: read-only review then fix swarm) as the *acceptance test* for a
proposed node change — L2 could require this holdout-gate to pass before a
fix is accepted, not just before an escalation to L4. Do not port the file
verbatim: it is wired to `.aac.json` cards and the SQLite/rivermap layer;
re-implement the algorithm against loop-factory's node/run-record schema.

### `qa_agent_package.py` — PORT (direct hit for ticket 09 + F4/F6 QA)
Dark-factory-style QA auditor for a whole package. Re-derives every
expectation from sources (atlas.json, schema requirement lists) rather than
trusting `CONTROL_STATE.json`/generator logs/builder claims, and re-runs the
concept-map lint itself rather than trusting a cached result — the
"auditor never trusts the builder's own say-so" discipline maps directly onto
loop-factory's rule 8 (receipt-gated steps, no step done on a model's/log's
say-so). Deterministic scenarios (spine locked, nodes tagged, lint clean,
crosswalk complete, workflow required fields, refusal sinks wired, cards
complete, telemetry/supervision/objective present, golden-set graded, no
leaked identifiers via a regex scan for email/phone/SSN/API-key patterns) are
graded here; three LLM-only scenarios (concept map reads as sense-making, not
a renamed step list; spine is falsifiable; cards are in owner language) are
marked `PENDING_LLM` for a separate blind cross-model pass — this is the
literal "deterministic checks first, cross-model LLM judgment second" split
ticket 09 asks about. Scoring: PASS=1.0/PARTIAL=0.5/FAIL=0.0, weighted
critical=3/major=2/minor=1, block on any critical FAIL or score<0.75, revise
on any major FAIL, else allow. Writes `qa_findings.md` with WHAT failed only
— never the weights/criteria — to the builder, keeping full criteria+results
in a gitignored `.holdout/` copy (an actual information barrier between
grading and building, not just a QA report).
**Contract**: in = atlas.json, crosswalk.json, concept-map export, workflow +
node cards, golden set, package file tree (leak scan). out =
`exports/qa_report.json` (full), `exports/qa_findings.md` (findings only, no
criteria), `.holdout/scenarios/*.yml` + `.holdout/results/qa-NNN.json`.
**Recommendation: PORT the verdict model + information-barrier pattern into
the evaluator framework as its "QA gate" tier**: weighted
critical/major/minor scoring with a block/revise/allow verdict, deterministic
scenarios executed inline, LLM scenarios marked pending for a separate blind
cross-model pass, and the finding-only bridge document. This is close to a
finished design for the ticket-09 "when a cross-model LLM evaluator is
required" question — the answer this script encodes is "always, for judgment
scenarios a deterministic check cannot grade, and the model that finds issues
is architecturally blind to the criteria/weights." The regex-based
PII/secret leak scan is directly reusable as a factory-wide pre-QA check
(complements CLAUDE.md's "no PHI/secrets/raw message bodies" rule with an
executed check instead of just a written rule).

### `heal_agent_package.py` — PORT (pattern), do not duplicate `heal_ladder.py`
Splits QA findings by fix class per the workflow card's `automation_policy`:
AUTO classes (`stale_exports`, `derivable_fields`, `missing_stubs`,
`control_state_sync`) get applied here with no human — e.g. re-export a stale
concept map, inject a missing `telemetry`/`supervision`/`automation_policy`
block from convention, create a missing prompt stub. PROPOSAL classes
(meaning changes, runtime assignment, golden grades, leak findings) are never
auto-applied; they're written to `exports/repair_proposals.json` for the
human-over-the-loop queue with a proposed action string per finding. One pass
per invocation; `run_pipeline.py` loops heal→validate→qa until no more
auto-fixable findings remain (bounded by `--max-heal`).
**Contract**: in = `exports/qa_report.json` (scenarios with `fix_class` +
`auto_fixable`), workflow card's `automation_policy.self_healing`. out =
mutated cards/exports (auto class), `exports/repair_proposals.json`
(proposal class), updated `CONTROL_STATE.json.healing`.
**Recommendation: this is NOT a duplicate of `factory/heal_ladder.py`, but the
two need to be composed, not merged.** `heal_ladder.py` is an *escalation
state machine* (L1 retry → L2 Ringer heal → L4 process-change card, with
oscillation detection and a hard ban on touching immutable invariants) — it
answers "how many times do we retry before escalating." `heal_agent_package.py`
is a *fix-classification taxonomy* — it answers "which specific findings may
ever be auto-applied vs. must always go to a human, regardless of retry
count." Loop-factory's heal ladder currently has no equivalent taxonomy: L1/L2
auto-heal actions aren't classified by "derivable-by-convention" vs
"meaning-change" the way this script's `auto_fix_classes` /
`proposal_only_classes` split does. **Port the taxonomy concept into
`heal_ladder.py`'s L1/L2 actions**: give each heal action a fix-class tag and
hard-code that meaning/runtime/grading/leak-adjacent classes can never be
auto-applied regardless of heal level — this directly reinforces loop-factory
rule 3 (always-human floor) and rule 2 (deny-by-default) with an explicit
allowlist instead of relying on heal_ladder's IMMUTABLE_INVARIANTS set alone
(which covers charter-level invariants, not QA-finding classes).

### `new_agent_package.py` — SKIP
Scaffolds a new AAC package by copying `_template_agent_package` +
`_template_concept_map`, personalizing `{{TOPIC_NAME}}`/`{{SLUG}}` tokens, and
seeding the SQLite DB's root node row via `sqlite3`.
**Recommendation: SKIP.** Loop-factory already has an equivalent, better-fit
F0 scaffolder (`loopfactory.py scaffold`) against its own template set
(`templates/*.tmpl`). No new pattern here beyond template-token substitution,
which loop-factory's scaffold step already does.

### `export_agent_map.py` — WRAP (small, situational)
Combines the concept-map export, workflow card, node cards, atlas, and
readiness report into one Mermaid flowchart (`agent_map.mmd`, concept
subgraph + process subgraph + dashed "serves" links from concept to process
nodes) and one unified JSON (`agent_map.json`). Read-only combinator, no
mutation.
**Contract**: in = concept-map export, `workflow.aac.json` + node cards,
atlas.json, crosswalk.json, readiness_report.json. out = `agent_map.mmd` +
`agent_map.json`.
**Recommendation: WRAP, low priority.** Loop-factory's `procedural-graph.md`
already embeds a Mermaid graph by hand, and `subgraphs.json` is the machine
form — there's no existing script that renders BOTH the concept-map.md and
subgraphs.json into one combined Mermaid diagram with cross-links. If a
"seeit"-style visual audit of a department's C-map ↔ P-graph traceability
becomes a recurring need (the `seeit` skill already exists for this general
purpose), this script's cross-link rendering technique (concept node → serves
→ process node, dashed edge) is a reasonable pattern to imitate, but it is not
a priority port — no open ticket currently asks for this.

### `models.json` — PORT (trivial, needed by evaluate_node/improve_node ports)
Four-line cost-rank ladder: `{"id": "<model-id>", "cost_rank": N, "tier":
"..."}`, 1=cheapest. Read by both `evaluate_node.py` (attaches
`model_cost_rank` to eval reports) and `improve_node.py` (candidate ranking
and champion-rank fallback).
**Recommendation: PORT as a small config file** wherever the evaluator
framework's model-swap logic lands — it's the one piece of literal config the
evaluate/improve port depends on. Needs loop-factory's actual model roster
and should probably live next to whatever module owns the improvement loop
rather than in `factory/` generically, since it's evaluator-specific config,
not a factory-wide constant.

## Template packages

### `concepts/_template` and `concepts/_template_concept_map` — SKIP (near-identical; substrate mismatch)
Both are the same self-contained "concept package" skeleton: SQLite DB
(`knowledge/knowledge.db`) + migrations (`001_init.sql` schema,
`002_triggers.sql`, `003_seed_rel_types.sql`, `004_lint_queries.sql`) +
`apply_sql.py`/`lint_db.py`/`export_map.py` + an `agent_knowledge_playbook.md`
prompt. `_template` appears to be the older/generic version;
`_template_concept_map` is the one actually referenced by
`new_agent_package.py` for AAC packages — the two are effectively
duplicates of each other inside knowledge_repo itself (worth flagging to
Ankit if knowledge_repo is still being maintained: `_template` may be dead).
**Recommendation: SKIP for loop-factory.** This is the SQLite/Mermaid
concept-map substrate discussed above; porting it would require adopting a
second, parallel concept-map format alongside `concept-map.md.tmpl`.

### `concepts/_template_agent_package` — SKIP (substrate), read for card-schema reference only
The AAC wrapper: `atlas/atlas.json` (evidence-state skeleton),
`CONTROL_STATE.json` (per-layer status tracker: atlas/concept_map/process/
crosswalk/readiness/qa/healing/alignment, each with a `status` field and a
`flags` array), `CLAUDE.md` (the package's own operating manual — "Non-
Negotiable Order," "Self-Correcting Reinforcement," "Definition of Done"),
`process/README.md` (card-field reference). No production code; purely
scaffolding + documentation.
**Recommendation: SKIP as a package**, but its `CONTROL_STATE.json` shape (one
status+flags block per pipeline layer, updated by each script as it runs) is
a clean small pattern worth citing if loop-factory ever wants a lighter-weight
per-department "where are we in F0-F6" status file distinct from the heavier
`departments/<dept>/state/` records — not urgent, no open ticket needs it.

## Summary table

| Item | Verdict | Primary consumer |
|---|---|---|
| `atlas_to_concept.py` | SKIP (pattern: refuse-with-fix, evidence tags) | none directly |
| `concept_to_process.py` | SKIP (pattern: objective block, D/C/A/H heuristic) | F1-F3 interview question bank |
| `evaluate_node.py` | **PORT** | ticket 09 evaluator |
| `improve_node.py` | **PORT** (adapt) | ticket 09 evaluator + heal L2 |
| `qa_agent_package.py` | **PORT** | ticket 09 evaluator + F4/F6 QA |
| `heal_agent_package.py` | **PORT** (taxonomy only) | `factory/heal_ladder.py` L1/L2 |
| `new_agent_package.py` | SKIP | none (duplicate of `loopfactory.py scaffold`) |
| `export_agent_map.py` | WRAP (low priority) | `seeit`-style visual audit, no open ticket |
| `models.json` | PORT (trivial config) | ticket 09 evaluator |
| `_template` / `_template_concept_map` | SKIP | none |
| `_template_agent_package` | SKIP | none (CONTROL_STATE shape noted, not urgent) |

## Answer to ticket 09's open questions, informed by this survey

- **Deterministic-first ladder**: `qa_agent_package.py`'s scenario list is a
  workable template — schema/required-field checks (workflow/card required
  fields), duplicate/structural checks (crosswalk completeness, graph
  reachability to sinks), and a regex leak scan, all executed before any LLM
  scenario runs.
- **When cross-model LLM judgment is required**: this script's answer is "for
  every scenario a deterministic check cannot grade" (its three `LLM_SCENARIOS`
  are explicitly the ones needing "does this make sense" judgment, not
  schema/structure) — and the LLM pass must be architecturally blind to the
  criteria/weights (the `.holdout/` gitignored split), which satisfies loop-
  factory's cross-model-for-promotion-counted-runs rule from CLAUDE.md by
  construction if implemented the same way.
- **Verdict → run record/receipt**: `qa_agent_package.py`'s
  block/revise/allow verdict plus its weighted satisfaction score is a direct
  fit for a receipt payload; `evaluate_node.py`'s per-example
  correct/incorrect breakdown plus executor provenance is a direct fit for
  the evidence attached to that receipt.
- **Per-node-class eval registry**: `models.json` + the node card's
  `objective` block (metric, target, guardrails, cost direction, holdout_ref,
  auto_adopt rule) is close to a working registry shape — each node class
  would need one of these plus a golden-set path, and new departments would
  inherit the *shape* (not the AAC JSON substrate) from a loop-factory
  template.
