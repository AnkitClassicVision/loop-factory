# Factory Pipeline Runbook (F0 → F6)

The repeatable process that turns a described process into a governed,
self-managing department. LLM-directed where judgment is real (interviewing,
map authoring, node drafting), deterministic everywhere else (lint, releases,
manager, kernel). The calibration rule applies to the factory itself: every
new factory mechanism starts hand-executed and earns automation with evidence.

| Phase | What happens | Who | Exit gate |
|---|---|---|---|
| F0 Scaffold | `python3 loopfactory.py scaffold --name <dept> --owner <owner>` | deterministic | skeleton + template charter exist |
| F1 Interview | `python3 loopfactory.py interview --name <dept>`, then an agent runs interview/INTERVIEW.md against the owner; concept map authored from the artifact ONLY | agent + owner | **INTENT LOCK** — owner sign-off with provenance (always human) |
| F2 Govern | Author charter.yaml (from the interview, valid YAML, loader-validated) + procedural-graph.md + subgraphs.json; every node traces to a concept node + interview question | agent, human-reviewed | `loopfactory.py validate --name <dept>` PASS; owner reviews the charter |
| F3 Author | Hand-author runtime nodes per the graph (script vs LLM per the Q8 triage); wire kernel bridge; every node has an executed QA check | agent/worker | node checks pass; kernel negative tests still green |
| F4 Shadow + Pin | Shadow run (simulate sinks, delivered_count==0); `loopfactory.py release pin --name <dept> --source-ref <git sha> --flip` | deterministic | shadow receipts + release pinned + `qa` PASS |
| F5 Operate | systemd timer (from templates/, installed DISABLED, enabled deliberately); manager cycle each wake; estate watchdog watches the manager; heal ladder on failures; human-in-the-loop queue for gated decisions | deterministic | 14 clean shadow days (burn-in) |
| F6 Evolve | Change cards → graph patched → re-lint → affected nodes re-authored → re-shadow → re-pin (see process-change-qa.md). Promotions per promotion-ladder.md | mixed, gated | drift check clean; promotions evidence-based |

## Definition of done for a stood department

1. Interview artifact with INTENT LOCK provenance.
2. Concept map locked; procedural graph + subgraphs.json lint PASS.
3. Charter valid (loader), owner-reviewed, governance-file discipline stated.
4. Runtime nodes authored, each with an executed QA check; traceability PASS.
5. Manager cycle green (STATE/heartbeat/brief written, epoch advancing).
6. Registered in estate/registry.d; estate cycle sees it.
7. Release pinned, `current` flipped, drift check clean.
8. Escalation path proven once end-to-end (a test escalation reached the
   human-in-the-loop outbox).
9. Zero external effects: no send, write, publish, or spend occurred (shadow).

## Hard rules that never relax

- Every department starts in shadow. Real effects are impossible until the
  kernel gateways are wired and the action class is promoted.
- Governance files (charter, autonomy states, promotion thresholds, this
  pipeline) are ALWAYS human — never editable by a department, manager, or heal.
- Deny-by-default: a missing/hollow/stale/forged receipt BLOCKS, never allows.
- No secrets, PHI, or raw message bodies in departments, records, or memory.
- **Receipt-gated steps (owner decision, Ankit 2026-07-23):** EVERY step of
  every process — factory phases F0–F6 included — proves completion with an
  executed artifact, output, or receipt before the next step may run. A step
  without its receipt is NOT done, regardless of what any log or model claims.
  A missing or failed receipt summons the MANAGER: the department manager (or
  estate watchdog above it) detects the gap, drives the heal ladder to remediate
  or escalates to the human-in-the-loop outbox — the pipeline never advances
  past the gap and never silently skips it.
- **Headless-only execution (owner decision, Ankit 2026-07-23):** processes run
  headless under Claude, Codex, or Ringer workers — never dependent on an
  interactive operator session to complete a step. When Ringer executes, ONLY
  OAuth/subscription-plan engine lanes are permitted (Codex OAuth, GLM coding
  plan, Claude subscription); per-token API lanes are forbidden — a lane that
  cannot run on a subscription plan escalates instead of spending.
