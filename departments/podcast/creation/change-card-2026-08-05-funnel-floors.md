# Change card — funnel floors + drive quotas (2026-08-05)

**What changes:** charter.yaml gains six funnel-floor objectives
(active_warm_threads >= 10, live_replies >= 4, prep_done_awaiting_recording >= 3,
recordings_booked_future >= 2, stale_touches = 0, expired_holds_unactioned = 0)
and a thresholds.funnel_drive quota block (steady 4 new outreaches/week,
rebuild 8/week while hopper_depth < 6, touch cadence <= 7 days with the
existing 4-day floor, expired holds actioned same day, detection latency 26h).
A new runtime drive-check sensor (built separately via the governed worker
path) measures these daily from FUNNEL-LEDGER.json evidence; any miss becomes
a plain-language mailroom card.

**Why:** owner finding 2026-08-05: "all my loops... they don't drive." The
6-in-hopper goal had no stage-level flow requirements behind it; nothing was
accountable for daily movement. Worked backwards: 1 publish/week, ~2-day
production lag, warm-lane conversion guesses (60%/70%/60%) => ~4 outreaches
per recording. Guesses are marked; the drive check measures real conversion
within 2 weeks and quotas get corrected from data.

**Owner sign-off (verbatim):** "lets atart with those recimede floor and
deicing to that bia loo" — Ankit, 2026-08-05, approving the recommended
floors table and driving to them via the loop system, following the full
floors table presented in-session.

**Governance note:** charter edited by the coordinator applying the owner's
explicit in-turn decision; no worker touched this file. Intent (mission, kill
conditions, human gates) unchanged — this adds gauges and quotas only.

**QA path:** validate --name podcast PASS -> drive sensor lands via worker
lane with tests -> re-shadow -> release pin --flip -> qa drift-clean.
