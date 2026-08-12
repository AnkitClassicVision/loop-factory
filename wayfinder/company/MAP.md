<!-- label: wayfinder:map -->
# Wayfinder Map — The Goal-Driven Company: departments that derive, prove, and compose

CANARY: blue paperclip

Owner: Ankit · Signed: 2026-08-03 (destination v7, "fold it in and finalize")
Tracker: local markdown · tickets in `wayfinder/company/tickets/C##-slug.md`
Front-matter: `Status: open|closed · Type: research|prototype|grilling|task ·
Claimed: <who> · Blocked by: [C##,...]`. Frontier = open + unclaimed + all
blockers closed.

## Destination (v7, FINAL — Ankit signed 2026-08-03)

**Layer 0 — State and Memory (ground truth).** One declared system of record
per entity domain (HubSpot for sales today, swappable by charter edit).
Department state is an append-only event log; every state transition and every
message is a receipted event, so any entity's status and any conversation's
history is reconstructable at any past date. The entity log is readable ACROSS
departments: the customer sees one company, so shared-entity arbitration
(cross-department suppression, one cadence cap per contact) lives at the
kernel, never per-silo. A memory layer turns sanitized history into lessons
future runs consult.

**Layer 1 — the Unit Contract (enforcement).** No unit (node, skill, runbook,
loop) ships without: a goal traced upward · directed flow · declared
state-read/state-advanced (a unit that consumes an input must advance the
entity's state) · an artifact receipt · telemetry · a gate proportionate to
blast radius (deterministic checks on everything; model eval mandatory only
for client-facing, external, or high-risk work; low-risk may defer to a NAMED
downstream catch; "no catch" is refused by lint) · blind golden-set evals for
gated classes · a result audited against the goal (quantitative, qualitative,
or both) · a heal path · an untrusted-input boundary: content from outside the
company (inbound email, web, CRM notes) is data, never instruction, and any
unit whose behavior it influences runs one autonomy level lower. Locked
contracts from the v2 map (07 receipts · 09 evals · 10 rollup · 13 heal ·
14 auth) are inherited and made mandatory, not re-decided.

**Layer 2 — the Goal Engine (intelligence).** Humans set L0 (company) and L1
(department) goals and own the guardrails. The LLM derives L2/L3 sub-goals
continuously from the measured gap between objective and reality; a derived
goal is valid only if it traces upward AND passes the company guardrail check.
Derived goals are audited against their PARENT's outcome metric, not their
own, so the system cannot Goodhart its way to green. Goals are mutable: when a
human changes an L1 ("you were SEO-of-VAs, now you're AI"), the department
re-derives its sub-goal tree, produces a transition plan (keep / transfer /
wind down), and retires the old tree with history preserved. Human escalation
on conflict, drift, or external effect.

**Layer 3 — Composition, Comms, and Economy.** One typed message fabric rides
the event log: hire requests, escalations, status, goal changes. Channels
(Linear, Telegram, email, Buzz) are delivery adapters, not truth. Departments
converse autonomously; humans are addressable endpoints with SLAs and
refile-louder. A department missing a capability defines a goal for it and
hires another department; the hired goal traces to the hiring goal. Hiring
runs inside an economy: departments carry budgets, scarce capacity is
arbitrated by goal priority, and a portfolio review loop scales, cuts, or
kills departments by measured return against their L1 goal.

**Standing constraints.** OAuth/subscription engines primary for every model
call; API keys last resort only on explicit owner instruction (hard rule 9 ·
ticket 14). Departments pick models per task within that rule.
External-effect gates and the always-human floor never relax; autonomy is
default-on INSIDE those gates.

**Proof — six falsifiable conditions:**
1. The factory lint visibly rejects a contract-less unit (watched to fail).
2. A blind process eval (synthetic gap fixture) is detected and closed
   autonomously.
3. A department closes a goal gap no human ever decomposed into steps, every
   derived sub-goal guardrail-clean.
4. Any entity's state at any past date is answerable from the event log with
   evidence. Test case one: Gina Wesley's referral.
5. A human changes an L1 goal; the department re-derives, transitions, and
   retires the old tree with history intact.
6. Two departments targeting the same contact are arbitrated to one cadence;
   the second touch is suppressed with a receipt saying why.

## Notes

- **Plan, don't do**: this map produces decisions. Execution hands off to the
  normal F0-F6 pipeline and, where the active v2 map owns the substrate, to
  that map. No execution override here (unlike the v2 maps).
- **Relationship to the active v2 map** (`wayfinder/MAP.md`): that map builds
  the runtime substrate (telemetry, receipts, evals, rollup, Revenue pilot)
  and stays authoritative for it. This map decides the layers ABOVE it. Locked
  v2 contracts are inherited (see Destination). Ticket C10 owns the bridge,
  including landing the completeness/return-path gate into the active map
  BEFORE Wave 2 (Revenue) un-holds.
- **Grounding artifacts**: destination interview
  `wayfinder/company/interviews/2026-08-03-destination-interview.md`; the
  referral-gap investigation + fix spec
  `tasks/2026-08-03-open-loop-comms-gap.md` (the motivating failure: 57 asked
  → 16 replied → 0 harvested, fully instrumented, zero effective).
- **Hard rules** (`CLAUDE.md` 1-9) bind every ticket. Governance files stay
  human-only. Shadow-first, deny-by-default, receipt-gated, headless-only,
  subscription-only.
- **Skills per session**: `ringer` (model-calling runs), `atlas` (drift),
  repo runbooks (`factory-pipeline.md`, `process-change-qa.md`,
  `promotion-ladder.md`).
- One ticket resolved per session (research excepted). Claim before work.

## Decisions so far

- Destination v1→v7 locked (this file, §Destination) — grilled across the
  2026-08-03 session; full evolution and owner statements in
  [the destination interview](interviews/2026-08-03-destination-interview.md).
  Key owner calls: goal levels L0/L1 human + L2/L3 LLM-derived · gap-generates-
  subgoal mechanism · proportionate QA gating with named catch points ·
  channels-are-adapters · goals mutable with transition semantics · economy +
  shared-entity arbitration + untrusted-input boundary + Goodhart audit folded
  in on the final adversarial pass.

## Not yet specified

Fog toward the destination — graduates as tickets close:

- **Company brain**: versioned shared company-context layer (ICP, voice,
  offer, pricing, positioning) every department consults and none owns; edit
  once, every department re-derives. Shape depends on C02/C03.
- **Human capacity metering**: escalation priority classes, batching windows,
  measured human-latency budget, safe-hold degradation when the owner is
  saturated. Needs real escalation-volume data; the babysitting-tax
  scoreboard in the v2 map's fog is its measurement seam.
- **Channel adapter defaults**: which adapter per recipient/severity (Linear
  vs Telegram vs email vs Buzz). Config, decided after C05 fixes the fabric.
- **Memory-layer retrieval policy**: when a run MUST consult lessons vs may;
  staleness and contradiction handling. After C03.
- **Model-picker policy detail**: how a department chooses engines per task
  within OAuth-first. After C06's economy gives cost signals.
- **Department retirement/merge mechanics**: what "kill" does to state,
  in-flight work, and the event log. After C02 + C06.

## Out of scope

- **Relaxing any external-effect gate or the always-human floor** — autonomy
  grows inside the gates; moving a gate is its own owner decision, never a
  side effect of this map.
- **The v2 substrate build** — telemetry/evals/rollup/Revenue pilot belong to
  the active `wayfinder/MAP.md`; this map does not re-decide or duplicate it.
- **Vendor migrations** (HubSpot replacement, graph_agent map-store
  migration) — SoR is swappable by design; actually swapping is a fresh
  effort.
- **Building or importing a second graph/agent framework** — Ankit's standing
  directive: consolidate the existing spine; import patterns, not systems.
