# Relationship to graph_agent (the knowledge-graph engine)

`/mnt/d_drive/repos/graph_agent` is the deeper, SQLite-backed graph engine this
factory's map process descends from (concept + procedural maps, typed
nodes/edges, governance fields, run cards). A read-only scout pass (2026-07-21)
established the division of labor:

## What graph_agent already does well (adopt, don't rebuild)

- Single-source invariants: each structural rule renders into BOTH write-time
  SQLite triggers and the linter's validator queries, so the live gate and the
  validator cannot drift (`graphagent/rules.py`).
- Typed node schema: 12 work types + 3 sinks, runtime_mode D/C/A/H, max_lane
  ladder (read_only→send, blocked), action_consequence, decision_class,
  confidence floors, 9-state spec_status.
- Typed edges (structural/semantic/flow, 20 seeded rel types; `why` + `how`
  mandatory on every edge, trigger-enforced).
- Refuse-reachability BFS: every C node must reach a refuse sink.
- PHI-safe telemetry: run cards carry hashes and pointers, never payloads.
- Node health scoring + telemetry drift (baseline-vs-recent regression).

## The three gaps the scout found — and where loop-factory closes them

| graph_agent gap | loop-factory answer |
|---|---|
| No interview/intake flow (question bank lives in a separate, unwired skill) | `interview/INTERVIEW.md` + `QUESTION_BANK.md` are first-class factory front end |
| Nodes bind to runtime by label match only — no manifest | `subgraphs.json` node `impl` fields + `factory/graphs.py` traceability check |
| No definition-vs-implementation drift check (its `drift` is metric regression only) | content-addressed releases + `check_drift` (live tree vs pinned release) |

## Integration path (deliberate, not now)

loop-factory v0.1 keeps its maps as JSON (subgraphs.json) for zero-dependency
portability. If/when a department needs the richer engine (typed edges,
refuse-reachability, run-card telemetry), adopt graph_agent as the map store
and keep loop-factory's interview, traceability, and release layers on top.
Before reuse, strip graph_agent's owner/domain coupling (MyBCAT guideline
blocks in its AGENTS.md/GEMINI.md, named-owner references, healthcare demo
strings) and note two documented-but-missing features there: the `rebuild`
verb and CONTROL_STATE.json for non-dual maps.
