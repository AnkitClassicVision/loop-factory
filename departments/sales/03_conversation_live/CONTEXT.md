<!-- GENERATED:BEGIN section=context source=subgraphs.json -->
# SG-CONVERSATION-LIVE Context

## Purpose

Implement `SG-CONVERSATION-LIVE` for concept refs: C3, C5, C6.

## Node chain

_No implementation nodes declared._

## DONE means

- two-way exchange within the last 7 days: a human reply or a live conversation (opens and clicks never count)
- every draft produced here came from a verified-complete context packet (relationship history: episodes, calls, Circle activity, prior threads)
- every draft passed the owner voice check before reaching the approval queue
Receipt: thread/reply record + context-packet manifest + voice-check receipt

## Floor

This stage holds the `conversation_live` floor. Current values live in `../floors.yaml` (machine-written; numbers are never copied here — two copies guarantees one stale).

## Inputs

### L3

- `charter.yaml`
- `references/`

### L4

- `state/` paths used by the node chain

## Outputs

- No implementation outputs declared

## Verify

Verify against the `SG-CONVERSATION-LIVE` row in `../procedural-graph.md`.
<!-- GENERATED:END section=context -->

_No owner notes yet._
