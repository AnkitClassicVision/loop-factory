# Ultimate Department Creation Runbook (Stages 1 → 12)

> **STATUS: RATIFIED — owner sign-off Ankit, 2026-08-03.** Runbooks are
> governance files: humans only, forever. Compiled by the coordinator from
> the executed process-diff of two real creation sessions (`wayfinder/map.md`
> v2 pilot, completed; the crashed sales revamp, `wayfinder/MAP.md`) and the
> r1 synthesis report; ratified verbatim by the owner. This is now the
> canonical orchestration layer; `factory-pipeline.md` is the F0–F6
> reference this runbook orchestrates.

The ONE canonical process that creates an automation or department from
scratch: **Wayfinder is the decision system, F0–F6 is the department
factory, and a versioned Creation Contract is the seam between them.**
The coordinator-brain carries state and dispatches; the owner supplies
judgment and approvals; Ringer workers implement bounded units. A stage
cannot advance without its named receipt (receipt-gated rule, owner
decision 2026-07-23).

## Why this runbook exists

Two sessions used variants of Wayfinder + Factory. The completed one proved
execution: prototypes for owner taste, Ringer builds with executed checks, a
throwaway-department inheritance proof, an adversarial verification report.
The crashed one proved discovery: a live-system gap audit, a full F1
interview with INTENT LOCK, isolated multi-worker dispatch — and exposed the
failure this process must prevent: **file-disjoint parallel work still
created three semantically competing spines** (run stores, projections, heal
authorities). The canonical process keeps both halves and closes that gap
with a semantic-authority gate before any fan-out.

## The twelve stages

| # | Stage | Actor | Artifact | Executed exit gate |
|---|---|---|---|---|
| 1 | Qualify the idea, lock the destination | owner + coordinator | Wayfinder Destination: business outcome, named proof department, scope + out-of-scope, ONE binary map-exit test | Owner signs; exit test is observable. No fog → no map; Wayfinder stops rather than manufacturing one |
| 2 | Chart the decision route | coordinator; owner joins HITL tickets only | One map, typed tickets (research/grilling/prototype/task), blocking graph, fog register | Frontier query returns only typed claimed-or-available tickets; research tickets carry evidence packets |
| 3 | Close decisions, compile the **Creation Contract** `[BUILD GAP 1]` | owner + coordinator | `department-creation-contract.yaml`: destination, proof loop, canonical authority per concern, answered F1 questions, factory version target, privacy/external-effect boundary, proof matrix, decision-ticket sources | `loopfactory.py creation-contract check` fails on open build-blocking decisions, duplicate authorities, missing sources, untestable destination |
| 4 | Instantiate the v2-native shell (F0) | deterministic scaffold | Department dir, charter template, eval registry, runtime-node template, engine routes, daily trigger, estate registration | Scaffold receipt exists; second run refuses to overwrite the governance charter |
| 5 | Owner interview + INTENT LOCK (F1) | owner + coordinator | Verbatim interview, concept map, source-of-truth decisions, proving slice, objectives/setpoints, NEVER list, budgets, kill/pause rules, action-class gates, escalation owners | Every question-bank section answered or skip-rationale recorded; readback folded; owner signs INTENT LOCK — no agent self-certifies |
| 6 | Author + validate the governed operating model (F2) | coordinator drafts; owner reviews governance | Final charter.yaml, concept map, procedural graph, typed subgraphs, eval policy, F1/Wayfinder crosswalk | `loopfactory.py validate --name <dept>` PASS; every node traces to an intent/decision source; conflicts fail closed |
| 7 | Bind objectives to independent sensors `[BUILD GAP 2]` | owner picks measures; Ringer implements adapters | Per-objective contract: label, unit, minimum, target, source, observation path, freshness window, independent owner, unknown behavior, breach code → `objectives-observed/v1` | `loopfactory.py objectives verify --name <dept>` runs fixtures: observed, unknown, malformed, stale, at-minimum, below-minimum; board/andon rows match charter |
| 8 | Compile cadence + alert contracts `[BUILD GAP 3]` | owner approves wake/ping policy; coordinator renders | Trigger spec (time/goal/event), timer/path units, concurrency + catch-up policy, outbox sources, classification table, dedupe/cursor rules, digest cap/cooldown, proposal-only repair classes, escalation SLA | `loopfactory.py cadence check` + triage installer `--dry-run`: exact files, no conflicts, no `systemctl` call, first-run EOF, one bounded digest, proposal-only repairs. Owner reviews units before activation |
| 9 | Generate + execute bounded build units (F3) | coordinator dispatches; Ringer workers build | Ringer manifest generated from validated graph nodes: owned files, I/O schema, expected receipt, test command, and the ONE canonical authority each task may change | Every manifest check executes green; fresh-context review of judgment-heavy output; integration waits for all receipts. No worker merges or promotes |
| 10 | Assemble the department control plane | coordinator integrates; Ringer fixes bounded reds only | One release candidate: signed run records, shared run identity, eval evidence, manager/heartbeat, estate registry, board feed, heal ladder, human outbox, triage proposal lane | `check` + `validate` + `qa` all PASS post-integration; shadow node emits a valid record; injected incident reaches the correct lane; external effects zero |
| 11 | One post-integration proof, then pin `[BUILD GAP 4]` | coordinator owns verdict | Versioned verification report + proof bundle tied to one release candidate | `loopfactory.py prove --name <dept>`: duplicate trigger, auth block, record-write failure, worker kill, evaluator rejection, objective breach, escalation delivery, receipt rebuild, board truth, drift, zero-external-effects. Only a wholly green RERUN permits pin + flip |
| 12 | Activate, burn in, self-heal, evolve (F5–F6) | owner approves activation/promotion | Running triggers, advancing heartbeat, live board, triage audit, heal proposals, 14-day burn-in record, promotion packets, change cards | Owner enables reviewed units; 14 clean shadow days measured; promotions owner-signed; changes go map-patch → re-lint → re-author → re-shadow → re-pin, drift-clean |

## Canonical operating rules

1. **One creation map → one Creation Contract → one proof department.** New
   departments or post-destination migrations start fresh maps.
2. **Wayfinder tickets are named decisions until Stage 3. Ringer units are
   named implementation/proof tasks after Stage 3.** Never reuse one ticket
   type for both meanings — that ambiguity produced both sessions' drift.
3. **The coordinator-brain is the only planner/integrator.** Ringer workers
   own bounded edits and checks. The owner alone locks intent, governance,
   activation, and promotion.
4. **Semantic-authority gate before fan-out.** The contract names ONE
   authority per concern — transition, run identity, telemetry, eval policy,
   eval evidence, board projection, healing, escalation — and no new store,
   projection, evaluator, heal path, or alert route lands without declaring
   whether it replaces, derives from, or feeds the existing authority. File
   isolation alone does not prevent semantic collision; the crashed session
   proved it.
5. **Every stage emits a receipt; a missing receipt blocks the next stage**
   and summons the manager (receipt-gated rule, factory-pipeline.md).
6. **Every outbound ask declares a return path.** Any node with
   `emits_ask: true` must declare `return_path` and `return_sla_hours`; the
   validator enforces it and the interview asks it at every department's
   birth (the 57-asked → 16-replied → 0-harvested lesson).

## The four build gaps (code deliverables this runbook references)

| Gap | Deliverable | Stage it gates |
|---|---|---|
| 1 | Creation Contract schema + compiler + `creation-contract check` | 3 |
| 2 | Objective sensor contract, adapter stub, `objectives verify` | 7 |
| 3 | Unified cadence/alert compiler binding scaffold, triage installer, F5 activation | 8 |
| 4 | Post-integration proof runner + verification-report emitter (`prove`) | 11 |

Until a gap ships, its stage runs hand-executed against the same gate
definition — the calibration rule: every factory mechanism starts
hand-executed and earns automation with evidence.

## Acceptance test for this runbook

Create a throwaway department from a one-paragraph idea; reach Stage 11 with
a green proof bundle; activate it in shadow; observe it on the board; inject
one fixable and one human-only incident; verify proposal/digest routing;
delete it. The runbook is done only when ONE uninterrupted run proves all
twelve stages.

## Hard rules inherited unchanged

Shadow-first, deny-by-default, always-human governance floor, records
always, no secrets/PHI/raw bodies, receipt-gated steps, headless-only +
subscription-only engines — see `factory-pipeline.md` "Hard rules that never
relax". This runbook never lowers any of them.
