<!-- GENERATED:BEGIN section=context source=subgraphs.json -->
# SG-DAG-SUPERVISION Context

## Purpose

Implement `SG-DAG-SUPERVISION` for concept refs: C1, C2, C11, C16, C19.

## Node chain

1. `runtime/dag_supervisor.py`

## DONE means

- the pipeline's hashed DAG projection validated fresh (stale or missing projection is an alarm, never a skip)
- every silent skip, forged skip artifact, or hash mismatch became a dag_receipt_violation incident
Receipt: dag-projection validation observation + run record

## Inputs

### L3

- `charter.yaml`
- `references/`

### L4

- `state/` paths used by the node chain

## Outputs

- `runtime/dag_supervisor.py`

## Verify

Verify against the `SG-DAG-SUPERVISION` row in `../procedural-graph.md`.
<!-- GENERATED:END section=context -->

_No owner notes yet._
