---
title: Retrieve Bee notes (Jason session) + diff vs Hermes packet
status: closed
type: research
assignee: wf-05-bee-notes
blocked_by: []
---

## Question

What is in the full Bee conversation notes from 2026-08-02 (Jason / AI Builder
Club session) about control graphs, knowledge graphs vs control graphs,
graph-of-loops drift, planner/executor split, and watcher loops — and what do
they add BEYOND the Hermes ELI5 packet already captured in this map's history?
Use the local `bee-cli` skill (`/home/ankit114/.claude/skills/bee-cli` or
~/.agents equivalents) to pull the conversation; the broader Bee index timed
out earlier today, so if retrieval fails again, record that and fall back to
confirming the Hermes packet is the best available source. Output: a delta
list of requirements/ideas not yet reflected in this map's tickets.

Boundary: no raw transcript bodies in findings — extracted requirements only.

## Resolution

`bee-cli` retrieval worked on the first attempt (conversation `9614305`,
2026-08-02 08:01-08:22 ET) — the earlier timeout did not reproduce. The live
transcript matches an already-saved local copy (found during this task) under
`/mnt/d_drive/repos/OB_mybcat/docs/meetings/bee/2026-08-02-jason-*`, which an
older ticket, `003-bee-workshop-notes.md`, already resolved today against
this same session. Both retrievals stop at the identical mid-sentence cutoff
(08:16:11) — Bee's own capture gap, not a tool failure.

Diffed against the Hermes packet (and cross-checked against ticket 003's
prior resolution so nothing already-known gets re-flagged), four real
requirement deltas surfaced: (1) an explicit "watchers can still fail even
when present" caution — feeds tickets 09 and 13, arguing for an independent
second-order check rather than trusting one watcher layer; (2) a named
captain→first-mate→per-repo-worktree-session orchestration mechanic with its
own task-to-session state management need — feeds ticket 03 primarily, ticket
07 secondarily; (3) spec artifacts (MSD/spec docs/ADRs) living in a queryable
database rather than flat files — feeds ticket 02; (4) a "repo expert agent"
as a standing, repo-bound role distinct from a general orchestrator — feeds
ticket 03. One minor item (trigger taxonomy: time/goal/event) refines ticket
07's `trigger` field but isn't new. No delta needed a new ticket. Full
findings: `wayfinder/research/05-bee-notes-retrieval.md`.
