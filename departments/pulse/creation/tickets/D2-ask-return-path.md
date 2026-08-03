# D2 — ask-class return path + SLA (resolved)

type: grilling · status: resolved · decided: 2026-08-03

DECISION: pulse has exactly one outbound ask class, `clarify_note` (asking the
owner to clarify an unreadable inbox note). Per the comms-loop invariant
(factory lint + podcast charter precedent) it declares:
- return_path: `digest_builder` — the node that reads `replies/` on the next
  cycle and folds clarifications into the digest
- return_sla_hours: 48

Escalation when asks accumulate with zero harvested replies beyond SLA.
In shadow the ask never leaves the machine: it lands in the department outbox
as a draft, and fixture replies exercise the return path.

Why: this is the invariant the factory now enforces (`emits_ask` requires
`return_path` + `return_sla_hours`); the throwaway must exercise it or the
acceptance run would not prove the new lint on a fresh department.
