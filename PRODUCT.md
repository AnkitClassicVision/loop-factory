# Product

## Register

product

## Users

Ankit — founder-operator of an estate of autonomous AI departments (podcast,
social, more coming). He walks past this board several times a day, usually
mid-task, often context-switching. The job: in under ten seconds, know whether
anything needs him; in under a minute, know where the program stands relative
to the goal. He has ADHD — the board must answer, never make him hunt.

## Product Purpose

The visual factory control surface for the Loop Factory estate — a lean
manufacturing andon/heijunka board for AI loops. It renders exclusively from
the estate board feed (`board-feed.ndjson`): goal progress, current health,
errors/issues, and trends. Success = Ankit stops asking "what's happening with
X" in chat because the board already answered, and zero missed
needs-a-human moments.

## Brand Personality

Sleek, editorial, calm. Three words: legible, honest, quiet. The board is a
factory wall, not a SaaS product tour — information carries the drama, the
design never does. Verbatim owner direction (2026-08-02): "like an operations
metrics board with clear indicators and graphs — very clear on where we are,
what's been accomplished relative to the goal, errors/issues; everything else
is nice-to-have."

## Anti-references

- The rejected v0 board (2026-08-02): dark glassy dashboard-dense, equal
  visual weight everywhere, panel-and-pill AI-generic.
- Generic SaaS analytics dashboards (identical stat-card grids, gradient
  accents, hero-metric clichés).
- Anything a viewer would call "AI made that."

## Design Principles

1. Ten-second walk-past test: state of the estate readable at a glance from
   two meters — one saturated color exists and it means "a human is needed."
2. Goal-relative, not activity-relative: progress toward the named goal
   outranks raw activity counts, always, in position and size.
3. White space is the structure; thin rules and type hierarchy do the work
   cards and boxes would otherwise fake.
4. Every number is honest: synthetic data is labeled synthetic, unmeasured is
   shown as unknown, never zero.
5. The board disappears into the task — earned familiarity over novelty.

## Accessibility & Inclusion

WCAG AA: body contrast ≥ 4.5:1; state never conveyed by color alone (always
paired with a label or count); tabular numerals for all data; reduced-motion
alternative for the single andon pulse; readable at 1366px laptop through
wall-monitor widths.
