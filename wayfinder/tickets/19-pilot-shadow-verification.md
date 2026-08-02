---
title: Podcast v2 shadow verification + re-pin (map exit)
status: closed
type: task
assignee: coordinator-fable
blocked_by: [16, 17, 18, 20]
---

## Question

Run the full v2 verification on the upgraded podcast department and close the
map: forced-failure drills in shadow (engine timeout, OAuth expiry block,
trigger replay, evaluator rejection, worker kill, rebuild-run-view-from-
receipts), `loopfactory.py check` + `validate` + `qa` all PASS, release
re-pinned, drift clean, board showing the truth, zero external effects.
Deliverable: the v2 verification report with receipts — this closing is the
map's Destination test. Remaining fog graduates into a fresh effort
(social migration, revenue department, AgentSpace choice).

## Resolution

MAP EXIT REACHED (2026-08-02). All gates and drills executed and green —
full detail in `wayfinder/19-verification-report.md`: CHECK PASS, both
departments validate ok and re-pinned from HEAD with zero drift mismatches,
six live forced-failure drills passed (trigger replay visible+harmless,
auth block -> AUTH andon, metered violation -> POLICY andon excluded from
stats, record gap fails closed, feed rebuild byte-identical, 79/79 records
with external_actions_taken=0), and today's real daily chain proved the
self-managing cycle end to end (11 nodes recording, heal lane proposing and
honestly failing verify in shadow, 11,210 rows rotated, boards
auto-regenerated). Remaining fog graduates to fresh efforts per the map.
Highest true status: shadow-verified on feat/loop-factory-v2, local branch,
not pushed, zero external effects.

