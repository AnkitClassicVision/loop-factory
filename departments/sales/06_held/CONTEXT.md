<!-- GENERATED:BEGIN section=context source=subgraphs.json -->
# SG-HELD Context

## Purpose

Implement `SG-HELD` for concept refs: C1, C6, C10.

## Node chain

1. `runtime/held_sensor.py`
2. `runtime/held_confirm_card.py`

## DONE means

- call attended, decision-maker present, >= 20 minutes (locked at readback)
- held receipt carries the qualification bar met and the verified source attribution
- the held event is appended to the cohort ledger the same day
Receipt: held-call receipt (independent of the booking receipt)

## Floor

This stage holds the `held` floor. Current values live in `../floors.yaml` (machine-written; numbers are never copied here — two copies guarantees one stale).

## Inputs

### L3

- `charter.yaml`
- `references/`

### L4

- `state/` paths used by the node chain

## Outputs

- `runtime/held_sensor.py`
- `runtime/held_confirm_card.py`

## Verify

Verify against the `SG-HELD` row in `../procedural-graph.md`.
<!-- GENERATED:END section=context -->

_No owner notes yet._
