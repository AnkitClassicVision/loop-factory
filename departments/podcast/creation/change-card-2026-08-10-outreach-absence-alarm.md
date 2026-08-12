# Change card — outreach absence alarm (U12) (2026-08-10)

**What changes:** SG-WATCHDOG gains one Sense node, `outreach_absence_sensor`,
implemented at `runtime/outreach_absence_sensor.py` and added to the daily run
roster. It reads two pieces of evidence from the podcast pipeline repo, the
guest producer's ledger of drafts it has created and the candidate feeder's
per-day drop accounting, and reports one of four states over a three-day
window:

- `ok` drafts were created in the window
- `alarm` zero drafts while eligible candidates existed, so the machinery died
- `drought` zero drafts and nobody was emailable, so the problem is supply
- `unknown` a day's feeder report is missing, so the gauge is blind

The node's `untraced_allowed` entry is removed, because it is now a real node.
No other node changes. No charter, threshold, autonomy state or human gate is
touched.

**Why:** the guest-acquisition loop could run every day, exit 0 every day, and
create nothing, and that is indistinguishable from a quiet week. Every other
unit in the loop-drive contract makes a specific failure loud; absence is the
one failure that cannot announce itself.

The sizing question the finish-line spec left open was whether an existing
sensor already covers this. Measured 2026-08-10, it does not:
`factory/estate_deadman.py` tracks epoch, findings, escalations and staleness,
which is LIVENESS, and a loop that runs faithfully and drafts nothing is
perfectly alive. `funnel_floor_sensor` measures funnel STATE, which cannot
separate "the machinery stopped" from "the pool is empty" — and the pool is
nearly empty right now (13 of 14 live inbox records carry no email address), so
a dead loop would hide behind the drought. Hence a new node, whose
discriminating input is the PAIR (drafts created, eligible candidates), which
only became measurable when the feeder began writing drop accounting earlier
the same day.

`unknown` is never folded into `ok`, following the owner's 2026-08-05 amendment
that a missing source makes a gauge blind and a blind gauge is not a passing
gauge. That rule matters most here: an absence alarm that reads missing
evidence as "nothing to report" switches itself off exactly when the thing it
watches has broken.

**Owner sign-off (verbatim):** "yes and yes i want thebalarm" — Ankit,
2026-08-10, approving both the first live Gmail draft and wiring this alarm,
after being shown in plain language that turning it on means adding it to the
jobs that already run every hour and that this is a sign-here change.

**Governance note:** coordinator applied the owner's explicit in-turn decision.
Adds a gauge only. Intent, mission, kill conditions, human gates, thresholds
and autonomy state are unchanged; the department stays in `shadow`.

**QA path:** maps patched (procedural-graph.md + subgraphs.json + run-roster)
-> validate --name podcast PASS -> node re-shadowed with executed checks and
delivered_count 0 -> release pin --flip -> qa drift-clean.

**Executed proof already on file:** `u12_absence_alarm_check` drives all five
states through the real sensor and PASSES, and it was watched FAILING first —
mutating the sensor to treat missing evidence as `ok` turns it red on both
blind cases and green again on restore.
