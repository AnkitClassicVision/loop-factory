---
title: Define the gated auto-patch healing class
status: closed
type: grilling
assignee: coordinator-fable
blocked_by: [09]
---

## Question

What exactly qualifies as a "low-risk internal patch" that a watch loop may
auto-apply (owner decision 4, 2026-08-02): the allowlist (runtime node fixes
with passing executed QA + full re-shadow + re-pin; never charter/governance
files, never kernel, never anything with external effects), the required
receipts and rollback path, the 3-strike demotion back to propose-only, the
promotion-ladder entry that turns it on per department, and how the watcher
itself is watched (owner, evaluator, limits, review cadence — the Bee
watcher-of-watchers point)? Decide with Ankit on top of ticket 09's evaluator
definitions. Hard rules 1–4 are the immovable frame.

Asset: coordinator-drafted proposal ready for reaction at
`wayfinder/drafts/13-auto-patch-proposal.md` (6-point class definition + 3
forks with recommendations).

## Resolution

Ankit rejected v1 as too human-in-the-loop, then accepted v2 in full
(2026-08-02): a five-rung automated repair ladder runs to exhaustion BEFORE
any human — L0 retry, L1 deterministic known-fix playbook, L2 self-patch
(versioned card + fresh full QA + re-shadow + re-pin), L3 cross-model repair
(a different engine takes an independent attempt from the failure dossier,
never reusing the failed diff, via Ringer one-task manifest), L4
contain-and-degrade (rollback to last-good, quarantine node, department keeps
running) — and only L5 pings a human, with the full attempt dossier and one
recommended action. Tripwires are automated and self-resetting: budget 10
auto-patches/department/week (breach jumps to L4+L5); 3 cumulative failed
attempts demote a node to propose-only with auto-reset after 7 clean days (no
human reset). The floor never automates: governance files, factory/kernel
code, external effects, new action classes; enabling the class per department
remains a promotion with owner sign-off. Ticket 18's scope now includes
wiring the EXISTING heal ladder into the daily chain (audit gap #7). Detail:
`wayfinder/drafts/13-auto-patch-proposal.md` (v2, ACCEPTED).
