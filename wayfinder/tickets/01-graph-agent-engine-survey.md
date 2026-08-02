---
title: Survey graph_agent for v2 map-store adoption
status: closed
type: research
assignee: wf-01-graph-agent
blocked_by: []
---

## Question

What exactly does the graph_agent repo (`/mnt/d_drive/repos/graph_agent`)
provide today that Loop Factory v2 could adopt as its map store (typed
nodes/edges, refuse-reachability, run cards, telemetry drift, health scoring),
what is documented-but-missing (`rebuild` verb, CONTROL_STATE.json), and what
owner/domain coupling must be stripped before reuse? Produce the concrete
adopt/strip/gap checklist that ticket 12 (adoption decision) needs.

Context: `docs/GRAPH-ENGINE-NOTES.md` in this repo parked this integration as
"deliberate, not now" — this ticket makes it decidable.

## Resolution

Full findings: `wayfinder/research/01-graph-agent-engine-survey.md`.

- All six "adopt, don't rebuild" items from GRAPH-ENGINE-NOTES.md are real and
  file:line-verified: trigger/linter parity (`graphagent/rules.py`), typed
  node/edge schema, refuse-reachability BFS (`validate.py:17-27`), PHI-safe
  run cards (`runcard.py`), node health scoring (`score.py`), and drift
  (`drift.py`). Confirmed live by running `tests/smoke_test.sh` — passes end
  to end (lint → validate → export → run → auto-downgrade → approve → score
  → doctor).
- Both documented-but-missing gaps confirmed: `rebuild` exists only as
  `mutate.replay()` (mutate.py:249-263) with no CLI verb wired in cli.py;
  `CONTROL_STATE.json` is only written for `--dual` maps (cli.py:57-80), so a
  plain single-map agent gets none by default. Both are small, well-scoped
  fixes (~10-20 lines each), not redesigns.
- Owner/domain coupling is narrow and mechanical to strip: the MyBCAT
  guideline block in `AGENTS.md`/`GEMINI.md` (lines 1-76, auto-synced,
  markers already present), `owner=Ankit`/`who=Ankit` in SPEC.md and
  test/smoke fixtures, and an optometry-flavored demo scenario
  ("After-Hours Intake" / "patient says clinic was closed"). None of it
  touches the reusable engine modules (rules/schema/mutate/validate/drift/
  score/runcard/cli all came back clean on grep).
- Full adopt/strip/gap table with file:line pointers is in the findings file,
  ready for ticket 12's adoption decision.
