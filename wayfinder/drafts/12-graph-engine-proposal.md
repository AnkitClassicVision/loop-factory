# Ticket 12 draft — graph-engine adoption decision

CANARY: blue paperclip

Status: **ACCEPTED — locked decision** (Ankit, 2026-08-02, both forks as
recommended; see ticket 12 Resolution). Grounded in ticket 01 (survey: all six
adopt-don't-rebuild capabilities verified live, smoke test passed; both known
gaps are ~10–20-line fixes; coupling strips cleanly), ticket 02 (knowledge_repo
authoring substrate SKIPped — graph_agent is the one engine worth adopting),
ticket 05 (Bee delta: specs in a queryable DB, not flat files), and
docs/GRAPH-ENGINE-NOTES.md's own integration path.

## What this is (plain English)

Whether your process maps move from plain JSON/markdown files into the
code-directed graph engine you already built (graph_agent: typed nodes and
edges, safety rules enforced by the database itself, run cards, health
scoring) — and when, relative to the podcast pilot.

## Recommendation: ADOPT — but sequenced AFTER the pilot

**Direction: yes, adopt graph_agent as the v2 map store.** The survey removed
the uncertainty that parked this: the engine works today (its full smoke chain
passed live), the two missing features are small, and the cleanup is
mechanical. The Bee session's specs-in-a-queryable-DB point and your explicit
"code-directed graphs" ask both land here.

**Sequencing: integration starts after ticket 19 closes**, as its own effort:

1. Now (inside this map, zero risk): keep JSON subgraphs for the podcast
   pilot. The locked run-record contract already anticipates this — if
   adopted, graph_agent's run-card tables CONSUME runs-v2.jsonl records, they
   never replace them. Nothing built in tickets 15–19 is throwaway.
2. Prep (small, parallel, in graph_agent's own repo): fix the two gaps
   (wire the `rebuild` CLI verb, emit CONTROL_STATE.json for single maps) and
   strip the owner/domain coupling per ticket 01's checklist.
3. After ticket 19: migrate maps (concept + procedural + subgraphs) into
   graph_agent as the store, keeping loop-factory's interview, traceability,
   and release layers on top — exactly the division of labor
   GRAPH-ENGINE-NOTES.md already records. Social migrates with its v2
   migration (already in fog).

Why not migrate during the pilot: it couples the map-store swap to the
telemetry/eval build, doubling ticket 19's failure surface — and the pilot
proves v2 fastest on the substrate that already runs. Why not "don't adopt":
the JSON layer has no typed edges, no refuse-reachability, no
enforced-at-write invariants — the safety properties you asked for with
code-directed graphs — and ticket 01 showed rebuilding them would recreate
what already exists and passes its tests.

## Forks (2)

1. **Adopt direction**: yes (recommended) / no / defer the decision itself.
2. **Prep timing**: do the graph_agent prep (2 small fixes + coupling strip)
   in parallel with tickets 15–19 (recommended — it's in a different repo,
   zero collision), vs strictly after.
