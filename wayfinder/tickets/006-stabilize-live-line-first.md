# 006 — Stabilize the live line before the rebuild?

Status: CLOSED · Type: grilling (HITL) · Resolved: 2026-08-02 by Ankit

## Question

Do we fix the two live fires from the audit **first**, as the opening execution tickets, before any v2 machinery lands?

Fire 1: `estate-manager.service` points at the open-engine repo — loop-factory's estate has not cycled since Jul 28; social died Jul 31 with zero escalation.
Fire 2: both departments run in unremediated drift (podcast 6, social 10 diverged artifacts) and the daily loops never invoke the drift check — hard rule 4 silently violated today.

## Recommendation

**Yes — stop the line, fix the abnormality first** (lean discipline; also Jason's deadman/false-green warnings made concrete). Two bounded implement tickets on approval:
1. Repoint/duplicate the systemd user unit so loop-factory's estate watchdog actually cycles, and add the estate deadman check.
2. Drift remediation: reconcile or re-pin both departments per `runbooks/process-change-qa.md`, and wire the drift check into the daily cadence.

Cheap, reversible, and the v2 telemetry/graph work then lands on a truthful baseline. Systemd unit changes touch host config → named explicitly at execution time for your approval.

## Resolution

**Yes** (Ankit, 2026-08-02). Two implement tasks dispatched immediately, each in its own worktree with its own PR (Ankit merges):
- `fix-estate-watchdog` (codex, branch polly/fix-estate-watchdog): repo-local non-colliding systemd user unit for loop-factory's estate cycle + estate deadman check (stale-heartbeat alarm, poisoned-registry self-test, false-green alarms) + print-first installer. No live systemctl actions by the agent.
- `wire-drift-check` (claude_code, branch polly/wire-drift-check): drift/QA check wired into the recurring cadence (alarm, never auto-remediate) + per-artifact reconciliation report for all 16 diverged artifacts with re-pin vs revert recommendations. Actual re-pin is a post-merge owner action.
Cross-vendor review follows each PR.
