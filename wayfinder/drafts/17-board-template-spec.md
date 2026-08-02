# Board Template v1 — the standard board grammar for ANY loop

CANARY: blue paperclip

Status: **LOCKED direction** (Ankit, 2026-08-02: "make this a similar process
for any loop — metrics up top, main actions, and loop-specific items on
bottom"). This is ticket 17's build contract, generalizing the approved
ticket-11 prototype (v4) into a factory-standard template.

## The rule

One board grammar, instantiated per loop AND at estate level. Loop-specific
content enters as DATA through the board feed — the renderer contains zero
department names or per-loop branches (factory layer, hard repo rule).

## Zone grammar (top to bottom, fixed order)

| zone | carries | rendered from (feed kinds) |
|---|---|---|
| **Header** | loop name, autonomy state, shadow/live, epoch, last cycle, honesty label | `dept_status` |
| **1 · Metrics** (up top) | objectives: setpoint/min/target vs observed (bullet graph when min+target exist, %+week-strip for reliability-type, big-figure+support otherwise) and trend graphs (first-try line vs target, needed-a-human compare) | `metrics` objective rows + daily rollups |
| **2 · Main actions** | the andon block(s) — the ONLY saturated red, with fault, age, impact, exact fix command; approval inbox with ages + card links | `andon`, `approval` |
| **3 · Activity** | today's jobs outcome bars, self-fix ladder summary + weekly budget, telemetry rows (model, tokens in/out, route badge OAuth/API) | `active_run`, `metrics` per-lane telemetry rows |
| **4 · Loop-specific** (bottom) | whatever this loop uniquely tracks: funnels render from `funnel_stage` rows (podcast: guest funnel); labeled panels render from custom metric groups; anything without a dedicated renderer falls back to a generic labeled list — never dropped silently | `funnel_stage`, namespaced `metrics` groups |

Estate board = the same grammar where zone 1 aggregates all loops' objectives,
zone 2 merges all andons/approvals (oldest first), and zone 4 shows one compact
lane per department linking to its own board page.

## Renderer contract (ticket 17 build)

- Input: `estate/state/board-feed.ndjson` only (+ `--department <name>` filter
  for a per-loop page). Rebuild-from-feed is the acceptance test.
- Output: self-contained static HTML per DESIGN.md tokens (light editorial,
  oxblood = needs-a-human only, HTML-text graphs for anything with labels,
  capped-width SVG only for trend lines).
- Honesty: unmeasured renders as "unknown", never 0; synthetic/prototype
  surfaces carry the label; a metered-API telemetry row renders as an andon
  incident, not a stat.
- New loops inherit the board with zero board work: stand a department, emit
  the feed kinds, the board exists. A loop with no objectives yet shows an
  explicit "no objectives declared — charter setpoints missing" nudge.

## Reference implementation

`wayfinder/prototypes/11-andon-board.html` (v4, verified: 0 contrast
failures, 0 overflow at 1440/1024/390, scale-invariant graphs) is the visual
reference. Its zones map 1:1 onto this grammar; the funnel + telemetry blocks
are the canonical zone-4 and zone-3 renderers.
