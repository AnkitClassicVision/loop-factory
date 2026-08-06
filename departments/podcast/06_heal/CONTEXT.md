<!-- GENERATED:BEGIN section=context source=subgraphs.json -->
# SG-HEAL Context

## Purpose

Implement `SG-HEAL` for concept refs: C5, C11, C12, C14.

## Node chain

1. `runtime/heal_select.py`
2. `runtime/heal_apply.py`
3. `runtime/heal_verify.py`
4. `runtime/record.py`

## DONE means

- every open incident offered exactly one allowlisted playbook or a recorded refusal (unknown classes refuse, never improvise)
- every heal stayed propose-only in shadow with its receipt appended
Receipt: heals.jsonl receipts + run record

## Inputs

### L3

- `charter.yaml`
- `references/`

### L4

- `state/` paths used by the node chain

## Outputs

- `runtime/heal_select.py`
- `runtime/heal_apply.py`
- `runtime/heal_verify.py`
- `runtime/record.py`

## Verify

Verify against the `SG-HEAL` row in `../procedural-graph.md`.
<!-- GENERATED:END section=context -->

_No owner notes yet._
