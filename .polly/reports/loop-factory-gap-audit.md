# loop-factory Ground-Truth Gap Audit — 2026-08-02

Read-only audit against the target "fully autonomous, self-healing, observable loops."
Every claim cites file:line or command output. Repo: /mnt/d_drive/repos/loop-factory @ master (3b7eaf9, working tree dirty).

Test suite executed: `python3 loopfactory.py check` → compileall clean, **138 passed in 4.21s, CHECK PASS** (only writes: `__pycache__`/`.pytest_cache`).

---

## PART 1 — What actually exists

### loopfactory.py (250 lines, one CLI)

10 subcommands, all real and deterministic (loopfactory.py:4-19, 186-246):

| cmd | what it really does |
|---|---|
| scaffold | F0 skeleton + charter template + estate registry partition (factory/scaffold.py:124-186) |
| interview | writes the intent-interview artifact from template; the interview itself is agent-run, lock is human (loopfactory.py:47-69) |
| validate | charter load + guard-matrix lint + traceability (loopfactory.py:72-87) |
| release pin/verify | content-addressed release; pin is GATED on valid charter + clean maps, stamps `intent_locked` (loopfactory.py:96-117) |
| manager | one Sense→Compare→Decide→Record cycle (factory/manager.py) |
| estate | one watchdog cycle over the registry + on-disk reality scan (loopfactory.py:135-144) |
| heal | record failure/success on the heal ladder (factory/heal_ladder.py) |
| hil | approval queue push/apply/escalate bridge (factory/human_in_the_loop.py) |
| qa | lint + traceability + release drift (factory/graphs.py:217-238) |
| check | compileall + pytest (loopfactory.py:167-178) |

### factory/ (13 modules, all functioning, all tested)

- **charter_loader.py** — fail-closed charter validation: required keys, valid autonomy states, non-empty invariants, dir-name match (charter_loader.py:48-78); engine_allowlist and max_edit_rounds with NO fallback (100-127); human-gate floor that a charter cannot lower (130-136). Mature.
- **manager.py** — deterministic department manager. Whitelisted verbs, shadow subset, unknown verbs escalate at every autonomy level (297-333); immutable-invariant guard raises (310-314); charter overrides CLI flag (566-575); records runs→brief→STATE(atomic,epoch)→heartbeat (433-476); undelivered escalations surfaced, never counted as sent (424-431). Mature.
- **estate_manager.py** — watchdog over managers: dead_manager (>26h), alive_not_working (epoch stall), registry drift both directions (95-130); renders Control Room HTML + brief; v1 escalates only, no park (docs/KNOWN-LIMITS.md:78-82). Code mature; deployment is the gap (see item 11).
- **heal_ladder.py** — real bounded state machine L1→L2→L4→parked, flap/oscillation detection, min observation window, immutable-invariant refusal (25-146). Mature but **nothing invokes it** (see gap #3).
- **graphs.py** — guard-matrix lint (S1..S8 ordering before dispatch), traceability incl. path-escape refusal (156-162), drift vs pinned release (190-214). Honest about scope: metadata lint only, runtime mediation is the real guarantee (17-22).
- **release.py** — content-addressed release pinning over all behavior-bearing artifacts, atomic `current` flip, model-free verify (26-114); calibration log of human cost (117-132).
- **memory.py** — local-first records; S3/OpenBrain backends are deliberate fail-closed seams: `ship()` raises BackendNotWired (125-148).
- **human_in_the_loop.py** — approval packets to outbox, strict APPROVE/REJECT verdict, replay no-op, hook failure visible (66-106); escalate() packets (126-144).
- **launch.py** — applies capability confinement: allowlist env, no-credential assert, capability-bearing commands restricted to python3 scripts inside the department's runtime/ (70-119).
- **outbox_push.py / outbox_listen.py** — outbox rows → Telegram ping + Linear card with cursor+hash dedup (298-301) and card ledger; listener parses APPROVE/SKIP/FIX from card comments, records decisions, acks/closes (314-405). Scheduled via social-decisions.timer (every 15 min) and social-escalations.timer.
- **scaffold.py, estate_registry.py** — working; registry parser fail-closed with duplicate-id refusal (estate_registry.py:78-100).

### kernel/ — effect gating that actually exists

- **receipts.py** — HMAC-signed receipts bound to action_class + binding hash + TTL + single-use nonce; verify checks signature, binding, expiry, revocation, replay (66-125). LocalSigner requires non-empty key (20-28); KMSSigner is a NotImplemented seam (38-46).
- **lock_service.py** — server clock (not caller-supplied), durable fsync'd nonce-consumption and revocation ledgers (48-86), frequency slot reserved INSIDE send issuance (133-142), budget reserved inside model issuance with mandatory sanitized attestation (144-161), gateway error → LockServiceDown refusal, never allow (165-188).
- **gateways/dispatch.py** — **live sends are impossible**: `if live: raise GatewayDenied("no wired real adapter in kernel v1")` (dispatch.py:61-62). Shadow writes to a sink file only.
- **gateways/budget.py** — reservation ledger, hard ceiling + review at 80%, telemetry failure → BudgetExceeded (55-87). `commit(actual)` exists (89-92) but no runtime caller commits actuals.
- **gateways/frequency.py** — 3 touches/person/30d, 1 person/org/7d (12-58).
- **gateways/read_broker.py** — field minimization: raw-field denylist, sensitive quarantine, email/phone redaction, >240-char and non-scalar quarantine (12-53).
- **capabilities.py** — env ALLOWLIST (not denylist), per-charter capability widening only (24-84).
- Honest limits ledger: kernel is in-process, sanitization attested not proven, LocalSigner shadow-tier, single-kernel-process assumption (docs/KNOWN-LIMITS.md:10-41). All flagged go-live blockers.

### templates/, runbooks/, interview/

templates: concept-map, procedural-graph, subgraphs.json, intent-interview, runbook, skill, department_daily.sh, systemd service+timer (dir listing). runbooks: factory-pipeline.md, process-change-qa.md, promotion-ladder.md. interview: INTERVIEW.md, QUESTION_BANK.md. All present; runbooks are human procedure docs (by design).

### departments/ — two stood, different states

**podcast** — LIVE. Charter v1.0 intent-locked 2026-07-22 (charter.yaml:1-11), shadow, capability `systemd_user_probe`. 8 pinned releases, current=77a3e3d095f3cc07. Runtime: 7-node watchdog chain + DAG supervisor + manager + HIL push (podcast_daily.sh, 49 lines), every node launched through factory/launch.py confinement. **Running every 30 min** via podcast-loop.timer (`OnCalendar=*:00/30`); heartbeats up to 2026-08-02T18:30 UTC, STATE epoch 1492. Fenced record writer with flock + epoch sequence enforcement + fsync (runtime/record.py:33-171). Heal lane nodes (heal_select/apply/verify) + dept tests exist.

**social** — BUILT, STALLED. Charter locked, engine_allowlist [codex_oauth, claude_subscription, glm_oauth] (charter.yaml:92). Full pipeline: inventory→select→brand→sanitize→draft→QA(≤3 tries/round, cross-model)→dispatch(simulated)→verify→record, receipt-gated per step (social_daily.sh:123-148). Per-run receipt dirs under state/receipts/. 2 releases, current=ac07729f5044185d. **Last run 2026-07-31T13:02** (runs.jsonl), manager last cycled 2026-07-28 (STATE.json). social-loop.timer exists (daily 13:00 UTC, installed disabled-by-convention); nothing has run it for 2+ days and nothing noticed.

### estate/ — registered but the watchdog doesn't run for this repo

registry.d: podcast.yaml + social.yaml (valid, both `schedule: "TODO_F1"`). estate/state/STATE.json: **epoch 1, last cycle 2026-07-28**, with an open `dead_manager` breach. The installed `estate-manager.service` points at a DIFFERENT repo (`/mnt/d_drive/repos/open-engine/.../open_engine_estate_manager.py`, WorkingDirectory=open-engine). loop-factory's own estate watchdog is real, tested code that is not scheduled anywhere.

---

## PART 2 — 15-item gap scorecard

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Versioned factory definitions | **MISSING** (dept layer: partial) | Departments are content-address versioned (release.py:66-81; 8 podcast releases) and charters carry `version:` (podcast charter.yaml:7). The FACTORY recipe itself has no version artifact, no v1-vs-v2 comparison; its only versioning is git history. Calibration log (release.py:117-132) feeds a future emitter decision but no factory release exists. |
| 2 | Explicit run-level state machine | **EXISTS-partial** | Real FSMs: heal ladder L1→L2→L4→parked (heal_ladder.py:95-146), approval statuses pending→approved/rejected/approved_hook_failed (human_in_the_loop.py:83-106), autonomy states validated (charter_loader.py:41,66-70), epoch fencing (record.py:120-127). But a pipeline RUN has no explicit state object; it is sequential bash where state = receipts + exit codes (social_daily.sh:123-148). No resumable run state, no "step 4 of 9" record. |
| 3 | Idempotency / duplicate-trigger protection | **EXISTS-partial** | Kernel: durable single-use nonces, fsync-before-effect (lock_service.py:48-86; receipts.py:116-121). HIL: replayed decision_id is a no-op (human_in_the_loop.py:90-92). Outbox push: cursor + row-hash dedup (outbox_push.py:298-301). Release pin idempotent (release.py:66-68). Podcast: flock + strict epoch sequence refuses interleaved writers (record.py:33-56,118-127). Gap: neither daily orchestrator holds a run-level lock (`grep flock *_daily.sh` → none); a double timer fire relies on the record fence (podcast) or nothing structural (social). |
| 4 | Evaluators, deterministic-first | **EXISTS-working** | Charter loader fail-closed (charter_loader.py:48-78). Map lint + traceability + escape checks (graphs.py:66-187). Per-step receipt schema check: missing/invalid receipt → incident + exit 2 (social_daily.sh:130-143). qa_post.py: deterministic + cross-model defect enumeration (qa_post.py:1). Drafts hard-checked: body caps, single CTA, ungrounded-number detection (draft_post.py:349-410). `external_actions_taken: []` recorded per run (podcast runs.jsonl escalate_outbox row). DAG receipt hash + staleness + silent-skip findings (dag_supervisor.py:55-119). |
| 5 | Retry limits / policy per step | **EXISTS-partial** | Heal ladder caps: MAX_L1=2, MAX_L2=2, MAX_L4=2, MAX_FLAPS=4 (heal_ladder.py:32-35). QA engine-unavailable retry ≤3 (social_daily.sh:150-175). Charter caps edit rounds via qa_shape.max_edit_rounds, no fallback (charter_loader.py:117-127). Gap: retry policy is hardcoded per call-site, not a charter-driven per-step contract, and the heal ladder is not invoked by any running loop (see #11). |
| 6 | Approval boundaries before external actions | **EXISTS-working** (shadow-proven only) | Human-gate floor cannot be lowered by charter (charter_loader.py:130-136). Live dispatch refused outright (dispatch.py:61-62). Manager verbs whitelisted; gated-live verbs escalate in shadow; unknown verbs escalate at EVERY level (manager.py:297-333). HIL requires exact APPROVE/REJECT (human_in_the_loop.py:83-85). Caveat: no real adapter has ever exercised the gate under live conditions, by design. |
| 7 | OAuth/auth expiry: block, don't switch billing | **EXISTS-partial** | Engine failure → SourceUnavailable, step blocks; there is no code path that swaps to another engine (draft_post.py:172-181; engine explicitly named per invocation, allowlist enforced 139-146). API lanes forbidden by charter policy (scaffold.py:55-57; social charter.yaml:92). Gap: auth expiry is indistinguishable from any nonzero exit; no credential-health preflight, no auth-specific diagnosis; QA collapses it to `qa_engine_unavailable` after 3 tries (social_daily.sh:96-116,150-175). It blocks correctly but cannot tell you WHY. |
| 8 | Model/auth route visibility per step | **EXISTS-partial** | Draft artifacts record `engine` + `round` (draft_post.py:349-356). Charter names the allowed auth classes (engine_allowlist). Gap: run receipts in runs.jsonl carry no engine/model/auth field (record.py:130-136); kernel budget ledger counts `model_calls` without engine identity (budget.py:84-87). You cannot reconstruct per-step "which model, which auth class" from the run record. |
| 9 | Department isolation | **EXISTS-working** | Env allowlist, everything else dropped (capabilities.py:24-31,76-84); launcher applies + asserts + restricts capability commands to the dept's own runtime/ (launch.py:70-119); impl paths may not escape the department (graphs.py:156-162); per-dept kernel ledgers under own state dir (bridge.py:27-46); systemd hardening on the running unit: ProtectSystem=strict, IPAddressDeny=any, ReadWritePaths scoped (podcast-daily-department.service). Known limit: kernel is in-process, real boundary is operational (KNOWN-LIMITS.md:10-19). |
| 10 | Evidence receipts (schema/tamper/replay) | **EXISTS-partial** | Kernel action receipts: signed, binding-hashed, TTL'd, single-use, durably revocable — genuinely tamper-evident and replay-proof (receipts.py:66-125; lock_service.py:88-188; tested in test_kernel_receipts/hardening). Release manifests sha256-pinned (release.py:41-102). DAG projection hash-verified (dag_supervisor.py:69-80). Gap: STEP receipts are plain unsigned JSON files (state/receipts/*/N1-*.json sample; record.py rows unsigned) — anything with disk write can forge one. KNOWN-LIMITS.md:73-77 admits QA-success receipts are trusted from the caller. |
| 11 | Watch loops real? | **EXISTS-working code, half-deployed** | Manager: real, tested, RUNNING (podcast heartbeats every 30 min through 2026-08-02T18:30, epoch 1492). Estate watchdog: real, tested code (estate_manager.py:95-130) but NOT scheduled for this repo — estate/state epoch 1, last cycle 2026-07-28; the installed estate-manager.service targets the open-engine repo instead. Live consequence: social loop dead since Jul 31 and its manager dead since Jul 28, and nothing has escalated either. |
| 12 | Drift review | **EXISTS-working — and currently firing unheeded** | check_drift compares live tree to pinned release (graphs.py:190-214); registry drift both directions (estate_manager.py:104-128). Executed today: BOTH departments report drift (podcast: 6 mismatched artifacts vs 77a3e3d0; social: 10 vs ac07729f — `loopfactory.py qa` output). Gap: the daily loop never runs the drift check (podcast_daily.sh has no qa step), and the estate cycle that would surface it isn't scheduled, so drift only alarms when a human runs `qa`. |
| 13 | Kill switches / pause | **EXISTS-partial** | Per-dept kill_switch documented in registry (`systemctl --user disable --now <dept>-loop.timer`, registry.d/*.yaml). Heal park is terminal and human-only unpark (heal_ladder.py:136-143). Manager throttle/park verbs exist but are gated-live-only and redirect to escalation in shadow (manager.py:74-79,324-330). Estate park verb NOT built (KNOWN-LIMITS.md:78-82). Kill today = a human runs systemctl; no tested one-command kill, no automated pause path. |
| 14 | Backup and restore | **MISSING** | All run/state truth is gitignored (`.gitignore`: departments/*/state/, estate/state/, state/). Durable backends are deliberate unwired seams that raise BackendNotWired (memory.py:125-148). No restore tooling or runbook anywhere in the repo. Sole durability: local disk + fsync. A disk loss erases every run record, receipt, ledger, and approval queue. |
| 15 | System-of-record ownership | **EXISTS-partial** | Documented in code: runs.jsonl authoritative, STATE.json a cache (memory.py:19-20); charter = governance truth, read-only to the dept (charter_loader.py:1-9); registry = estate truth (estate_registry.py). Gaps: task truth spans approval_queue.jsonl + decisions_outbox + card_ledger + Linear with ownership only in comments; podcast run truth is split with the external /mnt/d_drive/repos/podcast pipeline (podcast_daily.sh:33 reads its receipts); memory truth beyond local disk is explicitly unowned (seams). |

---

## PART 3 — Telemetry

**Per-node-run tracking of tokens, cost, latency, model used, eval score: NONE.** `grep -rn "tokens|latency|duration_ms|elapsed|cost"` across factory/, kernel/, and both departments' runtime returns zero telemetry fields; the only hits are the human-cost calibration log (release.py:117-132) and unrelated string tokens.

What exists today:

| Signal | Where | Shape |
|---|---|---|
| Run receipt | departments/*/state/runs.jsonl | `{node, epoch, timestamp, shadow, payload_summary}` (record.py:130-136) — no engine, no duration, no tokens |
| Manager tick | runs.jsonl + STATE.json + heartbeats.jsonl | findings codes + escalation counts (manager.py:437-476) |
| Budget | <state>/kernel/budget.jsonl | reservations of model_calls/dollars/worker_minutes (budget.py:84-87); `commit(actual)` exists (89-92) but nothing calls it, so actuals are never recorded |
| QA result | state/receipts/<run>/N5-qa-*.json | defect list, no score |
| Engine used | draft artifact only | `engine`, `round` (draft_post.py:349-356) |

**Natural attach points, in order of leverage:**
1. **`record.write_record` payload** (podcast record.py:101-136) and the social receipt files: add `engine`, `auth_class`, `duration_ms`, `tokens_in/out`, `exit_code` per step. Every step already flows through one recorder with a fenced epoch, so this is one struct change.
2. **Kernel budget ledger commit()** (budget.py:89-92): callers should commit actuals after each model call; the ledger and the manager's budget sensing (manager.py:173-183, 259-266) already exist and would light up for free.
3. **Heartbeat rows** (manager.py:464-476) for cycle-level latency, keeping per-step detail in runs.jsonl.

---

## Ranked top-10 material gaps vs "fully autonomous, self-healing, observable"

1. **The estate watchdog does not run for this estate.** Real, tested code; last cycled 2026-07-28 (epoch 1); the installed estate-manager systemd unit targets the open-engine repo instead of loop-factory. The proof of the cost is live: social's loop has been dead since Jul 31 and its manager since Jul 28, with zero escalation. Self-healing cannot start if the layer that notices death is offline.
2. **Self-healing is built but unwired.** heal_ladder.py and the podcast heal nodes (heal_select/apply/verify) pass tests, but no script, timer, or manager path invokes them (grep: only map/release-manifest references). Today a node failure produces an escalation packet at best; it never enters L1 retry, L2 ringer heal, or L4 change card automatically.
3. **No per-step telemetry at all.** No tokens, cost, latency, model, or auth class on any run record; the budget ledger only ever sees reservations because no caller commits actuals. "Observable" currently means liveness + findings, not cost or route.
4. **Both departments are running in unremediated drift**, and the daily loop never checks. `qa` shows podcast 6 and social 10 artifacts diverged from their pinned releases. The drift detector works; nothing schedules it, so hard rule 4 (process change = map change + QA) is currently being violated silently.
5. **Live effects are structurally impossible, and the path out is unbuilt.** dispatch live → GatewayDenied (dispatch.py:61-62); KMSSigner NotImplemented; kernel in-process; sanitizer attestation not independent (KNOWN-LIMITS 1-3). Correct for shadow, but "fully autonomous" requires the entire OPS tier (out-of-process kernel, real adapters, KMS) which exists only as seams.
6. **No backup/restore.** State gitignored, remote memory backends fail-closed seams, no restore runbook. One disk failure destroys all run truth, ledgers, and receipts, including the nonce-consumption ledger that replay protection depends on.
7. **Step receipts are forgeable.** Only kernel effect receipts are signed; the per-step receipts that gate pipeline advancement (social_daily.sh:130-143) are plain JSON any local writer can fabricate. Receipt-gated steps (hard rule 8) rest on filesystem trust.
8. **Auth expiry is a blind spot.** Failures block correctly (no billing-route switch is possible in code), but an expired OAuth is indistinguishable from any engine crash; after 3 QA retries it collapses into `qa_engine_unavailable` with no credential-health check or auth-specific escalation.
9. **Route visibility ends at the draft artifact.** Engine recorded only in draft JSON; runs.jsonl and receipts carry no model/auth identity, so per-step route audit (checklist #8) cannot be reconstructed after the fact.
10. **The factory itself is unversioned.** Departments get content-addressed releases; the factory recipe (interview protocol, templates, manager thresholds, guard vocabulary) has no pinned versions and no v1-vs-v2 diff surface, so "which factory built this department" is answerable only by git archaeology.

**Bottom line:** the external review's framing of "gaps everywhere" is wrong on the control plane: gating, isolation, receipts-for-effects, drift detection, watch-loop code, approval boundaries, and deterministic evaluators genuinely exist, are tested (138/138), and one department has been ticking every 30 minutes for days. The real distance to the target is concentrated in five places: nothing watches the watchers (1), healing never fires (2), nothing measures cost/route (3), drift discipline is not enforced on cadence (4), and nothing survives a disk loss (6).
