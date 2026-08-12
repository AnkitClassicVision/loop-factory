# 015 — Reconcile the three converged v2 spines

Status: OPEN (executing) · Type: task (polly-driven) · Claimed: polly 2026-08-02 · Blocked by: —

## Question

PRs #11 (graph runner), #12 (kernel telemetry), and #13 (owner's ringer lane: runrecord/runs-v2, eval registries, selfheal + podcast heal chain, all-loops board) merged file-disjoint but semantically split. Unify into ONE spine before the Revenue department is born (Wave 2 HELD on this per convergence scout verdict).

## Scout findings (2026-08-02, evidence in registry + scout report)

- CRITICAL: #11 runner escalations never reach manager/outbox.
- HIGH: one execution fragments across runs.jsonl / runs-v2.jsonl / telemetry.jsonl / scores.jsonl (no shared run identity).
- HIGH: board (direct department reads) and rollup.sqlite3 are independent projections that can disagree.
- HIGH: heal plurality — factory/selfheal.py (unwired) vs factory/heal_ladder (existing authority) vs podcast's heal_select/apply/verify chain (`|| true` swallowing, verify-can-pass-without-repair).
- MEDIUM: eval_registry.yaml (policy) and scores.jsonl (evidence) disconnected; podcast registry unconsumed; golden-set not wired.
- Combined master gate CHECK PASS (orchestrator-verified); podcast qa ok:true at 5689cd1.

## Canonical-surface table (scout synthesis honoring owner's own rules — pending owner ratification)

| Concern | Canonical |
|---|---|
| Transition authority | #11 typed graph + signed step receipts |
| Model-call telemetry | telemetry.jsonl (#12) |
| Node/execution summaries | runs-v2.jsonl correlated by graph run_id |
| Evaluator policy | eval_registry.yaml (#13) |
| Durable evaluator evidence | scores.jsonl (#12) |
| Cross-department analytics | rollup.sqlite3 (#12) |
| Board | read-only projection OF the rollup (+ documented direct-read exceptions) |
| Healing | ONE authority: heal_ladder (rec) — selfheal.py folded/retired; propose-only pre-QA |
| Human escalation | manager/outbox, runner escalations bridged in |

## Execution

- Wave R-a (DISPATCHED 2026-08-02): `reconcile-run-identity` (R1+R2, claude_code, polly/reconcile-run-identity) · `reconcile-board-canonical` (R4, codex, polly/reconcile-board). Cross-vendor reviews follow.
- Wave R-b (after R-a merges): R3 eval contract (registry=policy → adapter writes scores.jsonl evidence w/ config version + target run id) + R5 single heal authority (rec: heal_ladder; podcast chain adapts; remove `|| true` swallowing; verify must not pass without an applied repair). R5 heal-authority pick has OWNER VETO standing.
- Wave R-b addition from PR #14 closure (non-blocking capacity risk): projection-export cost is O(retained graph runs) under the records fence — add export duration/run-count/bytes metrics + graph-run retention policy BEFORE sustained high-frequency departments; act before p95 export time reaches 25% of the 10s lock timeout.
- Wave R-b additions from PR #15 review (deferred to avoid cross-PR rollup.py conflicts): rollup meta table (rebuilt_at + schema_version; boardfeed staleness then reads artifact metadata not mtime — N1); rollup schema extensions (department autonomy_state + escalations count, incident numeric observed/setpoint — N3/N4/N5); dash the timersense ExecStart so its failure degrades instead of killing the board unit.
- Then Wave 2 (Revenue department) un-HOLDs.

## Live-tree residue (feeds ticket 014)

All 4 dirty tracked files are STILL-PENDING owner work, none superseded by #13: social_daily.sh engine swap (owner adopt/revert decision), 2 ringer check tweaks, manifest-r2.json bootstrap fix.

## Resolution

(pending Wave R-a/R-b completion)
