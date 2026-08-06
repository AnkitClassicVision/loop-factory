<!-- GENERATED:BEGIN section=context source=subgraphs.json -->
# SG-MANIFEST Context

## Purpose

Implement `SG-MANIFEST` for concept refs: C6, C7, C10, C15.

## Node chain

1. `runtime/manifest_sensor.py`

## DONE means

- every guest/episode manifest checked for completeness against the declared expectation manifests
- every gap became an expectation delta with its declared heal
Receipt: expectation receipts + observations + run record

## Inputs

### L3

- `charter.yaml`
- `references/`

### L4

- `state/` paths used by the node chain

## Outputs

- `runtime/manifest_sensor.py`

## Verify

Verify against the `SG-MANIFEST` row in `../procedural-graph.md`.
<!-- GENERATED:END section=context -->

_No owner notes yet._
