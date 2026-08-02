# Loop Factory v2 — Verification Report (wayfinder ticket 19, map exit)

CANARY: blue paperclip
Date: 2026-08-02 · Branch: feat/loop-factory-v2 (7 commits) · Verifier: coordinator (Fable)

## Standard gates (all executed this session, after the final commit)

| Gate | Result |
|---|---|
| `python3 loopfactory.py check` | CHECK PASS (compileall + full pytest, 250+ tests) |
| `validate --name podcast` / `--name social` | ok: true / ok: true |
| Releases re-pinned from HEAD | podcast 834b72496a44ce3f · social 6f688bb22cc37415 · intent_locked |
| `qa` drift check both departments | mismatches: [] / [] |

## Forced-failure drills (live, this session)

| Drill | Result |
|---|---|
| Trigger replay (same node twice) | 2 records, IDENTICAL dedupe keys — replay visible, work harmless, both validate |
| Auth expiry (blocked record) | surfaces as an AUTH andon on the board feed, run_id carried |
| Metered/API violation | POLICY andon; excluded from stats (rollup runs=0) — the forbidden lane is an incident, never a number |
| Record-append sabotage (worker-kill class) | node exits nonzero — a record gap fails closed, never silent |
| Rebuild-from-receipts | real estate feed rebuilt twice → byte-identical (86 lines, 0 malformed) |
| Shadow floor | 79 real records across all departments: external_actions_taken = 0 in every one |

Crash-mid-write, evaluator-rejection, and auth-probe-blocks-model drills are
executed continuously in the suite (test_manager_hardening,
test_hitl_atomic, test_qa_verdict, test_engine_usage_parse).

## Live self-management proof (today's real daily chain)

- All 11 podcast nodes emitted validating runs-v2 records (79 today).
- The heal lane ran on a REAL incident: playbook selected → shadow proposal
  → honest verify FAIL (service inactive because shadow executes nothing).
- Observation rotation deduped 11,210 duplicate rows → 124 kept.
- Estate + department boards regenerated automatically at chain end; a real
  andon (pace_under) is visible on the live board.

## What v2 delivered (against the map's Destination)

1. Run-record contract live on every podcast node + social engine lanes
   (tokens/model/auth captured where models run).
2. Estate board feed + Board Template v1 renderer — any loop, data-driven,
   honest unknowns. Live at `estate/state/board.html`.
3. Evaluator framework (registry, weighted verdicts, golden-set advisory
   discipline) wired into social QA.
4. L0–L5 self-heal ladder machinery — PROPOSE-ONLY hard floor; heal lane
   wired into the daily chain (audit gap #7 closed).
5. New-loop entering process: scaffold inherits all of v2 (proven by
   scaffolding, running, boarding, and deleting a throwaway department).
6. Auth policy, GLM roster removal, idempotency hardening (manager lock,
   escalation dedup, atomic HITL writes) all landed.

## Deliberately NOT done (by locked decision, not omission)

- Auto-APPLY of L2/L3 patches: requires owner promotion of the class
  (promotion-ladder.md); every proposal card carries auto_apply: false.
- graph_agent migration: starts after this map (ticket 12 sequencing).
- Social's v2 record emission for its full node set, external-pipeline
  records, retention tuning, estate health thresholds: map fog, next efforts.
- Podcast objectives (publish 100%, hopper 2/6) render on the board only
  once F1 adds `setpoints.objectives` to the charter — a human governance
  edit by design.

## Highest true status

Shadow-verified on the feat/loop-factory-v2 branch (committed locally, not
pushed, not merged). Zero external effects at any point.
