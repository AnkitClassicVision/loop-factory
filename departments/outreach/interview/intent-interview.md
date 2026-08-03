# outreach — F1 intent interview

Owner: Ankit · Date: 2026-08-03 · Surface: Claude Code AskUserQuestion readback
Format: scoping round (2 questions) + F1 round (4 questions), recommended
answer first, VERBATIM selections below. Question-bank sections not asked
carry recorded skip rationales.

## Scoping round (verbatim)

- Department shape — "One 'outreach' dept": single department governing HSD
  + sales outreach lanes together.
- Sends posture — "Aim for auto-send": charter pre-declares specific send
  classes intended for autonomy after burn-in; still shadow-first with a
  declared promotion path.

## F1 round (verbatim)

1. F1-A sensing authority — "Split it": podcast keeps guest-related obe
   sensing (its existing four watches, unchanged); outreach takes HSD +
   sales only. One unit, one watcher; boundary documented in
   creation/tickets/D1-authority-split.md.
2. F1-B send classes — "All three (Recommended)": approved_draft_execution,
   warm_reply_followup, reactivation_nudge; each separately promotable
   after 14 clean shadow days + 100% voice-gate pass on that class; cold
   outreach excluded forever per the cold-email guardrail.
3. F1-C objectives — "Confirm": hard zeros outreach_state_drift,
   send_class_integrity, approval_queue_aged (>48h); measure-first
   voice_gate_pass_rate + reply/harvest rates, ratified after two shadow
   weeks.
4. F1-D floors + lock — "INTENT LOCKED": NEVER cold email outside an
   explicit ankit_manual_exception, never bypass or weaken the voice gate,
   never send outside a promoted class, never touch PHI, governance
   human-only; KILL IF a send occurs outside a promoted class or a gate
   bypass is detected.

## Skipped sections (rationale)

- Budget: factory defaults (subscription-only, $0 API); the department's
  own runtime is deterministic sensing/reconciling — model calls occur in
  the governed lanes it watches, under their own budgets.
- Memory/seams: local state only at standing; remote backends deliberate
  later seams.
- Escalation target: owner outbox, factory default; ping ceiling adopted
  from podcast precedent (5/day then digest) pending owner tuning.
- Edge grill: the send-class boundary and gate-bypass detection ARE the
  edges; chartered as kill conditions rather than re-grilled.

## INTENT LOCK

decided_by: ankit · date: 2026-08-03 ·
surface: claude-code-askuserquestion-readback-r2 ·
Agent did not self-certify; owner selected INTENT LOCKED on full readback.
