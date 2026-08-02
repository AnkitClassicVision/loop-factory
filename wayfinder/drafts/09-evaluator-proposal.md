# Ticket 09 draft — v2 evaluator framework

CANARY: blue paperclip

Status: **ACCEPTED — locked framework** (Ankit, 2026-08-02, all forks as
recommended; see ticket 09 Resolution). Changes now follow process-change QA.
Grounded in the ticket-02 port findings
(evaluate_node.py / qa_agent_package.py / models.json), ticket 03
(deterministic-vs-independent-judge split), the Bee delta ("watchers can still
fail"), the locked ticket-07 contract (`evaluator` field), and social's
existing N5-qa shape.

## What this is (plain English)

The rules for how a loop's work gets graded before it counts. An agent never
grades its own work without rules: cheap deterministic checks run first, an
independent model judges only where judgment is genuinely needed, and a
failing grade blocks the receipt — deny-by-default, same as everything else.

## The framework

1. **Two-tier ladder, deterministic first.** Tier 1 = codified checks the
   runtime executes itself (schema, required fields, duplicates, dates,
   permissions, `external_actions_taken`); a Tier-1 fail never reaches a
   model. Tier 2 = model judgment, only for content quality/ambiguity, run by
   a DIFFERENT engine lane than the one that produced the work (independence:
   the codebase-harness precedent + existing cross-model rule).
2. **Weighted verdicts** (port from qa_agent_package.py): defects classed
   critical/major/minor → verdict `block` (any critical, or majors over
   threshold), `revise` (majors/minors within the edit-round budget), `allow`.
   Verdict maps onto the receipt: `block` → `status: blocked`, no receipt for
   the step; `revise` → another `round` (capped by charter
   `max_edit_rounds`); `allow` → pass.
3. **Golden set + holdout** (port from evaluate_node.py): every node class
   that uses a Tier-2 evaluator gets a small golden set (5–10 fixed cases with
   known-correct verdicts, open + holdout split). The evaluator must score
   clean on the golden set before its verdicts count — a check nobody has
   watched fail is not a check; the golden set is where we watch it fail.
4. **Eval registry, inherited not invented.** One registry file per the
   factory (`templates/` seed + per-department overrides) mapping node class →
   required Tier-1 checks + Tier-2 evaluator spec + golden-set path. New
   departments inherit; F2 authoring fills the overrides.
5. **Second-order check** (the Bee "watchers can fail" delta): the estate
   cycle spot-checks evaluator health — golden-set re-run on a schedule +
   drift alarm when pass rates move abnormally (feeds the `metrics` kind of
   the board feed). Evaluator drift is an andon signal, not a silent decay.
6. **Model-swap discipline** (port from improve_node.py): swapping a Tier-2
   evaluator's engine/model requires beating the incumbent on the holdout set
   — cheaper-or-better, proven, never vibes.

## Forks (3)

1. **Where Tier-2 is mandatory**: promotion-counted runs (already required by
   CLAUDE.md) + anything human-visible (drafts, cards, briefs)? Recommend
   **yes, exactly that scope** — sensors and deterministic transforms stay
   Tier-1 only.
2. **Golden-set gate strictness**: does a node class with NO golden set get to
   use Tier-2 verdicts? Recommend **no** — until a golden set exists, Tier-2
   runs advisory-only (recorded, not gating), so we never gate on an unproven
   judge.
3. **Registry home**: factory-level seed in `templates/` with per-department
   override files (recommended — matches charter/templates pattern), vs one
   central estate registry. Recommend **templates + overrides**.
