# Wayfinder Map — Loop Factory v2: Fully Autonomous, Observable Loops

Label: `wayfinder:map` · Tracker: local markdown (tickets in `wayfinder/tickets/`) · Driver: polly (orchestrator) · Owner: Ankit

## Destination

One department loop **fully operational end-to-end**: versioned factory recipe, control-graph-driven runs (typed edges, deterministic runner, receipts as transition tokens), per-step telemetry (tokens / cost / latency / model / auth route), eval + QA scores as separate records, watch-loop self-healing backed by an independent deadman — **plus a validated export feed** (JSONL source of truth → SQLite rollup → NDJSON/BigQuery-or-GCS contract) so the visual workspace (Gemini Enterprise, ex-Agentspace) plugs in the day it is provisioned. Live external actions remain owner-promotion-gated per loop-factory hard rules.

## Notes

- **Execution is IN-map** (Ankit chose destination option B, 2026-08-02): this map carries decision tickets AND the implement tickets that make the proof loop operational. It does not end at a spec.
- Orchestration: polly. All code changes go to coding sub-agents in their own worktrees; every implementer PR gets a different-vendor cross-review; **Ankit merges — agents never do**. Dispatch registry: `.polly/registry.json`.
- Loop-factory hard rules apply unreduced: shadow-first, deny-by-default receipts, always-human floor on governance files, process change = map change + QA, headless-only + subscription-only engines.
- Standing directive from Ankit's own bee implementation map: **"Do not build or install another graph system. Consolidate the department control plane first."** Upgrade the existing spine; import patterns, not systems.
- Ticket conventions: a ticket is claimed by setting `Claimed:`; frontier = Status OPEN + empty `Blocked by` + unclaimed. One ticket resolved per session (research tickets excepted).
- **Parallel lanes warning:** Ankit runs split-tree lanes outside this map (ringer podcast hardening, dag-supervisor already merged as PR #8). Ticket [014](tickets/014-reconcile-split-tree-lanes.md) owns reconciliation; every polly PR rebases on current master before finalization and cross-review checks overlap.
- Research evidence lives in ticket resolutions, `.polly/reports/`, and `.polly/registry.json` key_findings.

## Decisions so far

- [001 — Audit loop-factory autonomy gaps](tickets/001-gap-audit.md) — control plane is real (138/138 tests); real gaps are deployment + observability: estate watchdog unscheduled (service points at open-engine), both departments drifted, zero per-step telemetry, no factory versioning, no backup/restore.
- [002 — Mine knowledge_repo graph patterns](tickets/002-knowledge-repo-patterns.md) — AAC graphs are declarative, not executable; transfer three patterns: typed-edge execution projection, deterministic factory-owned runner, receipts as transition tokens.
- [003 — Recover bee workshop notes (Jason, 2026-08-02)](tickets/003-bee-workshop-notes.md) — three-graph split (knowledge / control / graph-of-loops); watchers can fail → deadman + poisoned-registry + false-green-stops-promotion; trigger modes time/goal/event; visual surface renders canonical state, never a second source of truth.
- [004 — Telemetry, evals, and export research](tickets/004-telemetry-evals-research.md) — adopt OTel GenAI attribute names (Development stability → pin schema_version); cost + auth route are custom fields; scores are separate records aligned to `gen_ai.evaluation.*`; JSONL + derived SQLite rollup; Gemini Enterprise ingests via BigQuery/GCS connectors, A2UI can render agent-mediated dashboards later.
- [005 — Pick the first proof loop](tickets/005-pick-first-proof-loop.md) — **new Revenue / Lead Follow-Up loop** (Ankit's call, over the podcast-retrofit rec); proof loop is born v2-native through the full F0→F4 pipeline; podcast/social retrofit moves to fog.
- [006 — Stabilize the live line before the rebuild?](tickets/006-stabilize-live-line-first.md) — **yes**; `fix-estate-watchdog` and `wire-drift-check` implement tasks dispatched as parallel PRs (alarm-only, no live systemctl, no release flips by agents).
- [007 — Lock the telemetry + score schema](tickets/007-lock-telemetry-score-schema.md) — **signed off** as proposed; price table = versioned repo file; step-receipt signing folded into ticket 008 scope.
- [008 — Lock control-graph execution semantics](tickets/008-control-graph-execution-semantics.md) — **A, incremental runner over existing node scripts**, with Ankit's reserved right to rebuild from scratch; schema/receipts/telemetry designed runner-agnostic. Amended: build BESIDE the merged dag-supervisor (auditor, not runner); runner exports a versioned signed projection the independent supervisor verifies.
- [013 — Revenue department interview + intent lock](tickets/013-revenue-interview-intent-lock.md) — **INTENT LOCKED (Ankit, 2026-08-02)**. Full charter in [wayfinder/interviews/revenue-f1-interview-2026-08-02.md](interviews/revenue-f1-interview-2026-08-02.md): 4 lead types, unknown→human disposition branch, full-auto destination behind a 4-rung owner-signed ladder, 6 tripwires, suppression gate, 3-tier engine routing with loud fallback.

## Not yet specified

Fog toward the destination — graduates to tickets as decisions land:

- Heijunka slot scheduler + WIP limits + andon incidents in the manager tick (needs telemetry + runner running first).
- Drift-metric thresholds for the watch loop (reviewer burden, false positives, repeat defects) — needs first real telemetry data.
- Interview/charter pipeline updates so **future** departments are born instrumented (graph-executable, telemetry-on, export-wired) — the "input = owner interview, output = visual-workplace-ready department" goal; shape depends on tickets 007–009.
- Podcast + social v2 retrofit (graph runner, telemetry) after the Revenue proof loop is operational.
- Backup/restore of department state (interacts with system-of-record decision).
- Estate `park` kill-switch verb; auth-expiry visibility as a distinct error class.
- A2UI agent registration to render boards inside Gemini Enterprise (design only after export feed exists).

## Out of scope

- **Gemini Enterprise / Agentspace tenant provisioning and board rendering** — this map delivers the validated export contract; the workspace hookup is the next effort.
- **Building or importing a second graph system** (knowledge_repo wholesale, external graph frameworks) — per Ankit's standing directive above.
- **Promotion of any loop to live external actions** — owner gate, exercised outside this map.
- **Open-engine repo changes** — only the local systemd unit pointing at it is touched (ticket 006), not that repo.
