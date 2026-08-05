# Change card — hopper-unknown classification (2026-08-05)

**What changes:** `runtime/compare_charter.py` gains the missing
`("hopper", "unknown") -> ("hopper_blind", "high")` transition and a `hopper`
QUESTIONS entry. New `tests/test_compare_charter.py` adds a regression test, a
negative-case guard test, and a parametrized completeness test over every
declared FAILURE_CLASSES pair.

**Why:** since 2026-08-03T03:00Z every 30-minute chain run crashed at
compare_charter (ValueError: no transition for sensor='hopper',
status='unknown'), aborting before the stage-2 manager cycle — a dead manager
for 2+ days while sensing kept running. Second occurrence of the defect class
first seen 2026-07-31 with ("ledger", "unknown"); per charter, a repeat of a
resolved fingerprint class is a department defect, hence the completeness test.

**Intent unchanged:** deny-by-default stands — a truly unmapped (sensor,
status) pair still raises. The WHY of the process did not change; no
procedural-map edit required (classification table is runtime detail; lint and
traceability re-verified below).

**QA path (process-change-qa.md):** patch → `loopfactory.py validate --name
podcast` PASS → re-shadow (one live shadow cycle; proof = `last_cycle_at`
advances past 2026-08-03T02:30:01Z and a heartbeat with epoch > 1587) →
`release pin --flip` → `loopfactory.py qa --name podcast` drift-clean.

**Owner decision trail:** Ankit, 2026-08-05, "yes fix all of them" (round-2 GO,
Claude Code session; Ringer run everything-in-mailroom, task
fix-hopper-transition).
