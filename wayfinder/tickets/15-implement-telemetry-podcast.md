---
title: Implement the v2 run-record contract on podcast
status: closed
type: task
assignee: coordinator-fable
blocked_by: [07, 08, 14]
---

## Question

Wire the ticket-07 run-record contract into every podcast runtime node —
tokens/cost per engine lane (per ticket 04 sources), model + auth class,
attempts, disposition — closing the idempotency/retry gaps from ticket 08 and
the auth-block recording from ticket 14. Executed proof: `loopfactory.py
validate --name podcast` PASS, node QA checks PASS, records visibly carrying
the new fields for a full shadow cycle. Route the build through Ringer
manifests (executed checks prove pass/fail); process-change-qa runbook
applies (map patch + re-lint + re-shadow + re-pin).

## Resolution

Complete with executed proof (2026-08-02, two Ringer rounds, 7/7 tasks PASS
first attempt, coordinator line-reviewed):
- Round 1: runrecord module (locked contract), manager lock + escalation
  dedup (audit gaps #1/#2), atomic HITL writes (#4), observations rotation
  (#5), engine usage/auth capture on social. Coordinator fix: run records
  carry telemetry summaries only, never draft content.
- Round 2: all 8 podcast daily-chain nodes emit one validated runs-v2 record
  per invocation, fail-closed on record gaps; read_release / emit_record /
  timed_emit conveniences; run_id readable on review cards (row_hash
  byte-identical, test-proven).
- Maps patched per hard rule 4 (untraced_allowed rationales; social Linear
  map debt in fog). CHECK PASS 198 tests; validate ok both departments.
- SHADOW-CYCLE PROOF: live podcast_daily.sh run → 8/8 records in
  runs-v2.jsonl, every one validating against the contract, carrying release
  {hash, source_ref}, duration_ms, date-node dedupe keys; shadow held
  (external_actions_taken: []); manager epoch 1522.
- Releases re-pinned from commit 816794a: podcast 16f626e97869aae9, social
  21d236ef56471668; qa drift check zero mismatches, intent_locked.
Known refinement (rides ticket 19's clean-day definition): sensors mark
status 'error' when they SENSE findings, not only on run failure — visible
by design; clean-day counting keys off manager heartbeats.
Deviation: heal-lane nodes + rotation tool get record wiring in ticket 18
where they're being reworked anyway.
