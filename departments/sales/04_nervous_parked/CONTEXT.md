<!-- GENERATED:BEGIN section=context source=subgraphs.json -->
# SG-NERVOUS-PARKED Context

## Purpose

Implement `SG-NERVOUS-PARKED` for concept refs: C3, C2.

## Node chain

_No implementation nodes declared._

## DONE means

- exit happens exactly one way: a revive touch was executed (draft approved + sent via the estate gateway) OR an explicit kill reason is recorded
- no row parks silently past its scheduled revive date
Receipt: touch receipt or kill record with reason

## Floor

This stage holds the `nervous_parked` floor. Current values live in `../floors.yaml` (machine-written; numbers are never copied here — two copies guarantees one stale).

## Inputs

### L3

- `charter.yaml`
- `references/`

### L4

- `state/` paths used by the node chain

## Outputs

- No implementation outputs declared

## Verify

Verify against the `SG-NERVOUS-PARKED` row in `../procedural-graph.md`.
<!-- GENERATED:END section=context -->

_No owner notes yet._
