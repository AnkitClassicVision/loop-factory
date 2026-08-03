# D3 — lane inventory + evidence sources (resolved from recon, 2026-08-03)

## Watched lanes (per D1 split)

| lane | unit/entry | cadence | evidence |
|---|---|---|---|
| HSD daily operator | hsd-daily-operator.timer | 12h (07:30/17:30 ET observed) | its run receipts/logs in hubspot-daily-2 |
| Fix consumer | obe-fix-consumer.timer | 10 min | its ledger/receipts |
| Sales daily chain | open-engine sales_daily.sh (+ v2 sidecar records landed 2026-08-03) | daily | departments/sales/state runs-v2.jsonl + receipts_spine |
| Sales funnels | speed_funnel, email_funnel, deal_funnel, linkedin lanes, enrichment | event/daily | state ledgers in open-engine sales |
| Approval + send | send_approved / approval queue | on approval | approval ledgers; voice-gate receipts |

## Evidence sources (read-only for this department)

- HubSpot is the system of record for people/touches (owner decision
  2026-08-03, podcast charter precedent) — consumed via existing pull
  artifacts (hubspot_pull outputs), never direct API from this department.
- Voice gate: /mnt/d_drive/repos/hubspot-daily-2/scripts/voice_gate.py —
  PII-free receipts; the department consumes receipts, fail-closed.
- Cross-repo reads are configured paths (estate.json pattern), never
  ambient credentials; missing evidence = honest-absent.
