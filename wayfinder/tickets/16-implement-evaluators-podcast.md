---
title: Implement evaluator gates on podcast
status: open
type: task
assignee:
blocked_by: [09, 15]
---

## Question

Implement the ticket-09 evaluator framework on the podcast department:
deterministic checks wired into every node's QA gate, cross-model evaluation
where ticket 09 requires it, verdicts written into run records and gating
receipts deny-by-default. Executed proof: evaluator failures BLOCK (watch one
fail red before trusting it), `loopfactory.py qa --name podcast` PASS.
Ringer-routed; process-change-qa runbook applies.
