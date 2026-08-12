# Loop Brain Reconcile: The Conductor (v2)

Date: 2026-08-05. Owner: Ankit. Status: design approved section-by-section in session; awaiting owner review of this written spec.

## Problem (owner's words, paraphrased)

1. Loops do not always know what to do next. There is no great controller.
2. An end goal (example: 10 discovery calls/week) never compiles into floors for the
   feeder stages (prospecting, outreach, awareness, conversations, close-but-nervous).
   When the target is missed, activity does not increase in the right places.
3. Every process must declare a manifest before running and produce matching artifacts
   after. Manifest entry with no artifact = red flag. Skipped or half-done step = red flag.
4. The clief-notes architecture (AGENTS.md master, CLAUDE.md pointer, router, folder per
   part of the loop, each with its own context) should become the department surface.

## Evidence base

Nine verified Ringer lanes, one artifact page: `loop-brain-reconcile`
(reports under `/mnt/d_drive/ringer-work/loop-brain-reconcile/` and
`/mnt/d_drive/ringer-work/loop-brain-reconcile-redteam/`).

- `lf-controller-audit`: no component owns "what runs next." 17 stall paths. Manager
  decide() only escalates (factory/manager.py:580-596); podcast watchdog forbidden from
  scheduling (departments/podcast/procedural-graph.md:57-71); triage never launches;
  heals shadow-only; boards render.
- `receipts-vs-manifest-audit`: the signed receipt chain (kernel/step_receipts.py) is NOT
  on the production daily path; `podcast_daily.sh:127` carries a literal `|| true` bypass;
  six concrete silent-skip paths; factory/expectation_manifest.py already exists as the
  diff engine but is unwired and existence-only.
- `podcast-funnel-anatomy`: stages F00-F19 real; charter numbers real but hand-entered;
  funnel_floor_sensor implements a one-dimensional 4/8 quota; no cohort event history,
  no end-goal compiler, detection is not execution.
- `ea-architecture-extract`: the transplantable clief-notes pattern in 7 mechanics
  (router, stage contracts, half-life references, output chaining, executable spine,
  corrections loop, draft/decide/execute/receipt separation); it has no answer for
  goals, floors, funnels, ramping, multi-loop coordination.
- `floor-math-design`: cascade and stock-floor math with cold start, hysteresis,
  bottleneck attribution, and a failure-mode/check table. Worked example: 10 discovery
  calls/week -> 15 F17 handoffs -> 58 qualified replies -> 112 invites -> 137
  fit-approvals -> 274 prospect reviews (stated priors, not measured claims).
- Red-team (contrarian, verifier, operator, invariants): 4 packets, all first-try PASS,
  3 CRITICAL catches and ~30 proof slots. All incorporated below.

## Decisions locked by the owner (2026-08-05, this session)

| Decision | Answer |
|---|---|
| Home base | Evolve loop-factory. Factory emits the new surface; governance stays. |
| Floor authority | FULL AUTO + alarm-after, inside the authority split below. Caps, sends, charter values stay kernel/human-gated. |
| Proving ground | Both: podcast retrofit + fresh discovery-call sales department. |
| Red flag behavior | Block the unit + summon manager (extends the 2026-07-23 receipt-gated-steps decision). |
| Approach | A ("The Conductor"), v2 after 4-lane red-team. |

## Architecture v2

Four components. Existing machinery unchanged: kernel gateways, signed step receipts
(finally placed on the production path), releases/drift, promotion ladder, estate
watchdog, heal ladder, always-human charter floor.

### C1. Compiled clief-notes department surface

The pinned procedural graph is the single topology source. The factory GENERATES from it:
`ROUTER.md` (task -> exactly one stage folder; unknown route stops), numbered stage
folders `NN_<stage>/` with `CONTEXT.md` skeletons (Inputs labeled L3/L4, Process,
Outputs, Verify), and run-manifest templates. Humans write prose (Process detail,
references content), never edges. `AGENTS.md` is the master control file; `CLAUDE.md` is
a three-line pointer. `references/` files carry Last-updated + half-life; expiry fails CI.
A pairwise drift check across graph source, generated surface, and subgraphs blocks
promotion on any mismatch. (Answers: third-topology drift catch.)

Per-department layout:

```
departments/<dept>/
  AGENTS.md   CLAUDE.md   ROUTER.md   floors.yaml (derived snapshot, append-only)
  01_<stage>/ ... NN_<stage>/   each: CONTEXT.md  references/  output/
  state/events.jsonl            kernel-stamped cohort transition ledger
```

### C2. Run manifests + blocking reconciliation

- Minting: kernel-owned service, from the release-pinned template hash; node roster
  hash-derived from the pinned graph (a hand-trimmed roster is REJECTED: forgery by
  omission is the attack). Atomic create-no-replace; department workers have no write
  path to the manifest directory. (Answers: manifest custody + omission catches.)
- Batch runs get one immutable manifest; event-driven units (webhook reply, human call)
  get per-unit manifests minted at admission. No amendment semantics needed. (Answers:
  event-arrival catch.)
- Every node emits a run-bound produced record; emission failure makes the node FAILED
  regardless of exit code.
- Reconciliation: a kernel-side verifier diffs plan vs produced records and emits a
  SIGNED verdict consumed by the manager between Sense and Compare. Missing or errored
  verdict = REFUSE, never an inferred empty diff. (Answers: reconciler monoculture.)
- On red verdict: approvals, heals, rotation BLOCK; a red diagnostic board still
  publishes from quarantined data ("block acts, never eyes"); escalation card carries a
  recheck deadline. Estate deadman spot-recomputes random samples from raw records.
- `podcast_daily.sh:127` `|| true` is removed in P0.

### C3. Goal/floor compiler

Authority split (answers: governance relocation, floor laundering):

- charter.yaml (humans only, forever): end_goals, transition schema + priors, buffers,
  deadband, caps, actuator mappings, freeze conditions.
- floors.yaml (machine, append-only versions): derived floor values + attainment +
  provenance hash. Heals may NEVER touch it. It is a derived snapshot, not policy.
- A floor change can never change an action class, promotion state, or lease authority.
  Kernel action-class registry enforces this independently of floor eligibility.
- A compiler change that alters the transition set, priorities, or actuator mapping is a
  PROCESS CHANGE: map QA + shadow + re-pin required.

Math (weekly tick):

```
flow_floor_i  = ceil(flow_floor_downstream / rate_i * (1 + buffer_i))
stock_floor_i = ceil(flow_i * lead_days_i / 7 * (1 + s_i))        # Little's Law
rates: matured 4-week cohorts only (>=30 entrants, >=10 conversions),
       blend 0.75 old + 0.25 new; maturity uses elapsed-time rules robust to clock jumps
deadband 90-105% attainment (percent-of-floor, boundaries inclusive at 90, exclusive 105);
       two consecutive reviews to enter/exit; max +/-20%/week
FREEZE increases when: state_drift != 0 | unledgered_inbound != 0 | cohort lag |
       reconciliation verdict red   (circuit breaker vs runaway ramp)
CAP CONFLICT: latch; zero dispatch on the conflicted transition; one deduplicated owner
       card; clears only on a signed new cap/floor version
```

Bottleneck attribution on a miss: scan outcome-upstream; flag the FIRST stage with
sufficient input, matured conversion < 90% twice, unblocked downstream. Raise activity
only there.

### C4. Conductor

Deterministic `factory/conductor.py` (no LLM in control flow). Each tick:

1. Acquire the exclusive department driver lease (one claimant per transition, dispatch
   receipt required; a second driver's claim is refused loudly).
2. Reconcile: verdict green required to advance past any unit.
3. Pick highest-priority eligible transition: unblock > floor-gap > routine, WITH aging
   so routine work has bounded wait. Deterministic tie-break.
4. Reserve budget atomically, dispatch through a kernel gateway to a headless
   subscription worker (Codex OAuth / Claude subscription / GLM plan; per-token API
   forbidden), verify the receipt, advance.
5. Stall: heal ladder in order; exhaustion = one deduplicated human card with recheck
   deadline.

Supervision: systemd restart + extended estate deadman covering the conductor itself.
Budget telemetry is WIRED (the current 900-call ceiling loads with no usage feed and
reads missing data as zero; that false guard is fixed in P0).

## Two-department wiring

The discovery-call goal lives in the SALES department (podcast cannot observe held
calls; S01 is a deliberate boundary). Sales charter: `discovery_calls_per_week: 10`,
stages prospecting -> outreach -> conversations -> close-nervous -> discovery call, with
podcast_handoffs as one explicitly priced input lane. A kernel-registered
cross-department contract sets podcast's `handoffs_supplied_per_week` objective from the
sales cascade; changes to that number escalate to the owner. Sales' measured rates move
only on attributed, deduplicated, matured outcome receipts.

## Human interface

Cards speak owner language (WHAT THIS MEANS / WHAT IT NEEDS, house style). Recheck
deadlines on every card; dependent work self-parks; unrelated safe work continues; one
daily digest; one card per fingerprint; silence never becomes approval.

## Error handling (deny-by-default, complete table)

| Failure | Behavior |
|---|---|
| Gateway error | REFUSE (LockServiceDown) |
| Verifier verdict missing/errored | REFUSE; empty diff never inferred |
| Produced-record emit fails | Node FAILED even on exit 0 |
| Budget | Reserve-before-dispatch; 80% review card; ceiling hard block |
| Conductor crash / disk full | Restart + deadman; degraded control page; no manifest advance |
| Ignored card | Self-park + recheck deadline + daily digest; never auto-approve |
| Cap-floor conflict | Latched; zero dispatch; signed version to clear |

## Verification (acceptance gates; every check watched RED first)

Adopted from the red-team packets (full pass/fail wording in
`/mnt/d_drive/ringer-work/loop-brain-reconcile-redteam/*/packet.json`):

1. Manifest adversarial suite: forged signer, replayed nonce, zero-node manifest,
   duplicate ordinal, wildcard skip, worker manifest-write attempt, omitted node (shell
   AND manifest), hollow artifact, wrong-run binding: each must BLOCK all downstream acts.
2. Scheduler ownership trace: shell timer + pipeline tick + manager + conductor enabled;
   exactly one claimant and one signed receipt per transition; duplicate claim kills.
3. Topology drift CI: pairwise normalized diffs across graph source, ROUTER.md, stage
   CONTEXT files, subgraphs; any mismatch blocks promotion.
4. Floor goldens + fault injection: known cohort ledgers reproduce exact cascades;
   immature cohorts (29/9) change nothing; whipsaw + restart honors deadband, hysteresis
   persistence, 20% cap; poisoned ledger (duplicates, backdating, illegal transitions)
   yields UNKNOWN/FROZEN, never a rise.
5. Conductor properties: never dispatches without a current-run manifest entry; never
   exceeds caps across retries/crashes (idempotency proven); priority order with
   starvation bounds; stall runs heal ladder then exactly one card.
6. Floor-laundering shadow proof: every floor raised above every cap -> zero external
   actions, zero new leases, one latched escalation, promotion state byte-identical.
7. Privacy canary: seeded PII in event fixtures blocks record append and board publish.
8. Operator drills: budget exhaustion starting at 719/900; 7-day no-human simulation
   (one card per decision, safe work continues, no inferred approval); kill conductor +
   fill disk (recovery or visible degraded page, no advance).
9. Podcast A/B shadow: existing path vs retrofit on identical fixtures for 4 weeks;
   compare missed transitions, duplicate actions, operator minutes, receipt coverage.

## Rollout

| Phase | Lands | Rollback |
|---|---|---|
| P0 | Close silent skips (remove `\|\| true`, fatal emits) + wire budget telemetry | git revert |
| P1 | Kernel manifest custody + verifier; verdict advisory 1 week, then blocking | flip advisory |
| P2 | Compiled folder surface (podcast) + drift CI | delete generated files |
| P3 | Cohort ledger + compiler shadow (alarm-only); sales dept F1 interview -> INTENT LOCK -> charter | compiler off |
| P4 | Conductor shadow beside shell; lease-arbitration proven; cutover | re-enable shell timer |
| P5 | Floor-gap dispatch live; full-auto floors on | charter freeze switch |

Owner-required moments: P3 sales interview + intent lock signature; every promotion on
the existing ladder; any cap/charter change.

## Invariants preserved (unchanged, non-negotiable)

Shadow-first; deny-by-default; always-human governance floor; process change = map
change + QA + re-pin; records always; no secrets/PHI in department code, records, cards,
or memory; receipt-gated steps; headless subscription-only engines.

## Out of scope (YAGNI)

Multi-box estate; cross-department resource arbitration beyond the sales-podcast
contract; LLM-driven scheduling of any kind; automatic promotion; touching the EA repo.
