# 002 — Mine knowledge_repo graph patterns

Status: CLOSED · Type: research (AFK) · Resolved: 2026-08-02 by codex explore sub-agent

## Question

What does `/mnt/d_drive/repos/learning_github/knowledge_repo` (the repo Ankit called "leaning_github_knowdoe") do, and which code-directed-graph patterns transfer to loop-factory?

## Resolution

- Three layers: ATLAS evidence map (JSON), Concept River Map (SQLite + typed edges: precedes/is_a/part_of/causes/related), AAC process graph (`workflow.aac.json`: D/C/A/H node modes, deterministic router, conditional edges, refusal + hard-refusal sinks, per-node run cards — runcard.py:22-69).
- **The AAC graph is declarative, NOT executable.** No live dispatcher exists; `evaluate_node.py` replays saved answers; live executors deferred to a future "S6 compiler". Traversal is validation-time reachability only (validate_agent_package.py:143-171).
- Same gap in loop-factory: `subgraphs.json` is a flat node manifest (templates/subgraphs.json.tmpl), `factory/graphs.py` is lint/traceability only, departments hardcode order in shell (social_daily.sh:243-348, podcast_daily.sh:21-46).

### Patterns to transfer (feeds ticket 008)

1. **Typed execution projection** — extend subgraphs.json nodes (impl, runtime_mode, io contracts, receipt schema, action class, failure/state policy) + explicit edges (from, to, deterministic predicate over predecessor receipt, normal/refusal/escalation/terminal targets).
2. **Deterministic factory-owned graph runner** — loads only the release-pinned graph, runs one node, validates its receipt, persists state, evaluates edge predicates, advances only on proof. Effects stay behind the existing kernel dispatcher.
3. **Receipts as transition tokens** — a successor is runnable only on: valid predecessor receipt + output-schema conformance + satisfied edge predicate + graph/release version agreement.

### Does not transfer

The knowledge_repo build pipeline itself (concept→process generation, QA scoring, healing) — loop-factory has its own equivalents; importing it would violate the "no second graph system" directive.
