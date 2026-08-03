# Department audit — verdict (2026-08-03)

Population: the two registered departments (podcast, social), measured against
the ratified `runbooks/ultimate-department-creation.md` standard by six Ringer
lanes: read-only live gates, sandboxed prove drills, and read-only Codex
reviews with invented-evidence detection. Full reports in `reports/`.

## Scoreboard

| dimension | podcast | social |
|---|---|---|
| validate (charter+maps) | PASS | PASS |
| prove — 7 real drills (sandbox) | PASS | PASS |
| objectives gate | RED — hopper_depth BELOW MIN; 4/6 unmeasured | RED — no objectives contracts exist at all |
| release qa | RED — drift vs pin 50b79dc4 (comms landing not re-pinned) | RED — drift vs pin 6f688bb2 (draft_post.py, social_daily.sh) |
| v2 run records | P1 — mixed legacy/v2 | P1 — fully legacy (record.py:106-160) |
| ask classes / return paths | P1 — escalation asks: no declared return path | P1 — review-card asks absent from graph, `\|\| true` on creation, readers never triggered |
| fail-closed gating | P1 — podcast_daily.sh:52-70 suppresses heal failures with `\|\| true` | P1 — kernel lock_service.py:58-70 silently drops corrupt nonce rows |
| receipt-gated steps | P1 — shell exit codes, not receipts | P2 — cap-yield exit + review card escape the chain |
| cadence contract (Stage 8) | P1 — none exists | P1 — none exists |
| map-runtime honesty | P1 — 3/3 spot checks mismatched | (publish guard chain executes in order — clean) |
| manager liveness | alive | P1 ROOT-CAUSED — timer units deliberately never installed; registry schedule TODO_F1; owner activation was never done |

## What is genuinely sound

Both departments pass every real proof drill: trigger dedupe, fail-closed
record writes, objective-breach surfacing, escalation delivery, receipt
rebuild, zero external effects. Social's publish guard chain (S1,S2,S3,S5,S4
+ S6/S7 health + pre-gateway recheck) executes in the required order — the
highest-blast-radius path is properly guarded.

## Proposed fix swarm (review-before-fix: nothing below is started)

- **Wave 1 — deny-by-default violations (no owner decisions needed):**
  podcast heal `|| true` suppression made fail-closed; social terminal-yield
  and review-card stages brought into the receipt chain; kernel nonce-ledger
  strict on non-final corrupt rows (shared kernel — extra test burden);
  podcast map-runtime reconciliation (graph = execution).
- **Wave 2 — standard adoption (pattern proven by the sales retrofit):**
  v2 run records in both departments; every ask class modeled as an
  `emits_ask` node with an executed return path; cadence contracts authored
  and gated for both.
- **Wave 3 — owner gates:** social objectives numbers (owner decision);
  sensors for podcast's three unmeasured objectives; social manager timer
  enablement (owner activation by design); release re-pins for both via
  process-change QA after the waves land.

## Coordination note

`departments/podcast/releases/current` carries an uncommitted flip by the
podcast agent; podcast re-pin must be coordinated with that lane.
