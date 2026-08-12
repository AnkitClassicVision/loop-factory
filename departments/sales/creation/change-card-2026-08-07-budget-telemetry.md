# Change card — budget telemetry producer (2026-08-07)

**What changes:** the sales daily chain gains a deterministic factory-level
producer, `factory/budget_telemetry.py`, invoked in `runtime/sales_daily.sh`
immediately before the manager tick. It aggregates the department's own run
ledger (`state/runs-v2.jsonl`, schema run-record/v2) over the manager's rolling
7-day window and writes `state/budget_used.json` — the file the manager has
been reading via `--budget` since the chain was wired, which nothing produced.
Keys mirror the charter's `budget.weekly_ceilings`: `model_calls` summed from
`cost.model_calls`, `worker_minutes` summed from `duration_ms`, `dollars`
from priced evidence only (a kernel `BudgetBroker` ledger at
`state/kernel/budget.jsonl`, merged per-kind with max() when present). No new
department node: the producer is manager-side sensing infrastructure, like the
manager itself — it appears in the daily trigger script, not in the roster or
`subgraphs.json`. The prose map's SENSE loop line gains one clause.

**Why:** the charter sets weekly ceilings (450 model calls, $0, 840 worker
minutes) and the manager fails closed on them: with ceilings set and no
telemetry file it raises the breach `budget_telemetry_missing` on every tick
("spend is unverifiable — wire the producer or fix the path",
factory/manager.py:520). This is the standing finding named in the 2026-08-06
handoff, and it blocks the scheduler-cadence gate: a timer must not go live
while budget enforcement is blind.

**Fail-closed contract (the design's core):** missing run ledger, an
unparseable line, a record from the wrong department, metered-lane model calls
(unpriceable spend), or an unreadable broker ledger each abort the producer,
which then DELETES any previous `budget_used.json` before exiting nonzero —
a stale file must never let yesterday's numbers pass as today's evidence. The
absence of the file keeps the manager's `budget_telemetry_missing` breach
firing, which is the correct alarm state. The chain does not hard-stop on
producer failure: the manager tick is the enforcement surface and must run to
raise the breach. Success writes atomically (tmp + rename) with provenance
(`window_start`, `generated_at`, `records`); the manager consults only the
ceiling keys.

**Intent unchanged:** no ceiling moves (budget_ceilings is an immutable
safety invariant); no autonomy changes; the producer reads department records
and writes one derived state file — zero external actions. Q9's
subscription-only posture is enforced harder, not softer: metered-lane usage
now aborts telemetry instead of hiding in an absent file.

**QA path (process-change-qa.md):** change card + prose map patched first →
`loopfactory.py validate --name sales` PASS → producer + tests authored →
`loopfactory.py check` PASS → re-shadow the daily chain on a live copy
(budget_used.json produced, budget_telemetry_missing absent from findings,
delivered_count==0, external_actions_taken==0) → `release pin --flip` →
`loopfactory.py qa --name sales` drift-clean.

**Owner decision trail:** Ankit, 2026-08-07, current-turn selection "Both,
telemetry then cadence" against the 2026-08-06 held-confirm handoff's ONE next
pickup action (budget telemetry producer vs scheduler cadence). Ceilings:
charter Q9 ("tighter than podcast", locked at readback). Scheduler cadence is
the NEXT gate this session, proposal-only until the owner approves.
