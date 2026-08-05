# Change card — plain-English escalation copy (2026-08-05)

**What changes:** compare_charter.py gains a MEANINGS map (what_it_means /
what_it_needs per failure class, owner language, jargon banned by test);
escalate_outbox.py leads every outbox row eli5 with WHAT THIS MEANS / WHAT IT
NEEDS; technical one_question moves into context for machines.

**Why:** owner received ANK-319 and had "no clue what it means" (verbatim,
2026-08-05). Owner directive: every escalation is a simple message on what it
means and what it needs.

**Intent unchanged:** classification, severities, dedup, TTL untouched.
QA path: validate PASS -> shadow cycle -> re-pin -> qa drift-clean.
