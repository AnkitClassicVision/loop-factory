# Ticket 13 draft v2 — self-healing ladder + gated auto-patch

CANARY: blue paperclip

Status: **ACCEPTED — locked contract** (Ankit, 2026-08-02, v2 all forks as
recommended; see ticket 13 Resolution). v1 was rejected as too
human-in-the-loop; v2 replaces "gate fails → ask a human" with a five-rung
automated repair ladder; a human appears only at the top, holding a
decision-ready dossier. Grounded in tickets 02, 03, 08, 09; Bee
watchers-can-fail; hard rules 1–4. Changes now follow process-change QA.

## What this is (plain English)

When something breaks, the department runs a full escalating repair sequence
on its own — retry, known fixes, patch itself, bring in a second stronger AI,
contain the damage — and only if ALL of that fails does a human get pinged,
with the whole attempt history and a recommended action attached. The human
decision becomes a 10-second review, not a diagnosis.

## The ladder (each rung exhausts before the next; every rung leaves receipts)

| Rung | Response | Detail |
|---|---|---|
| L0 | Retry | Bounded retries with backoff for transient failures (per ticket 08 policies). |
| L1 | Known fix | Deterministic playbook remediation from the fix-class catalog: restart, re-run, clear stale lock, rebuild artifact, re-fetch input. No model involved. |
| L2 | Self-patch | Auto-patch attempt: diagnose → versioned change card + patch → fresh full QA (ticket 09) → affected-map lint → re-shadow → apply + re-pin. |
| L3 | Cross-model repair | If L2's patch fails QA: a DIFFERENT engine lane gets the failure dossier and produces an independent fresh diagnosis + patch (never reusing the failed diff — no-stale-proof), same full gate sequence. Runs as a Ringer one-task manifest. |
| L4 | Contain + degrade | Quarantine the node: roll back to last-good pinned release, mark the node degraded, keep the rest of the department running. The loop stays alive in degraded mode instead of stopping. |
| L5 | Human, dossier in hand | Outbox item + Linear card carrying: every attempt (L0–L4), diagnostics, diffs, QA results, current degraded state, and ONE recommended action. Ten-second review, not a debugging session. |

## The fence (unchanged floor — these never automate)

- Auto-patch may only touch the department's own `runtime/` node code/config,
  only auto-fixable fix-classes. Meaning/intent, grading/evaluator, and
  leak-adjacent changes are always-human, at every rung.
- Governance files, factory/ and kernel/ code, external effects, new action
  classes: never touchable by any rung. Hard rules 1–4 intact.
- Enabling this class per department = promotion-ladder entry with owner
  sign-off; weekly review inspects receipts.

## Tripwires (automated, self-resetting)

- **Budget**: 10 auto-applied patches per department per week (was 3);
  breaching the budget jumps straight to L4+L5 — a department patching itself
  that often has a design problem, not a patch problem.
- **Demotion**: 3 cumulative failed L2+L3 attempts on one node → that node
  goes propose-only. **Auto-reset after 7 clean days** (was: human reset) —
  no human touchpoint to forget.
- **Watcher-of-watchers**: all ladder activity streams to the board feed
  (andon + metrics); estate cycle alarms on abnormal patch frequency or
  evaluator drift.

## Forks (4)

1. Weekly auto-patch budget = 10/department. Recommend 10.
2. Demotion = 3 cumulative fails per node, auto-reset after 7 clean days.
   Recommend as stated.
3. L3 cross-model repair via Ringer one-task manifest (different engine than
   L2). Recommend yes — it's the single biggest "robust before human" gain.
4. Ticket 18 scope also wires the EXISTING heal ladder into the daily chain
   (audit gap #7 — today healable incidents reach no heal attempt at all).
   Recommend yes — prerequisite plumbing for every rung above L0.
