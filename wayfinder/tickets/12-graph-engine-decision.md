---
title: Decide graph-engine adoption (graph_agent vs JSON maps)
status: closed
type: grilling
assignee: coordinator-fable
blocked_by: [01, 02]
---

## Question

Does v2 adopt graph_agent as the map store (typed nodes/edges,
refuse-reachability, run cards, health scoring) with loop-factory's
interview/traceability/release layers on top — per the integration path in
docs/GRAPH-ENGINE-NOTES.md — or keep zero-dependency JSON subgraphs for now,
and if adopting: what is stripped, what is ported, and in what order relative
to the podcast pilot? Decide with Ankit using tickets 01 and 02 findings.
"Code-directed graphs" was an explicit owner ask (2026-08-02).

Asset: coordinator-drafted proposal ready for reaction at
`wayfinder/drafts/12-graph-engine-proposal.md` (recommendation: ADOPT,
sequenced after the pilot, with parallel prep; 2 forks).

## Resolution

Ankit accepted the full proposal (2026-08-02): **ADOPT graph_agent as the v2
map store — sequenced AFTER ticket 19.** The podcast pilot stays on JSON
subgraphs (nothing in 15–19 is throwaway: graph_agent run-card tables will
CONSUME runs-v2.jsonl records, never replace them). Prep runs in parallel in
graph_agent's own repo (wire the `rebuild` CLI verb, emit CONTROL_STATE.json
for single maps, strip owner/domain coupling per ticket 01's checklist). The
migration itself (maps into graph_agent with loop-factory's interview/
traceability/release layers on top) is a fresh effort after this map closes —
recorded in Out of scope. Detail: `wayfinder/drafts/12-graph-engine-proposal.md`
(ACCEPTED).
