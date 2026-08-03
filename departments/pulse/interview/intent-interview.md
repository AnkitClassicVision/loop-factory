# pulse — F1 intent interview (compressed for acceptance throwaway)

Owner: Ankit · Date: 2026-08-03 · Surface: Claude Code AskUserQuestion readback
Compression rationale: throwaway acceptance department; runbook Stage 5 allows
question-bank sections to carry a recorded skip rationale. Four essential
decisions asked with recommended answers first; all confirmed VERBATIM below.

## Answered (verbatim owner selections)

1. MISSION + PROVING SLICE — "Confirm": pulse reads fixture notes from
   `inbox/`, builds a daily owner digest draft (shadow, never sends), asks
   `clarify_note` on unreadable notes with `replies/` as return path, 48h SLA.
   Proving slice: the daily digest loop over fixture notes.
2. OBJECTIVES — "Confirm": `digest_coverage` (every readable note in exactly
   one digest within 24h; setpoint/min/target 100%) and
   `ask_return_integrity` (zero asks older than 48h unharvested;
   setpoint/max 0). Aggregate state only.
3. HARD FLOORS — "Confirm": NEVER send/publish/spend/network/touch real
   data/enable timers; kill_if any external action attempted or governance
   edit attempted.
4. INTENT LOCK — "INTENT LOCKED" on full readback (mission, objectives,
   floors, cadence daily 07:00 never-enabled, ask class with declared return
   path).

## Skipped question-bank sections (rationale recorded)

- Budget: inherits factory defaults (subscription-only, $0 API) — throwaway
  runs zero model calls in runtime; nothing to size.
- Memory/seams: none — department deletes at the end of the run.
- Escalation targets: factory default human-in-the-loop outbox; no external
  channel exists in scope.
- Edge grill (gaming/weird inputs): the unreadable-note path IS the edge
  under test; broader grill adds nothing to a fixture-only throwaway.

## INTENT LOCK

decided_by: ankit · date: 2026-08-03 ·
surface: claude-code-askuserquestion-readback-acceptance-run ·
Agent did not self-certify; owner selected INTENT LOCKED on readback.
