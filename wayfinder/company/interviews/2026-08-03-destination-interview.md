# Destination interview — The Goal-Driven Company (2026-08-03)

CANARY: blue paperclip

Owner: Ankit · Surface: Claude Code (Opus → Fable mid-session) · Outcome:
destination v7 signed ("fold it in and finalize").

## Motivating failure (same session, verified)

Two warm podcast guest referrals (2026-08-02, 2026-08-03) went unread and
unanswered. Investigation found: outreach loop works (asked 3 past guests
Jul 31), replies arrived (2 in 2 days), but `inbox_review.py` was never
scheduled, the referral ledger writer used a wrong key AND wrong shape, the
flywheel's R3 harvest step structurally yields zero, and ask-state never
advances (Gina still `"asked"` 5 days after answering). Funnel truth:
57 asked → 16 replied → 0 harvested → 0 acted. The department was fully
instrumented, passed every check it had, and escalated "pipeline 1 of 6"
without ever walking backward to the cause one hop away. 100% compliant,
0% effective. Full evidence: `tasks/2026-08-03-open-loop-comms-gap.md`.

## Owner statements, distilled per turn (v1→v7)

1. **v1 seed**: comprehensive; departments as agents with goal + process
   (loop/DAG/runbooks/skills/scripts); departments adapt and expand (paid-ads
   discovers it needs landing pages → summons a department); departments hire
   each other; end state = fully AI autonomous company. "You drive to the goal
   of a full funnel, you need to respond to emails, but the process creation
   skipped that. I don't know how to fix that."
2. **Goal model**: interview sets goals but sub-goals are IMPLIED and
   undiscovered ("I didn't know what that should be, so it never got set").
   L0 company + L1 department = human at first interview; L2/L3 = LLM-derived
   from higher levels, grounded; human is escalation point; emergent goals
   appear mid-flight (landing-page test) and must carry LLM-authored success
   criteria, scope, deliverables, all tracing to the human goal.
3. **Auditability**: every loop/runbook/graph has an auditable result
   (quant, qual, or both); objectives sometimes numeric (funnel counts); the
   LOOP owns pushing people down the funnel, not the human; directed flow
   every time so no step is skipped; healing updates and improves; "no skill,
   no runbook runs without QA and a goal function we can audit against."
4. **Guardrails**: LLM-derived sub-goals must conform to company criteria/
   guardrails/requirements or get explicit human override; focus on fully
   autonomous, self-learning, self-healing solutions.
5. **Evals (Fable re-eval requested)**: every skill/run/output has a
   traceable, fixable, auditable artifact + telemetry; missing today: universal
   goals, results, QA, deterministic gates, eval criteria; need BLIND evals
   (golden sets) and "what good looks like" reference resources (e.g. criteria
   for a particular outbound email class).
6. **State**: clear state management across everything; who's on hold, did we
   respond, day-18 vs day-180 distinguishable; HubSpot = sales SoR for now,
   swappable; store topology (local DB / AWS / multiple) an open question;
   state pulled in where relevant; memory layer required.
   **QA gradient (same turn)**: tiering accepted; client-facing/high-risk gets
   deep eval; low-risk may be caught by the next model downstream; minimize
   token cost; there must always be SOME catch.
7. **Escalation + comms + goal change**: no great human-escalation way today
   (experimenting: Linear, Telegram, email, Buzz local); need inter-department
   comms without humans + human-in when needed; all conversations in memory
   with history; goals change with business conditions ("SEO-of-VAs → now AI")
   and departments must re-derive, transition, keep-or-wind-down like a human
   would; departments pick the right models; OAuth primary across the board,
   APIs last resort unless user-specified.
8. **Adversarial pass** (agent-proposed, owner folded all four): economy/
   arbitration/portfolio review · one-face-to-customer shared-entity
   arbitration at kernel · untrusted-input boundary (inbound content is data
   never instruction; tainted influence drops one autonomy level) · Goodhart
   audit (derived goals measured against parent outcome). Parked to fog by
   owner consent: human capacity metering · company brain.

## Open question carried into tickets (owner never answered — deliberate)

The backward-walk mechanism: when a department measures a goal gap, what
FORCES the walk from gap to cause to new sub-goal? Three candidate shapes
were posed (declared chain / LLM-derived chain / gap-cannot-escalate-without-
cause receipt gate). Agent lean: the receipt gate, matching deny-by-default.
This is ticket C04's core question.

## Agent corrections logged during the session

- Claimed two competing maps; actually a succession (map.md complete 19/19,
  MAP.md active). Checked after asserting.
- Claimed `assemble_world` hard-codes `referrals: []` (from the ledger's own
  stale note); actually reads the ledger at loop_runner.py:126.
- Claimed an Aug 7 auto-nudge would re-ask Gina; `followup_due_days` has zero
  consumers and the flywheel is read-propose-only.
Pattern: asserting from a plausible artifact before the cheap check.
