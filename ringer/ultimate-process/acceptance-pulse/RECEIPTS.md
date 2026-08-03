# pulse acceptance run — stage receipts

Receipt-gated per the ratified runbook: no stage advanced without the receipt
below. Executed evidence only.

| stage | receipt | evidence |
|---|---|---|
| 1 Destination | owner signed | Ankit selected "Signed" (Claude Code AskUserQuestion, 2026-08-03); destination + binary exit test in creation/map.md |
| 2 Route | frontier empty | creation/map.md + tickets D1, D2 both resolved; fog: none (recorded honestly, not manufactured) |
| 3 Creation Contract | `loopfactory.py creation-contract check` exit 0 | "PASS creation contract" on creation/department-creation-contract.yaml — 8 authorities named, no blocking open questions, all F1 answers sourced |
| 4 Scaffold | scaffold receipt JSON | registry_file estate/registry.d/pulse.yaml created; skeleton dirs present; second-run charter-overwrite refusal by design |
| 5 INTENT LOCK | owner signed | 4 verbatim confirmations + INTENT LOCKED (AskUserQuestion readback); artifact interview/intent-interview.md with skip rationales |
| 6 F2 govern | `loopfactory.py validate --name pulse` exit 0 | charter ok:true, maps ok:true; emits_ask node carries return_path+return_sla_hours (new invariant exercised on a fresh department) |
| 7 Objectives | gate deferred to post-cycle | charter objectives digest_coverage + ask_return_integrity locked; `loopfactory.py objectives --name pulse` runs after the first shadow cycle writes observations (Stage 10/11) |
| 8 Cadence | `loopfactory.py cadence check` exit 0 | units rendered to departments/pulse/systemd/ — OnCalendar=*-*-* 07:00:00, Persistent=false, zero systemctl, enabled_by_default false; never installed |
| 9 Runtime build | Ringer check exit 0 | run p3854141 (codex, worktree) PASS attempt 1 — 13 fixture tests, patch 30267 bytes, validate PASS inside the gate |
| 10 Assemble | check + validate + cycles green | full `loopfactory.py check` PASS; two hand-driven shadow cycles: 8 run records ALL validate run-record/v2, external_actions_taken=0; digest folds both readable notes; ask drafted to outbox (no dispatch); RETURN PATH EXECUTED — reply harvested, clarification folded; objectives gate `OBJECTIVES_VERIFY_OK pulse` exit 0 (Stage 7 receipt); manager epoch 0 green; estate watched=3 (sees pulse) |
| 11 Prove + pin | prove + pin + qa exit 0 | strict `prove` rc=1 with 6 REAL drills PASS + 5 honestly unsupported (auth-block, worker-kill, evaluator-rejection, board-truth, drift — no fixture APIs yet; recorded, never skipped); `--allow-unsupported` rc=0; release pinned+flipped f08f08992dfd3490 intent_locked; `qa` rc=0 zero mismatches |
| 12 Incidents + delete | both incidents routed; clean deletion | Incident A (proposal lane): post-pin drift injected → `qa` rc=1, reverted → rc=0 — the unsupported drift drill HAND-EXECUTED per the calibration rule. Incident B (human lane): stale ask past 48h SLA → sensor wrote ask_return_integrity=1 → gate rc=1 "exceeds maximum 0" → `hil escalate` row landed in decisions_outbox.jsonl. Department deleted; estate no longer watches it; factory check PASS after removal |

## Verdict

The ratified 12-stage process created, governed, proved, and deleted a real
department in one uninterrupted run. Known gaps recorded honestly: five prove
drills lack factory fixture APIs (future factory work), and Stage 12 burn-in
(14 shadow days) is out of scope for a throwaway by design.
