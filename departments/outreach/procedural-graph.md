# outreach — procedural graph (human form; machine form: subgraphs.json)

One funnel, SG-GOVERN, read-only from the guard matrix's perspective: the
department senses, reconciles, monitors, and escalates — it never
dispatches; the watched lanes dispatch under their own gateways and
budgets. Node implementations bind at F3.

## SG-GOVERN (concepts C1–C7)

| node | role | concept |
|---|---|---|
| ORCH | cycle orchestrator (`outreach_daily.sh`), receipt-gated, v2 records | C7 |
| N1 lane_sense | liveness of every watched unit/lane per the D1 boundary (timer state, receipt/ledger freshness) | C1 |
| N2 state_reconcile | funnel ledger vs HubSpot evidence → drift count; ALSO the return-path reader for escalation answers | C2, C5 |
| N3 gate_monitor | voice-gate receipts + send-class integrity over observed sends | C3, C4 |
| N4 queue_ager | approval-queue age accounting (>48h = aged) | C4 |
| N5 escalate | unknown failures → ONE ask to the owner outbox; `emits_ask`, return_path `state_reconcile`, SLA 48h | C5 |
| N6 objectives_sensor | objectives-observed/v1 (three hard zeros) + baselines jsonl (rate metrics) | C6 |

Guard posture: S1/S2 not applicable — the department handles lane metadata
and aggregate state, no recipient identities; S3 not applicable — zero
model-capable nodes, deterministic sensing only; S8 not applicable — zero
cost-incurring nodes (model_calls budget 0); S4/S5/S6/S7 not applicable —
no dispatch exists in this funnel (the watched lanes' own graphs carry the
full send-guard chains). Rationales restated in `subgraphs.json`.
