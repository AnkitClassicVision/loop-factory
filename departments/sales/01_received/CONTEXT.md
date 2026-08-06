<!-- GENERATED:BEGIN section=context source=subgraphs.json -->
# SG-RECEIVED Context

## Purpose

Implement `SG-RECEIVED` for concept refs: C2, C10, C13.

## Node chain

1. `runtime/intake_sensor.py`

## DONE means

- lead exists as one row in the cohort ledger with source attribution (icaregrow, podcast_handoff, inbound, website_form, pfs_warm, pfs_cold)
- identity deduplicated against every lane before the row lands (one human, one id)
Receipt: events.jsonl row (opaque subject_id, from_stage null -> received)

## Floor

This stage holds the `received` floor. Current values live in `../floors.yaml` (machine-written; numbers are never copied here — two copies guarantees one stale).

## Inputs

### L3

- `charter.yaml`
- `references/`

### L4

- `state/` paths used by the node chain

## Outputs

- `runtime/intake_sensor.py`

## Verify

Verify against the `SG-RECEIVED` row in `../procedural-graph.md`.
<!-- GENERATED:END section=context -->

_No owner notes yet._
