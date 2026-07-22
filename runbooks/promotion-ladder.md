# Promotion Ladder Runbook (earned autonomy, per action class)

Ladder per action class: `shadow → draft_only → gated_live → autonomous`.
Default operating mode is FULLY AUTOMATED WITH ESCALATION — the department
runs without a human until a gate or breach — but every externally-visible
action class starts gated and EARNS each step with evidence.

## The always-human floor

`charter.yaml`, any file carrying autonomy state / promotion thresholds /
interaction whitelists / setpoints, and the runbooks are governance files:
ALWAYS human, at every level, never eligible for autonomy, never overridable
by a "process change." The single approval that promotes a process-change
class covers process-quality edits only, never governance files.

## Promotion evidence (anti-Goodhart)

- Evidence population = a deterministic query over ALL triggers in the window
  (failures reset the streak) — never "N passing runs exist somewhere."
- Counting is done by the estate watchdog / a read-only job — never by the
  department manager grading itself.
- Cross-model QA (a different model family from the doer) on every run counted
  toward promotion.
- Setpoints include at least one outcome sensor independent of the
  department's own classifiers.
- Escalation-rate denominator: the babysitting metric counts escalations per
  ambiguous case, so it cannot improve by under-escalating.

## The asymmetric ratchet

- Demotion is AUTOMATIC on any floor breach and sets a floor only a
  human-approved packet can raise.
- Heals never promote. L1/L2/L4 restore quality at the post-demotion level.
- No-reply on an approval request past TTL (default 48h) = DENY and re-file.
  Never auto-approve. Stated, not assumed.

## Human decision integrity

- Delivery channel (outbox → phone/bot) is separate from the reply channel.
- Every decision write is append-only auditable (runs row).
- Decision packets state options + tradeoffs framed by the watchdog tier, not
  by the requesting department.

## Escalation defaults

- Breach → escalate with ONE question and concrete options (A/B beats essay).
- Stuck past SLA → re-ping, then escalate up the target list from the
  interview (Q14).
- Kill conditions (charter `kill_if`) are kill, not pause: non-restartable
  without a new human charter decision.
