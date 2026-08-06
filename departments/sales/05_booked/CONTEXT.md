<!-- GENERATED:BEGIN section=context source=subgraphs.json -->
# SG-BOOKED Context

## Purpose

Implement `SG-BOOKED` for concept refs: C3.

## Node chain

1. `runtime/booked_sensor.py`

## DONE means

- calendar receipt exists with a confirmed time and attendee
- the booking is linked to the ledger identity and its source attribution
Receipt: calendar event id in the booking record

## Floor

This stage holds the `booked` floor. Current values live in `../floors.yaml` (machine-written; numbers are never copied here — two copies guarantees one stale).

## Inputs

### L3

- `charter.yaml`
- `references/`

### L4

- `state/` paths used by the node chain

## Outputs

- `runtime/booked_sensor.py`

## Verify

Verify against the `SG-BOOKED` row in `../procedural-graph.md`.
<!-- GENERATED:END section=context -->

_No owner notes yet._
