# 003 — Recover bee workshop notes (Jason graph-engineering, 2026-08-02)

Status: CLOSED · Type: research (AFK) · Resolved: 2026-08-02 by codex search sub-agent

## Question

What did the 2026-08-02 bee-captured workshop (Jason / AI Builder Club) concretely recommend about control graphs, loop autonomy, watchers, and visual workspaces?

## Resolution

Sources (all local, verbatim quotes in the sub-agent report):
- `/mnt/d_drive/repos/OB_mybcat/docs/meetings/bee/2026-08-02-graph-engineering-workshop-raw.json`
- `.../2026-08-02-jason-graph-engineering-workshop-transcript.md` (presenter diarized "Unknown"; "Jason" is user-provided attribution)
- `.../2026-08-02-jason-graphs-to-mybcat-implementation-map.md` (derived analysis, not verbatim)

Key content:
- **Three-graph split:** knowledge graph = entity relationships for retrieval/context, "totally different thing" from execution; control graph = the SOP a workflow follows trigger→proven outcome; graph of loops = many recurring workflows compounding — "even just one loop is really hard… it will start drifting… when you have many different loops, it just compounds."
- **Watchers can fail:** "for each loop… another watch loop to look at the results" BUT "even if you do that, it might still fail." Implementation map countermeasures: independent deadman check (`estate-deadman.timer`), poisoned-registry/count tests causing visible hold, **false-green estate report stops promotion**.
- **Trigger modes for autonomy:** time-based, goal-based (clear definition of done, agent continues until finished), event-based — each needs explicit boundaries and persistent state.
- **Planner/executor split:** planner breaks approved spec into bounded tasks; executors in isolated sessions/worktrees; state management + review gates reconcile.
- **Standing directive:** "Do not build or install another graph system. Consolidate the department control plane first."
- Observability should be **receipt-based**; a visual control room renders canonical state and evidence, never becomes a second writable source of truth. "Telemetry"/"evals" never appear literally — schema comes from ticket 004 research, not the workshop.
