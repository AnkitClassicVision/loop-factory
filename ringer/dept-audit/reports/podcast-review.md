# Podcast department audit

Standard: `runbooks/ultimate-department-creation.md` (ratified 2026-08-03). Scope was read-only inspection of the requested podcast and factory sources.

## 1. v2 run records

### Finding: Record coverage is mixed, not v2-native end to end
Evidence: The v2 contract requires and validates the complete field set in `factory/runrecord.py:20-65,147-199` and appends only validated rows to `runs-v2.jsonl` at `factory/runrecord.py:228-254`. Core invoked nodes do call it, for example `departments/podcast/runtime/pipeline_sensor.py:158-188`. However, `departments/podcast/runtime/record.py:101-171` still appends the five-field legacy receipt to `runs.jsonl`; `departments/podcast/runtime/comms_reconcile_sensor.py:79-103` emits no record; and daily-invoked rotation returns/prints a loose receipt without a v2 append at `departments/podcast/runtime/rotate_observations.py:102-140`.
Impact: Not every mapped or executed runtime stage produces a schema-validated, identity-bearing v2 record, so Stage 10 cannot prove a complete signed run chain.
Fix: Route the remaining runtime stages through `factory.runrecord.emit_record`; retire `runs.jsonl` as the graph's N9 authority once consumers are migrated.
Priority: P1
Confidence: high

Verdict: **findings**.

## 2. objectives sensors

### Finding: Three ratified objectives are unmeasured
Evidence: The charter declares `hopper_interviews_ready`, `state_drift`, and `unledgered_inbound` at `departments/podcast/charter.yaml:72-90`. The only adapter writing `objectives_observed.json` is `departments/podcast/runtime/hopper_sensor.py:266-319`; its run populates only `hopper_depth` and `publish_reliability` at `departments/podcast/runtime/hopper_sensor.py:353-370`. The verifier treats absent values as honest unknown/failure at `factory/objectives_verify.py:114-124`.
Impact: Stage 7 cannot pass, and the department cannot detect breaches of interview readiness, evidence-ledger drift, or unledgered inbound people.
Fix: Add independent adapters for the three objectives and atomically merge their observations into the existing v1 document with timestamps/freshness evidence.
Priority: P1
Confidence: high

Verdict: **findings**. Measured: `publish_reliability`, `hopper_depth`. Unmeasured: the three objectives above.

## 3. outbound ask classes

### Finding: Escalation asks have no declared or executed answer-return path
Evidence: The charter requires every outbound ask to have both fields and an executed return path at `departments/podcast/charter.yaml:106-112`; the lint enforces these only on nodes marked `emits_ask: true` at `factory/graphs.py:76-85`. `escalate_outbox` constructs a human question and writes the ask at `departments/podcast/runtime/escalate_outbox.py:143-181`, but its node declaration has neither `emits_ask`, `return_path`, nor SLA at `departments/podcast/subgraphs.json:35-40`. The intended reconciliation node is declared at `departments/podcast/subgraphs.json:46-48`, but the daily chain proceeds from `sense_estate` through the other sensors to compare/dedup/escalate without invoking it at `departments/podcast/runtime/podcast_daily.sh:21-44`.
Impact: Human answers can sit unharvested, while map lint reports no missing return contract.
Fix: Mark the ask-emitting node, declare its return component/SLA, and execute a consumer/reconciler in the same governed chain.
Priority: P1
Confidence: high

Verdict: **findings**.

## 4. fail-closed gating

### Finding: Heal proposal failures are explicitly allowed to pass
Evidence: Individual heal code is conservative: unknown or ambiguous selection produces refusal receipts at `departments/podcast/runtime/heal_select.py:120-218`; apply refuses invalid bindings and records command failure at `departments/podcast/runtime/heal_apply.py:210-317`; verification converts probe failure into failed status at `departments/podcast/runtime/heal_verify.py:120-164`. But the orchestrator suppresses selection, parsing, apply, verify, and incident-load failures with `|| true` and continues at `departments/podcast/runtime/podcast_daily.sh:52-70`.
Impact: A failed/refused proposal gate does not block downstream verification or the rest of the run, permitting false advancement despite deny-by-default intent.
Fix: Inspect each receipt/result and halt that incident lane on any non-success; emit one manager-visible v2 failure before continuing unrelated incidents.
Priority: P1
Confidence: high

Verdict: **findings**. No copy-drafting node exists in this department; the finding concerns proposed heal actions.

## 5. receipt-gated steps

### Finding: Shell sequencing checks exit codes, not completion receipts
Evidence: The charter requires proof before the next step at `departments/podcast/charter.yaml:114-121`, and the canonical runbook repeats that missing receipts block at `runbooks/ultimate-department-creation.md:57-58`. The daily chain invokes sensors sequentially and then compare/dedup/escalate at `departments/podcast/runtime/podcast_daily.sh:21-44`, but contains no receipt validation between calls. Its heal chain advances despite suppressed failures at `departments/podcast/runtime/podcast_daily.sh:55-70`.
Impact: Process order is not receipt-gated; a zero exit or swallowed failure can advance without proving the expected artifact and contract.
Fix: Run nodes through a graph runner that validates the expected v2 receipt/artifact after each node and blocks the dependent edge on absence, failure, or identity mismatch.
Priority: P1
Confidence: high

Verdict: **findings**.

## 6. cadence contract

### Finding: Stage 8 has timer units but no governed cadence contract
Evidence: Stage 8 requires a compiled trigger/alert artifact and `cadence check` gate at `runbooks/ultimate-department-creation.md:40-43`. The available schedule is encoded directly in `departments/podcast/runtime/systemd/podcast-daily-department.timer:1-12`, while the machine map allowlists only runtime artifacts at `departments/podcast/subgraphs.json:223-230`; no Stage 8 cadence artifact is represented there.
Impact: Concurrency, catch-up, dedupe/cursor, digest/cooldown, and escalation SLA cannot be checked as one approved contract.
Fix: Add the smallest valid Stage 8 cadence contract describing the existing timer and outbox behavior, then execute the cadence check and installer dry-run.
Priority: P1
Confidence: high

Verdict: **findings**.

## 7. map-runtime honesty

### Finding: Three spot-checked declarations disagree with execution
Evidence: (1) The map places N6 alongside N1 before compare at `departments/podcast/procedural-graph.md:28-42`, but daily execution omits N6 at `departments/podcast/runtime/podcast_daily.sh:21-44`. (2) The machine map says rotation is not daily at `departments/podcast/subgraphs.json:223-230`, but the daily trigger executes it at `departments/podcast/runtime/podcast_daily.sh:72-73`. (3) SG-PIPELINE declares N1→N2→N9 at `departments/podcast/subgraphs.json:98-114`, while the shell runs N1, then multiple other sensors, then one shared compare and never invokes `record.py` as that lane's N9 at `departments/podcast/runtime/podcast_daily.sh:24-44`.
Impact: The graph is not an honest executable description, undermining traceability and change QA.
Fix: Make the orchestrator and machine graph identical, preferably by executing graph edges rather than duplicating them in shell.
Priority: P1
Confidence: high

Verdict: **findings** (3/3 spot checks mismatched).

| dimension | verdict | worst finding priority |
|---|---|---|
| v2 run records | findings | P1 |
| objectives sensors | findings | P1 |
| outbound ask classes | findings | P1 |
| fail-closed gating | findings | P1 |
| receipt-gated steps | findings | P1 |
| cadence contract | findings | P1 |
| map-runtime honesty | findings | P1 |
