# Social Department Audit

Standard: `runbooks/ultimate-department-creation.md` (ratified 2026-08-03). Scope was static, read-only inspection; no department runtime or live service was executed.

## 1. v2 run records

Verdict: **findings**. The Social writer appends a fenced legacy row, but it does not use or validate against `run-record/v2`.

### P1: Social writes legacy records, not the factory v2 contract

Evidence: `departments/social/runtime/record.py:106-160` builds `{node, epoch, timestamp, shadow, payload_summary}` and appends `runs.jsonl`; `factory/runrecord.py:20-65` and `factory/runrecord.py:147-199` require `run-record/v2` plus run identity, release, trigger, engine/auth/cost, status, receipts, approval, and external-action fields; `factory/runrecord.py:228-254` validates before appending `runs-v2.jsonl`.

Impact: Social runs cannot prove shared identity, release provenance, authorization, evaluator/approval state, or zero external actions under the ratified Stage 10 record contract.

Fix: Replace the Social-specific row builder with `factory.runrecord.emit_record`/`append_record`, supplying the shared run ID and actual node receipts.

Priority: P1

Confidence: high

## 2. Objective sensors

Verdict: **findings**. Partial observation adapters exist: Zernio produces per-post metrics and platform verification (`departments/social/runtime/pull_zernio_analytics.py:70-127`); the calendar-export join produces discovery-call counts (`departments/social/runtime/pull_call_joins.py:88-109`). These are not Stage 7 objective adapters.

### P1: Every charter objective is unverifiable under objectives-observed/v1

Evidence: `departments/social/charter.yaml:60-77` declares operational/outcome metrics but no `setpoints.objectives` contracts; `factory/objectives_verify.py:56-59` rejects that shape; `factory/objectives_verify.py:92-134` requires a fresh `objectives-observed/v1` snapshot. No Social runtime file writes `objectives_observed.json`. The call adapter explicitly says its real Calendar/HubSpot wiring is a later seam at `departments/social/runtime/pull_call_joins.py:3-7`.

Impact: Discovery calls, posting volume, engagement, webinar registrations, podcast-post engagement, verified delivery, and quarantine backlog are all unmeasured for the ratified Stage 7 gate, even where lower-level rows exist.

Fix: Convert each charter metric into a full `setpoints.objectives` contract and add an independent adapter that atomically emits the single fresh `objectives-observed/v1` snapshot; keep unavailable measures unknown.

Priority: P1

Confidence: high

## 3. Outbound asks and publish path

Verdict: **findings**. The publish chain itself is executed in the required order: S1/S2/S3 at `departments/social/runtime/social_daily.sh:337-388`, kernel authorization calls S5 then S4 (`departments/social/runtime/kernel_bridge.py:110-123`; `kernel/lock_service.py:144-153`), fresh S6/S7 checks run at `departments/social/runtime/social_daily.sh:471-480`, then N6 dispatches at lines 482-489. Dispatch also rechecks kill/breaker markers immediately before the gateway (`departments/social/runtime/dispatch.py:233-250`).

### P1: Outbound review asks evade the ask schema and have no executed return loop

Evidence: No node in `departments/social/subgraphs.json:19-179` declares `emits_ask`, `return_path`, or `return_sla_hours`, although SG-LEARN N3 queues a human decision. Review-card scripts are merely allowlisted as “MAP DEBT” at `departments/social/subgraphs.json:182-188`. The daily driver creates the external Linear ask after the run, ignores failure, and advances no receipt gate at `departments/social/runtime/social_daily.sh:517-521`. Reader/closer scripts exist (`departments/social/runtime/linear_read_comments.py:51-70`; `departments/social/runtime/linear_close_issue.py:50-66`) but no Social trigger invokes them.

Impact: A card can fail to send or remain unanswered forever without blocking, SLA enforcement, denial/refile, or a returned approval receipt. The validator cannot detect this because the actual ask is absent from the graph.

Fix: Model each ask as an `emits_ask` node with return path/SLA, route creation through the governed outbox, and add a receipt-gated listener that records approve/skip/fix or TTL deny-and-refile.

Priority: P1

Confidence: high

## 4. Fail-closed gating

Verdict: **findings**. Social guards write blocked/missing receipts and return nonzero (`departments/social/runtime/guards.py:685-704`, `departments/social/runtime/guards.py:747-792`); dispatch refuses malformed inputs, missing caps, bad QA/tokens, non-live charter states, and gateway exceptions (`departments/social/runtime/dispatch.py:72-121`, `departments/social/runtime/dispatch.py:197-250`).

### P1: Corrupt nonce-ledger rows are silently discarded

Evidence: `kernel/lock_service.py:58-70` catches malformed consumed/revoked nonce rows and continues with no distinction between a torn trailing line and corruption of an earlier durable consumption.

Impact: After restart, corruption of a consumed receipt row can remove replay evidence on the highest-blast-radius publish path, contrary to deny-by-default record handling.

Fix: Tolerate only one provably torn final line; reject startup on any malformed non-final row and emit an incident.

Priority: P1

Confidence: high

## 5. Receipt-gated steps in social_daily.sh

Verdict: **findings**. `run_step` blocks on missing, malformed, blocked, failed, or error receipts before continuing (`departments/social/runtime/social_daily.sh:66-148`), and the main publish stages use it.

### P2: Terminal yield and review-card stages escape the receipt chain

Evidence: A cap yield exits before delivery verification and N9 record at `departments/social/runtime/social_daily.sh:491-515`. N10 review-card creation bypasses `run_step` and explicitly ignores failure at lines 517-521.

Impact: Valid cap yields disappear from canonical run history, and an outbound human gate may fail without summoning the manager. “Every stage emits a receipt” is therefore false.

Fix: Record a terminal `skipped/yielded` v2 run before exit, and run the review ask through the same receipt gate with manager escalation on failure.

Priority: P2

Confidence: high

## 6. Manager liveness

Verdict: **findings**.

### P1: The independent manager timer exists but is deliberately uninstalled

Evidence: The service/timer comments state they are never auto-enabled and require manual link/enable (`departments/social/runtime/systemd/loop-factory-social-manager.service:10-14`; `departments/social/runtime/systemd/loop-factory-social-manager.timer:1-15`). The incident analysis confirms `social_daily.sh` never cycles the manager and the units remain outside systemd until linked (`docs/drift-reconciliation-2026-08-02.md:131-153`). The estate registry still has `schedule: TODO_F1` at `estate/registry.d/social.yaml:1-8`.

Impact: No independent process advances Social’s manager heartbeat or notices a dead worker, explaining a stale heartbeat beginning 2026-07-28.

Fix: After owner review, install and enable the dedicated manager unit, then prove two advancing epochs while the worker is stopped; update the registry schedule.

Priority: P1

Confidence: high

## 7. Stage 8 cadence contract

Verdict: **findings**.

### P1: Social has no compiled Stage 8 cadence/alert contract

Evidence: Stage 8 requires a trigger, concurrency/catch-up, alert classification, dedupe/cursor, digest/cooldown, escalation, and dry-run gate (`runbooks/ultimate-department-creation.md:41-43`). `factory/cadence.py:49-128` defines that contract. Social contains only a manager timer and the registry placeholder `schedule: TODO_F1` (`estate/registry.d/social.yaml:5`); no Social cadence contract or rendered worker timer exists.

Impact: Worker wake policy, overlap/catch-up behavior, alert routing, deduplication, digest limits, and activation proof are unspecified and cannot pass Stage 8.

Fix: Add the Social cadence contract, run `loopfactory.py cadence check` and triage installer dry-run, review the exact disabled units, then activate only by owner action.

Priority: P1

Confidence: high

| dimension | verdict | worst finding priority |
|---|---|---|
| v2 run records | findings | P1 |
| objective sensors | findings | P1 |
| outbound asks and publish | findings (publish guard execution clean) | P1 |
| fail-closed gating | findings | P1 |
| receipt-gated daily stages | findings | P2 |
| manager liveness | findings | P1 |
| Stage 8 cadence | findings | P1 |
