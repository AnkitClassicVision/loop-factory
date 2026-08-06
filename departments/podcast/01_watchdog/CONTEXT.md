<!-- GENERATED:BEGIN section=context source=subgraphs.json -->
# SG-WATCHDOG Context

## Purpose

Implement `SG-WATCHDOG` for concept refs: C1, C3, C4, C12, C13, C16.

## Node chain

1. `runtime/sense_estate.py`
2. `runtime/compare_charter.py`
3. `runtime/fingerprint_dedup.py`
4. `runtime/escalate_outbox.py`
5. `runtime/hopper_sensor.py`
6. `runtime/funnel_floor_sensor.py`
7. `runtime/comms_reconcile_sensor.py`
8. `runtime/record.py`
9. `runtime/expectation_reconcile.py`
10. `runtime/floor_compiler_run.py`
11. `runtime/conductor_tick.py`

## DONE means

- every estate unit in the charter inventory was sensed this run (a missing unit is a silent-gap FAIL)
- every non-ok observation classified through the finite transition table and deduplicated to one open thread per fingerprint
- escalation cards carry the one question + evidence + fingerprint, delivered_count stays 0 in shadow
Receipt: observations.jsonl rows + escalation outbox rows + run record

## Inputs

### L3

- `charter.yaml`
- `references/`

### L4

- `state/` paths used by the node chain

## Outputs

- `runtime/sense_estate.py`
- `runtime/compare_charter.py`
- `runtime/fingerprint_dedup.py`
- `runtime/escalate_outbox.py`
- `runtime/hopper_sensor.py`
- `runtime/funnel_floor_sensor.py`
- `runtime/comms_reconcile_sensor.py`
- `runtime/record.py`
- `runtime/expectation_reconcile.py`
- `runtime/floor_compiler_run.py`
- `runtime/conductor_tick.py`

## Verify

Verify against the `SG-WATCHDOG` row in `../procedural-graph.md`.
<!-- GENERATED:END section=context -->

_No owner notes yet._
