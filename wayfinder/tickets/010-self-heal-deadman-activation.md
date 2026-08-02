# 010 — Self-heal + deadman activation policy

Status: OPEN · Type: grilling (HITL) · Claimed: — · Blocked by: [006 — Stabilize the live line before the rebuild?]

## Question

The heal ladder exists as tested code but nothing ever invokes it, and the watcher itself has no watcher. Decide the activation policy that makes self-healing real without violating the always-human floor.

## Shape

- What invokes heal: manager tick detects gap (missing receipt, stale heartbeat, failed QA) → drives heal ladder; estate watchdog catches dead managers.
- **Watcher's watcher** (from ticket 003): independent `estate-deadman.timer`, poisoned-registry/count test causing visible hold, false-green estate report **stops promotion**.
- Escalation: heal rung exhaustion → human-in-the-loop outbox, never silent retry-forever. Retry caps move from hardcoded to charter-declared.
- Kill switches: estate `park` verb (graduates from fog if this ticket wants it now).

## Resolution

(pending)
