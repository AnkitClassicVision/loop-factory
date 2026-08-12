# P4 (shadow slice): The Conductor — Lease, Tick, Would-Dispatch Ledger — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic conductor that acquires an exclusive per-department driver lease, senses everything that can stall (manifest verdicts, open incidents past recheck, unanswered human cards, heal proposals awaiting execution, floor freezes), and records the ORDERED next actions it would take — a would-dispatch ledger the owner can read daily and learn to trust. In shadow it dispatches NOTHING; cutover (conductor replaces the shell as driver) is a later owner action with the shell timer as rollback.

**Architecture:** Phase P4 shadow slice of `docs/superpowers/specs/2026-08-05-loop-brain-reconcile-design.md` (C4). `kernel/lease.py` provides the exclusive driver lease (create-no-replace file + TTL, expired-lease takeover, loud refusal). `factory/conductor.py` runs one tick: lease → sense → prioritize (unblock > floor-gap > routine, with aging) → write `state/conductor-shadow.jsonl` + heartbeat. The estate deadman learns to watch the conductor's heartbeat when adopted. The podcast daily chain gains the conductor tick as its LAST node (observer position — it reads everything the run produced).

**Tech Stack:** Python 3 stdlib, pytest, bash.

## Global Constraints

- SHADOW: the conductor never launches a process, never writes outside `state/`, never touches gateways, caps, or floors. Its output is a decision LEDGER, not actions. Any dispatch capability is a later phase behind the promotion ladder.
- Exactly-one-driver is the invariant this phase must PROVE: two concurrent tick attempts → one lease holder, one loud refusal receipt. No silent second driver, ever.
- Deny-by-default: unreadable inputs are sensed as `unknown` items in the ledger (visible), never skipped; a lease that cannot be acquired or released cleanly is a loud failure (exit 1), never a retry loop.
- No PII in decisions: decisions reference fingerprints, node names, run ids, card ids — never message bodies or names.
- Watch every new test fail RED first. Runtime/shell/roster change → re-pin at integration.

## Frozen interfaces

`kernel/lease.py`:

- `acquire(state_dir, *, holder: str, ttl_s: int, now=None) -> Lease` — creates `<state>/driver.lease` with `O_CREAT|O_EXCL` containing `{holder, acquired_at, expires_at, nonce}`. If the file exists: unexpired → raise `LeaseHeld` (carrying the current holder string); expired → atomic takeover via replace, recording `takeover_from` in the new lease. `Lease` dataclass: `.path, .holder, .nonce, .expires_at`.
- `release(lease) -> None` — removes the file ONLY if the nonce matches the file's current nonce (a stale holder must not delete a successor's lease); mismatch → `LeaseHeld`.
- `refusal_receipt(state_dir, *, loser: str, holder: str, now=None) -> Path` — appends one line to `<state>/lease-refusals.jsonl`; every refused acquisition is durable evidence for the arbitration proof.

`factory/conductor.py`:

- `tick(dept_dir, state_dir, *, holder="conductor", now=None) -> dict` — result `{"run_id": <newest manifest run id or None>, "decisions": [...], "held_lease": bool, "refused_by": str|None}`. On `LeaseHeld`: writes the refusal receipt, returns `held_lease: False, refused_by: <holder>`, exit path is SUCCESS (a refused shadow tick is correct behavior, loudly recorded).
- Sense sources (each unreadable source appends a `{"kind": "unknown_source", "source": <name>}` decision instead of crashing):
  1. newest run-manifest verdict red → one decision per missing/unexpected/duplicate node: `{"kind": "unblock", "action": "rerun_node", "node": ..., "run_id": ...}`.
  2. `incidents.json` entries with state `open`/`department_defect` whose `last_escalated_at` (or newest ts field present — READ the real file shape first) is older than 48h → `{"kind": "unblock", "action": "re_escalate", "fingerprint": ...}`.
  3. approval queue rows `pending_approval` older than 24h → `{"kind": "unblock", "action": "remind_owner", "card": <row id/fingerprint>}` (one per card, deduped).
  4. `heals.jsonl` rows with `result: "proposed"` and no later matching `applied`/`verified` row → `{"kind": "routine", "action": "apply_heal_shadow", "fingerprint": ..., "playbook": ...}`.
  5. newest floors-history line status `frozen` → `{"kind": "floor_gap", "action": "repair_floor_inputs", "reason": ...}`; status `ok` with non-empty changes → `{"kind": "floor_gap", "action": "review_floor_move", "stages": [...]}`.
- Priority: all `unblock` first, then `floor_gap`, then `routine`; WITHIN a class, oldest evidence first; AGING: any decision whose identical `(kind, action, key)` appeared in the previous 3 ticks' ledgers rises one class (routine→floor_gap→unblock) so nothing waits forever. Deterministic tie-break by sorted key.
- Ledger: append `{"ts", "holder", "run_id", "decisions", "refused_by"}` to `state/conductor-shadow.jsonl`; write `state/conductor-heartbeat.json` (`{"ts", "epoch": prior+1}`) on every SUCCESSFUL held tick.
- Budget dry-run: before writing the ledger, compute `would_reserve = len(decisions)` and read the charter's `budget.weekly_ceilings.model_calls` via charter_loader thresholds; if `would_reserve > 0.2 * ceiling` add a `{"kind": "unknown_source", "source": "budget_headroom_check"}`-style warning decision `{"kind": "unblock", "action": "halt_and_review_budget"}` as the FIRST decision. (Shadow never spends; this pins the reserve-before-dispatch shape.)

Podcast node `departments/podcast/runtime/conductor_tick.py`: CLI `--state-dir --dept-dir --shadow`; calls `factory.conductor.tick(holder="conductor-daily")`; always `runrecord.emit_record` (status ok unless crashed — node health, not finding health; emission failure fatal); exit 0 on held OR refused tick (both are correct shadow outcomes), 1 on crash.

`factory/estate_deadman.py`: adoption-gated addition — for each registered department whose state dir contains `conductor-heartbeat.json`, a heartbeat older than 26h appends the standard deadman escalation (mirror the existing estate-manager staleness block's style exactly; READ it first). No heartbeat file = not adopted = no check.

compare transitions: none this phase — the conductor is an observer; its ledger is read by the owner and (next phase) the manager. Deliberately no new observation sensor: the shadow ledger must prove itself useful before it feeds alarms.

## Tasks

### Task 1 (lane A): lease + conductor + podcast node + tests

**Files:**
- Create: `kernel/lease.py`, `factory/conductor.py`, `departments/podcast/runtime/conductor_tick.py`
- Test: `tests/test_lease.py`, `tests/test_conductor.py`, `departments/podcast/tests/test_conductor_tick.py` (all create)

- [ ] Failing tests first, then implement:
  - lease: acquire happy path (file content shape); second acquire unexpired → LeaseHeld + refusal_receipt row; expired takeover records takeover_from; release with wrong nonce → LeaseHeld and the file survives; ARBITRATION PROOF: two sequential acquirers with the same now → exactly one holds, one durable refusal row exists.
  - conductor: refused tick returns refused_by and writes NO heartbeat; red-verdict fixture → rerun_node unblock decisions; stale incident fixture → re_escalate; pending approval >24h → remind_owner; proposed-without-applied heal → apply_heal_shadow routine; frozen floors → repair_floor_inputs floor_gap; priority ordering asserted across a mixed fixture (unblock before floor_gap before routine, oldest-first inside a class); aging promotes a 3-tick-repeated routine decision; unreadable incidents.json yields unknown_source decision, not a crash; heartbeat epoch increments only on held ticks; ledger line shape.
  - node: emits record with node conductor_tick; refused tick still exits 0; crash exits 1.
- [ ] Green gate: `PYTHONPATH="$PWD" python3 -m pytest tests/test_lease.py tests/test_conductor.py departments/podcast/tests/test_conductor_tick.py -v`.

### Task 2 (lane B): deadman coverage + wiring + static pins

**Files:**
- Modify: `factory/estate_deadman.py` (adoption-gated conductor heartbeat check)
- Modify: `departments/podcast/runtime/podcast_daily.sh` (append the conductor tick invocation as the LAST node line, after the board regeneration section: `python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/conductor_tick.py" --shadow --state-dir "${STATE_DIR}" --dept-dir "${REPO}/departments/${DEPARTMENT}"`)
- Modify: `departments/podcast/runtime/run-roster.json` (append node `conductor_tick`, required true, next ordinal — no renumbering needed at the tail)
- Test: `tests/test_estate_deadman_conductor.py` (create), extend `departments/podcast/tests/test_daily_failclosed.py` (static pin: conductor_tick is the LAST runtime node invocation in the script)

- [ ] deadman tests: department with fresh heartbeat → no escalation; stale (27h) → escalation row in the deadman's outbox format (copy the existing test file's fixture style for estate deadman if one exists — search tests/ for estate_deadman); no heartbeat file → no check. RED first, implement, green including any existing deadman tests.
- [ ] NOTE: test_run_roster.py's required-source check will fail in THIS worktree for conductor_tick (the node file is lane A's); acceptable only for exactly that assertion — state it in your summary. bash -n must pass.

### Task 3 (coordinator): integrate, N14, surface, map, shadow, re-pin, arbitration smoke

- [ ] Apply patches; full check + podcast suite; N14 (`runtime/conductor_tick.py`) into SG-WATCHDOG; regenerate surface; map row (shadow observer contract, exactly-one-driver invariant, would-dispatch ledger location); validate.
- [ ] LIVE ARBITRATION SMOKE: run two conductor ticks concurrently against the real state dir (`&` + wait); assert exactly one heartbeat epoch increment and one lease-refusals row for the loser. This is the scheduler-uniqueness proof the red-team demanded, executed against production state in shadow.
- [ ] Shadow run of the daily chain; inspect `state/conductor-shadow.jsonl` first ledger line — its decisions must reconcile with today's known state (open incidents count, pending cards). Spot-read it fully.
- [ ] Re-pin, qa, commit, ledger checkpoint. Cutover stays owner-gated and is NOT this plan.

## Self-Review Notes

- Spec C4 minus dispatch: dispatch/verify/advance requires promoted action classes and the kernel worker gateways — deliberately behind the shadow trust period and the owner's cutover decision. The tick's sense→prioritize→record core, the lease, and the deadman coverage are the load-bearing pieces proven here.
- The would-reserve budget check pins the reserve-before-dispatch SHAPE without spending; real atomic reservation lands with dispatch.
- Aging uses the previous 3 ledger lines — cheap, deterministic, testable; no wall-clock starvation math needed in shadow.
