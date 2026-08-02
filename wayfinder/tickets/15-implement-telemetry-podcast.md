---
title: Implement the v2 run-record contract on podcast
status: open
type: task
assignee: coordinator-fable
blocked_by: [07, 08, 14]
---

## Question

Wire the ticket-07 run-record contract into every podcast runtime node —
tokens/cost per engine lane (per ticket 04 sources), model + auth class,
attempts, disposition — closing the idempotency/retry gaps from ticket 08 and
the auth-block recording from ticket 14. Executed proof: `loopfactory.py
validate --name podcast` PASS, node QA checks PASS, records visibly carrying
the new fields for a full shadow cycle. Route the build through Ringer
manifests (executed checks prove pass/fail); process-change-qa runbook
applies (map patch + re-lint + re-shadow + re-pin).
