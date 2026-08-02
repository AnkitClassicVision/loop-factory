---
title: Prototype the interim heijunka/andon board
status: closed
type: prototype
assignee: coordinator-fable
blocked_by: [10]
---

## Question

What should the interim visual factory board look like — a generated,
self-contained HTML board (lineage: docs/seeit estate-control-room v0 +
loop-factory-house in open-engine) rendered from the ticket-10 rollup
contract: department lanes, active runs + current step, blockers/andon
signals, approval inbox, token/cost meters, throughput/failure views,
heijunka-style scheduling row? Build the cheap concrete prototype from
synthetic rollup data, react to it with Ankit, iterate once. The prototype
locks the board's information design; live wiring is ticket 17.

## Resolution

Design locked through four verified iterations (2026-08-02):
- v0 (dark dashboard) rejected by Ankit → redesigned per OB design prefs
  (light editorial, white space as structure) after a rejected Kimi lane
  (HARNESS_FAIL: missing binutils in the session container) and a stopped
  Claude lane — Ankit directed "just use fable for design."
- v2 added Ankit's objective-driven direction: podcast goals (publish 100%,
  hopper min 2 / target 6 as a bullet graph), the guest funnel
  (outreach → recorded), audience metrics.
- v3 fixed everything a Playwright verification pass measured (funnel 8.8x
  the andon, SVG label collapse at 390px, AA failures) by converting funnel/
  bullet/outcome graphs to scale-invariant HTML; re-verified: 0 contrast
  failures, 0 overflow at 1440/1024/390.
- v4 (approved: "this is great") moved Seven-days up top, rewrote the two
  unclear zones in plain language, and added the Telemetry zone
  (model, tokens in/out, OAuth-vs-API route badges; metered = andon).
- Generalization (Ankit, same session): the layout becomes the standard
  template for ANY loop — header → metrics up top → main actions →
  loop-specific items on bottom — encoded as
  `wayfinder/drafts/17-board-template-spec.md`, ticket 17's build contract.

Artifacts: `wayfinder/prototypes/11-andon-board.html` (v4 reference
implementation; v0–v3 preserved alongside), verification receipts in the
scratchpad screenshots + this map's history, board-feed contract addenda
v1.1/v1.2 in `drafts/10-rollup-proposal.md`.
