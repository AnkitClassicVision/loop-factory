---
title: Survey Jason's codebase-harness repo (AI Builder Club)
status: closed
type: research
assignee: wf-03-codebase-harness
blocked_by: []
---

## Question

What does https://github.com/JayZeeDesign/codebase-harness (Jason, AI Builder
Club, pointer captured 2026-08-02 in /mnt/d_drive/repos/loops_agents_jason)
actually implement — harness structure, skills, verification/eval loops,
telemetry — and which of its patterns are worth adopting for Loop Factory v2's
telemetry contract (ticket 07), evaluator framework (ticket 09), or watch
loops (ticket 13)? Read-only survey of the public repo; report reusable
patterns with file pointers, not code dumps.

## Resolution

codebase-harness (commit `8edc6af`) is a small, single-commit Claude Code
skill pack for onboarding unfamiliar repos to agent-driven dev — not a
telemetry/evaluator/supervisor runtime. It has no run-record schema at all
(nothing for ticket 07 beyond a minimal expected/observed/evidence verdict
envelope in `skills/pr/SKILL.md`). Its strongest contribution is to ticket 09:
`skills/pr/SKILL.md` splits verification into deterministic codified checks
(run by the orchestrator itself, never delegated) versus subjective agentic
judgment (delegated to a fresh, independent sub-agent) — the same
deterministic-first-then-model-judgment split ticket 09 is deciding, plus
independence-of-judge as precedent for loop-factory's cross-model rule. For
ticket 13 it contributes a capped-retry-then-escalate loop and a
no-stale-proof-across-fixes rule, but has zero prior art on the
watcher-of-watchers question — that has to be designed fresh. Full findings
with line-level pointers:
`/mnt/d_drive/repos/loop-factory/wayfinder/research/03-codebase-harness-survey.md`.
