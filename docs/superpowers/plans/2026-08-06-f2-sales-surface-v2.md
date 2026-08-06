# F2 Sales + Surface Compiler v2 (DONE-per-stage) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Checkbox steps.

**Goal:** Sales department F2 artifacts authored from the LOCKED intent (charter, concept map, procedural graph, stage-shaped subgraphs), and the surface compiler learns to render each stage's DONE + floor into its folder and the router — owner directive 2026-08-06.

**Split:** Lane (Ringer): surface compiler v2 + tests. Coordinator: all F2 authoring (charter/maps are judgment from the locked interview), integration, generation, validation.

## Frozen interface — surface compiler v2

- `subgraphs.json` subgraphs MAY carry two OPTIONAL fields (v1 lint ignores unknown subgraph keys — verified):
  `"stage": "<floors.yaml stage key>"` and `"done": {"conditions": ["<binary condition>", ...], "receipt": "<what proves it>"}`.
- CONTEXT.md generated region, only when `done` present, gains after the Node chain: `## DONE means` — one bullet per condition, then `Receipt: <receipt>`. When `stage` present, gains `## Floor` — exactly: "This stage holds the `<stage>` floor. Current values live in `../floors.yaml` (machine-written; numbers are never copied here — two copies guarantees one stale)."
- ROUTER.md table gains a `DONE means` column: first condition (+ ` (+N more)` when several), `—` when absent.
- ABSENT fields ⇒ byte-identical output to v2-less rendering (podcast surface must not drift; verified at integration by regenerating podcast and checking `git diff --quiet`).
- `check_surface` unchanged (regenerate-and-diff covers the new content automatically).
- Tests extend `tests/test_surface_compiler.py`: done+receipt rendered; floor pointer rendered; router column with truncation; absent-fields output contains no DONE/Floor sections; idempotency and human-region survival still green.

## Coordinator F2 authoring contract (from intent.md, LOCKED 2026-08-06)

- charter.yaml: mission (10 held qualified attributed calls/wk blended), objectives (held_calls_week floor 10; per-stage stock objectives measured in shadow), month-one halved budget (450 calls/wk, $0, 6 outbound/day, 2 new contacts/day, 840 worker-min/wk, 4 broker sweeps/wk), funnel: end_goal held 10/wk + linear transitions received→qualified(0.5)→conversation_live(0.6)→booked(0.4)→held(0.7) upstream-first (nervous_parked is a stage, not a cascade link), kill_if/pause_if verbatim from Q10, escalation (owner, 48h SLA, outbox→Telegram+Linear), human gates from Q12, never list (poaching, unverified receipts, recording content), memory podcast-pattern.
- subgraphs.json: seven v1-lint subgraphs — SG-RECEIVED, SG-QUALIFIED, SG-CONVERSATION-LIVE, SG-NERVOUS-PARKED, SG-BOOKED, SG-HELD (each with `stage` + `done` from Q4/readback, concept_refs, S1/S2/S3/S8 + send guards not_applicable with real rationales, nodes WITHOUT impl — F3 adds runtime) and SG-SENSE (watchdog: staleness truth, cross-lane suppression, drift, floors attention — the four context-is-king gates each named). untraced_allowed for the F0 scaffold runtime files.
- knowledge/concept-map.md: C1..C14 tracing every charter/graph element to its Q#.
- procedural-graph.md: stage table with DONE column mirroring subgraphs (single source: subgraphs carries the machine copy; the md narrates), guard rationale, F3 triage notes (script vs LLM per node).
- Validation gate: `python3 loopfactory.py validate --name sales` PASS; surface generated with v2; podcast surface byte-stable; full `check` green.
