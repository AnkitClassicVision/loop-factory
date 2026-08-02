---
title: Idempotency + retry audit of podcast nodes
status: closed
type: task
assignee: wf-08-idempotency
blocked_by: []
---

## Question

For every podcast department runtime node: what happens today on trigger
replay (same trigger twice), worker crash mid-step, and retry — where are
duplicates possible, what dedupe keys exist, and what retry limits apply?
Produce the per-node audit table (node, trigger key, idempotent yes/no,
retry policy, gap) that ticket 15 must close. Read-only audit; forcing
synthetic failures happens later in shadow, not here.

## Resolution

Full per-node audit table and evidence: `wayfinder/research/08-idempotency-retry-audit.md`.

Ranked gaps ticket 15 must close (highest first):

1. `factory/manager.py` writes `STATE.json`/`runs.jsonl`/`heartbeats.jsonl`
   without the `record.records_lock` fcntl lock that protects the same files
   elsewhere — the only genuine unlocked-write race found; concurrent
   invocation can silently lose a cycle's findings (last `os.replace` wins,
   no error).
2. Manager-originated escalations have no fingerprint/marker dedup (unlike
   `escalate_outbox.py`'s durable-marker mechanism), so a persisting breach
   re-escalates every cycle with no way to collapse replays.
3. `pipeline_sensor.py`, `publish_verifier.py`, `manifest_sensor.py` never
   call `record.write_record` — no run receipt at all, so a crash is
   indistinguishable from "ran and found nothing."
4. `factory/human_in_the_loop.py`'s `_save()` is the one non-atomic
   JSON/JSONL writer in the department (plain `write_text`, not temp+replace)
   — a crash mid-write can corrupt `approval_queue.jsonl`.
5. `observations.jsonl` (and eventually `heals.jsonl`) grow unbounded with no
   dedup on replay — confirmed live at 11,166 lines with hundreds of exact
   duplicates per `(sensor,subject)`; `compare_charter.py` absorbs this
   correctly today but nothing rotates/compacts the file.
6. `heal_apply.py`'s daily attempt-counter reservation and its receipt append
   are two separate non-transactional writes; a crash between them silently
   burns a retry attempt with no visible record (currently inert in shadow
   mode, must fix before live promotion).
7. The self-heal ladder (`factory/heal_ladder.py`'s `HealLadder`,
   `heal_select.py`/`heal_apply.py`/`heal_verify.py`) is not wired into the
   daily chain at all — an open healable incident has no automatic path to a
   heal attempt today.
8. `dag_supervisor.py`'s direct appends to `incident_candidates.json` are
   only deduped by `compare_charter.py`'s next successful overwrite; an
   aborted chain between the two leaves duplicate entries until the next
   good run.

One well-designed exception worth preserving as the model to copy:
`escalate_outbox.py`'s durable-marker dedup against `decisions_outbox.jsonl`
is proven crash-safe by an explicit injected-crash test
(`test_records_integrity.py::test_outbox_append_survives_state_write_crash_without_duplicate`).
