# Design

Visual system for the Loop Factory estate board (and future factory-visual
surfaces). Register: product. Strategy: Restrained — pure white surface, one
saturated color reserved for meaning.

## Theme

Scene: a lean factory floor at first light — white wall, one red andon lamp.
Light theme, editorial. The mood lives in typography and the semantic colors,
never in the background.

## Color (OKLCH only)

- `--bg`: oklch(1 0 0) — pure white, no hidden warmth.
- `--surface`: oklch(0.965 0.002 0) — quiet panel tint, used sparingly.
- `--rule`: oklch(0.885 0.003 0) — thin structural rules.
- `--ink`: oklch(0.18 0.01 0) — body/headline text (≥7:1 on bg).
- `--muted`: oklch(0.44 0.008 0) — secondary text (≥4.5:1 on bg).
- `--red` (primary/andon): oklch(0.50 0.194 0) — oxblood. RESERVED: means
  "a human is needed now." White text on red fills.
- `--green` (accent): oklch(0.52 0.12 155) — accomplished/ok/goal progress.
- `--amber`: oklch(0.65 0.13 70) — warn/waiting/blocked.
- `--violet`: oklch(0.52 0.09 300) — degraded/self-heal activity.
- State is never color-only: every colored mark carries a count or label.

## Typography

One family: system-ui stack (`system-ui, -apple-system, "Segoe UI", Roboto,
sans-serif`). Fixed rem scale, ratio ~1.2. Hero figure 4.5rem/650. Section
titles 0.95rem/650 sentence case — no uppercase tracked eyebrows. Data uses
`font-variant-numeric: tabular-nums`. Body 0.95rem, line-height 1.5.

## Layout

Full-bleed page, content max-width 1180px, single-column vertical flow with
generous space (zone gaps ≥ 3rem). Two-column splits inside a zone only when
both halves are peers. Thin rules separate zones; no card grids, no nested
boxes.

**Board Template v1 zone grammar (locked, Ankit 2026-08-02)** — every loop's
board and the estate board use the same fixed order: Header (identity,
autonomy state, honesty label) → 1 Metrics up top (objectives:
setpoint/min/target vs observed, plus trend graphs) → 2 Main actions (andon —
the only saturated red — and the approval inbox) → 3 Activity (today's jobs,
self-fix summary, telemetry with OAuth/API route badges) → 4 Loop-specific
items on the bottom (funnels and custom panels, rendered from feed data,
never from per-loop board code). Full contract:
`wayfinder/drafts/17-board-template-spec.md`.

## Graphs

Inline SVG only, direct-labeled (values on the marks, no legends where a
label will do), editorial: thin strokes, flat fills, no gradients, no chart-
library look. Canonical marks: segmented progress bars (tickets/phases),
stacked horizontal outcome bars (ok/blocked/error), a 7-day line for rates,
paired compare bars for week-over-week. Axis text ≥ 0.7rem in `--muted`.

## Motion

One animation: a 2s opacity pulse on an active andon alert. Everything else
is static. `prefers-reduced-motion: reduce` disables the pulse (solid red
carries the meaning).

## Components

- Zone header: sentence-case title + thin rule, optional right-aligned
  context note in `--muted`.
- Stat row: large tabular figure + one-line label beneath, separated by
  white space, never boxed.
- Andon alert: red-filled block, white text, states the fault, the age, the
  queued impact, and the exact fix command in a `<code>` slot.
- Status chip: colored dot + label + count, inline, never a pill background.
- Feed rows: single-line entries with thin dashed separators.

## Honesty furniture

Every synthetic surface carries "PROTOTYPE — synthetic data" near the title
and a footer naming the real data source (`board-feed.ndjson`). Unmeasured
values render as "unknown", never 0.
