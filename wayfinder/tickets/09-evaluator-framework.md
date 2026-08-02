---
title: Decide the v2 evaluator framework
status: closed
type: grilling
assignee: coordinator-fable
blocked_by: [02, 07]
---

## Question

How do evaluators work in v2: the deterministic-first ladder (schema,
required-field, duplicate, date, permission, external_actions_taken checks)
before any model judgment; when a cross-model LLM evaluator is required
(promotion-counted runs already require cross-model per CLAUDE.md); how
evaluator verdicts land in the run record (ticket 07) and gate receipts
(deny-by-default); and what the per-node-class eval registry looks like so
new departments inherit evals instead of inventing them? Decide with Ankit,
informed by ticket 02's port recommendations (evaluate_node.py etc.).

Asset: coordinator-drafted proposal ready for reaction at
`wayfinder/drafts/09-evaluator-proposal.md` (6-point framework + 3 forks with
recommendations).

## Resolution

Ankit accepted the full proposal (2026-08-02): two-tier ladder (deterministic
Tier-1 first, never reaches a model on fail; Tier-2 model judgment by a
DIFFERENT engine lane than the producer); weighted critical/major/minor →
block/revise/allow verdicts mapped onto receipts deny-by-default; golden set
(5–10 cases, open + holdout) required before a Tier-2 evaluator's verdicts
gate; eval registry seeded in templates/ with per-department overrides;
estate-level second-order checks (scheduled golden-set re-runs + pass-rate
drift alarms as andon signals); evaluator model swaps must beat the incumbent
on holdout. Forks: (1) Tier-2 mandatory for promotion-counted runs +
human-visible outputs only; (2) no golden set → advisory-only, never gating;
(3) registry = templates + overrides. Detail:
`wayfinder/drafts/09-evaluator-proposal.md` (ACCEPTED). Unblocks ticket 13.
