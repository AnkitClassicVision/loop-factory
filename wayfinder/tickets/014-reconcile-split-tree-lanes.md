# 014 — Reconcile split-tree parallel lanes

Status: OPEN · Type: task (HITL at the merge points) · Claimed: — · Blocked by: — (fed by explore-parallel-lanes, in flight)

## Question

Ankit is running parallel agent lanes on this repo outside this map — known: `ringer-work/podcast-loop-hardening-r1/watchdog-spine` (locked worktree) and the already-merged `feat/dag-supervisor` (PR #8, now master HEAD 3b7eaf9); `one-true-master-r56/dag-supervisor` is prunable. The end processes must reconcile after the fact. Decide and execute the reconciliation: what each lane produced, which artifacts win where they overlap, and in what order branches land so nothing silently overwrites anything.

## Working rules until resolved

- Every polly implement worktree branches from current master and rebases before PR finalization if master moves.
- Cross-review of polly PRs must check for semantic overlap with dag-supervisor and the watchdog-spine lane once the explore report lands.
- No polly agent ever touches the ringer worktrees; reconciliation recommendations go to Ankit, who owns merge order.

## Inputs

- `explore-parallel-lanes` report — LANDED 2026-08-02. Findings:
  - **watchdog-spine r1**: worktree never initialized (locked at zero SHA, `git worktree add failed` in its worker log) — administrative residue, no code to merge.
  - **watchdog-spine r1b**: already MERGED as `ab6131e` — the podcast department-tier watchdog (sense_estate / compare_charter / fingerprint_dedup / escalate_outbox / record.py). Department tier and estate tier are separate; no file collision with polly/fix-estate-watchdog.
  - **one-true-master-r56**: physical worktree gone; retained index blobs identical to merged `645530b`; applying its stale index would ROLL BACK PR #8 wiring. Nothing to salvage.
  - **PR inventory**: PRs #1–#8 all merged; no open/unmerged branches besides the two polly lanes.
  - **Semantic contracts to preserve** (bind tickets 008/012): (1) estate deadman expects STATE.json `last_cycle_at` + integer `epoch` + heartbeat `emitter/kind/payload.epoch` shapes — version any v2 record-schema change; (2) a live post-pin edit stays an ALARM, never becomes runner input; (3) drift findings, DAG-projection findings, department health findings, estate deadman alarms keep distinct versioned record types + dedup keys; (4) runner (execution authority) and supervisor (audit) stay separate components.

## Live event 2026-08-02 (supersedes item 1 below)

Owner confirms the podcast-telemetry ringer lane is STILL RUNNING and will output a new podcast process. Its work-in-progress lives as ~1509 lines of uncommitted modifications in the LIVE checkout (manager.py +232/-65 lockutil work, human_in_the_loop.py, podcast subgraphs.json, 5 social runtime files, ringer checks) — backed up to `.polly/reports/live-lane-uncommitted-2026-08-02.patch`. HOLD list until that lane lands: no `git pull` in the live checkout (master b162264 waits), no estate-watchdog install (needs pulled code), no polly writes to any file in the patch's file set. Wave-1 worktrees (graph-runner, telemetry-spine) are isolated from origin/master and unaffected. When the lane finishes: reconcile-live-lane implement task (apply patch in clean worktree vs master, resolve manager.py overlap with the merged drift sensor, gates + cross-review + PR), then one clean live-tree update + watchdog install under standing permission.

## Remaining to resolve

1. Ankit confirms no OTHER active split-tree lanes beyond what the scout found (it found none live).
2. Owner runs `git worktree prune` + removes the locked r1 dir when convenient (administrative cleanup, mutates repo metadata — owner-run).
3. Merge order for polly PRs (recommendation: PR #9 wire-drift-check first, then estate-watchdog PR — deadman consumes shapes the manager writes).

## Resolution

(pending items 1–3 above)
