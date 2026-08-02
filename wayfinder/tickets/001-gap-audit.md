# 001 — Audit loop-factory autonomy gaps

Status: CLOSED · Type: research (AFK) · Resolved: 2026-08-02 by claude_code explore sub-agent

## Question

What actually exists and works in loop-factory today versus the 15-gap external review, and where would per-step telemetry attach?

## Resolution

Full report: [`.polly/reports/loop-factory-gap-audit.md`](../../.polly/reports/loop-factory-gap-audit.md) (every claim file:line cited; `python3 loopfactory.py check` = 138/138 pass).

- The external review **undersells** the repo. Working today: signed TTL single-use effect receipts (kernel/receipts.py:66-125), live sends refused in code (kernel/gateways/dispatch.py:61-62), credential-free departments (kernel/capabilities.py:24-84), deterministic evaluators, approval boundaries, department isolation, manager watch loop, drift QA.
- **Live fire 1:** installed `estate-manager.service` points at the open-engine repo, not loop-factory; estate frozen at epoch 1 since 2026-07-28. Social department dead since Jul 31, its manager since Jul 28 — nothing escalated.
- **Live fire 2:** both departments in unremediated drift (podcast 6, social 10 artifacts diverged from pinned releases); daily loops never run the drift check — hard rule 4 silently violated.
- **MISSING:** factory-recipe versioning, backup/restore, per-step telemetry (budget ledger never commits actuals — kernel/gateways/budget.py:89-92).
- **PARTIAL:** run state machine (runs are sequential bash), step receipts unsigned/forgeable JSON, auth expiry indistinguishable from any crash, model route visible only in draft artifacts, kill switches manual systemctl only, task truth spans 4 stores.
- **Telemetry attach points already exist:** the fenced run recorder (podcast `runtime/record.py:101-136`) and `BudgetBroker.commit(actual)` — manager budget sensing lights up automatically once actuals are committed.
- Departments that exist: **podcast (alive, ticking every 30 min, heartbeat epoch 1492)** and **social (dead)**.
