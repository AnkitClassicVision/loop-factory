# podcast Department — Procedural Graph (F2 output, the WHAT)

Status: DRAFT (F2). Hand-authored from the locked concept map. The authoritative
machine form is `subgraphs.json` (validated by `factory/graphs.py`); these
tables are the human-readable companion. If the two disagree, one is wrong —
fix both, re-lint, re-pin.

Traces to: `knowledge/concept-map.md` (LOCKED 2026-07-22) + `charter.yaml`.
Every node: declared inputs → output contract → EXECUTED QA check → receipt to
runs. Scripts get NO QA exemption (C14: this department is script-only in V1 —
no model-capable nodes; LLM work stays in the estate's loops).

This department is a WATCHDOG (C2): all six lanes are read-only sensing except
SG-HEAL, which mutates only via known playbooks and is propose-only in shadow.
It never dispatches externally; its outbound is escalation cards to the
human-in-the-loop outbox (delivered via the kernel escalation seam once wired).

## Shared Safety Layer usage

Send guards S4/S5/S6/S7 are not_applicable in every lane (read-only funnels, no
external dispatch — the estate's own dispatch gateway owns sends). S1 identity
resolution applies where person entities are joined (SG-PIPELINE, SG-MANIFEST).
S2/S3/S8 are not_applicable per-lane with recorded rationales (no contact
targeting; no model calls; no cost-incurring nodes under subscription-only C8).

## SG-WATCHDOG — estate health sensing (proving slice, C3)

```
[T daily] → N1 sense_estate → pipeline/publish/manifest/hopper sensors
          → DAG supervision → N2 compare_charter → N3 fingerprint_dedup
          → N4 escalate_outbox (shadow: local outbox only)
```

| # | Node | type | impl | action_class / autonomy | QA check (executed) | traces |
|---|---|---|---|---|---|---|
| N1 | sense_estate | Sense | SCRIPT | internal_read / shadow | output lists EVERY unit in the charter estate inventory; a unit missing from output = check FAILS (silent-gap guard) | C3, C16, Q3 |
| N2 | compare_charter | Score | SCRIPT | internal_read / shadow | every incident cites setpoint + raw evidence path; classification is enumerable (state machine, C14) | C4, Q4 |
| N3 | fingerprint_dedup | Transform | SCRIPT | internal_read / shadow | same fingerprint twice in open state = ONE thread (dedup test); resolved fingerprint recurring = flagged department_defect | C12 |
| N4 | escalate_outbox | Act(internal) | SCRIPT | escalation / shadow | card contains the ONE question + evidence + fingerprint; shadow asserts delivered_count==0 externally | C12, C13, Q11 |
| N6 | comms_reconcile_sensor | Sense | SCRIPT | internal_read / shadow | available reconciliation sensor, but not invoked by the current daily orchestrator | C3, Q3 |
| N9 | record | Record | SCRIPT | internal_write / shadow | legacy standalone recorder; not invoked by the current daily orchestrator because invoked sensors emit their own records | C18, Q15 |

Sensor families inside N1 (C3): (a) systemd timer/unit state + receipt freshness
+ log error patterns for the 7 loops + support lanes, (b) escalation-channel
liveness (can Telegram/Linear actually deliver? — C16, the 2026-07-22 lesson),
(c) VPS reachability + service state (read-only SSH). N6 adds deterministic
open-loop communication reconciliation: it reads the referral tracker report
and referral ledger, applies the upstream-nonzero/downstream-zero-beyond-SLA
invariant, and emits findings only; it never sends anything.

## SG-DAG-SUPERVISION — pipeline projection integrity (C1/C2/C16/C19)

```
[pipeline-owned dag-projection-v1 receipt] → N1 dag_supervisor
  → local observation + critical incident candidates → existing watchdog dedup/outbox
```

| # | Node | impl | QA check (executed) | traces |
|---|---|---|---|---|
| N1 | dag_supervisor | SCRIPT | recomputes canonical DAG and skip-artifact hashes, fails closed on stale/missing/unreadable projections, and alarms on silent skips; source inspection confirms it never schedules pipeline steps or writes pipeline state | C1, C2, C11, C16, C19, Q1, Q12/Q13 |

The pipeline repository remains the single execution authority and only writer
of episode state. This lane only reads the exported projection and writes local
supervisory observations/candidates; scheduling or choosing a next step here is
a split-brain defect.

## SG-PIPELINE — independent guest-count sensor (C1/C4)

```
[shared daily chain] → S1 → N1 pipeline_sensor → other sensors
                     → one shared N2 compare_charter
```

| # | Node | impl | QA check (executed) | traces |
|---|---|---|---|---|
| N1 | pipeline_sensor | SCRIPT | count derives from calendar+HubSpot join, NEVER from loop self-reports (independence assertion: source fields logged per counted guest) | C4, C15, Q4 |

## SG-PUBLISHDAY — publish-day verification (C1/C10)

```
[shared daily chain] → N1 publish_verifier → later sensors → one shared N2 compare_charter
```

| # | Node | impl | QA check (executed) | traces |
|---|---|---|---|---|
| N1 | publish_verifier | SCRIPT | public receipts checked at the PROVIDER (RSS/YouTube/social liveness), not internal state; missing receipt = incident, never a warning | C10, C16, Q12 |

## SG-MANIFEST — guest manifest completeness sensor (C6/C7)

```
[shared daily chain] → S1 → N1 manifest_sensor → later sensors → one shared N2 compare_charter
```

| # | Node | impl | QA check (executed) | traces |
|---|---|---|---|---|
| N1 | manifest_sensor | SCRIPT | per-guest completeness computed from HubSpot(read) + episode state; every gap lists the missing field + chase status + fallback available yes/no | C6, C7, C10 |

HubSpot WRITES of manifest status are ESTATE-side (charter estate_decisions);
this lane is read-only. Unresolved guest identity = suppress + review (C15).

## SG-HEAL — known-pattern auto-heal (C5/C11/C12)

```
[incident from SG-WATCHDOG] → N1 heal_select → N2 heal_apply (shadow: propose-only)
                            → N3 heal_verify → N9 record
```

| # | Node | impl | QA check (executed) | traces |
|---|---|---|---|---|
| N1 | heal_select | SCRIPT | fingerprint matches a versioned playbook entry or lane HALTS and escalates (unknown = never improvise, Q11) | C5, C11, C14 |
| N2 | heal_apply | SCRIPT | playbook allowlist only (restart unit, recreate tracker, re-run step); anything touching an immutable invariant = refuse; shadow = propose-only receipt | C5, C11 |
| N3 | heal_verify | SCRIPT | re-sense proves the incident cleared; heal without verified clearance = FAILED heal → escalate; recurrence feeds C12 root-cause item | C12, Q13 |

## Autonomy rollout

Department starts shadow (charter). Ladder per runbooks/promotion-ladder.md:
shadow → heals live (playbook allowlist) → escalation delivery live. The
department has NO send/publish/CRM-write class to promote — those live in the
estate. No lane auto-promotes.

## Daily maintenance

After all daily consumers and the heal phase finish, the orchestrator runs
`rotate_observations.py` to bound retained observation evidence, then rebuilds
the operator boards. Rotation is therefore part of every successful daily run.

## Intent traceability

Every node lists concept-map refs above. Does-less/does-more check: no
capability beyond the locked interview — specifically, no outreach drafting, no
publishing, no CRM writes, no VPS mutation beyond the heal playbook allowlist.
