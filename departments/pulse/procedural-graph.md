# pulse — procedural graph (human form; machine form: subgraphs.json)

One funnel, SG-DIGEST, read-only (no external dispatch, no CRM, no model
calls). Node implementations are bound at F3; ids are stable.

## SG-DIGEST (concepts C1–C5)

| node | role | concept | notes |
|---|---|---|---|
| ORCH | cycle orchestrator (`pulse_daily.sh`) | C5 | runs the chain below, hand-invoked during acceptance |
| N1 intake_scan | read `inbox/`, classify readable vs unreadable | C1 | deterministic parse only |
| N2 digest_builder | fold readable notes + harvested `replies/` into the digest draft | C2, C3 | THE declared return path of the ask class |
| N3 clarify_ask | emit one `clarify_note` ask draft per unreadable note into the outbox | C3 | `emits_ask` — declares `return_path: digest_builder`, `return_sla_hours: 48` (comms-loop invariant) |
| N4 objectives_sensor | compute digest_coverage + ask_return_integrity from aggregate state; write `state/objectives_observed.json` | C4 | objectives-observed/v1; absent, never invented |

Guard posture (read-only funnel): S4/S5/S6/S7 not applicable — nothing
dispatches; S1/S2 not applicable — fixture notes carry no identities or
recipients; S3 not applicable — zero model-capable nodes, deterministic
parsing only; S8 not applicable — zero cost-incurring nodes (model_calls
budget is 0). Every rationale is restated in `subgraphs.json`.
