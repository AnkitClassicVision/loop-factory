---
title: Implement evaluator gates on podcast
status: closed
type: task
assignee: coordinator-fable
blocked_by: [09, 15]
---

## Question

Implement the ticket-09 evaluator framework on the podcast department:
deterministic checks wired into every node's QA gate, cross-model evaluation
where ticket 09 requires it, verdicts written into run records and gating
receipts deny-by-default. Executed proof: evaluator failures BLOCK (watch one
fail red before trusting it), `loopfactory.py qa --name podcast` PASS.
Ringer-routed; process-change-qa runbook applies.

## Resolution

Complete (2026-08-02, rounds 3+4, commits e8ebc64 + HEAD). The locked
evaluator contract is code: factory/evalregistry.py (registry validation,
weighted block/revise/allow verdicts, structural cross_model requirement,
golden-set gating rule) + factory/goldenset.py (open/holdout splits, 100%
open + >=80% holdout before verdicts gate, failures named). Real registries:
podcast (Tier-1 only per C14), social (draft_qa, Tier-2 advisory until a
golden set lands). social qa_post.py wired: severity-weighted deterministic
defects, verdict + gating in every report/receipt, binary pass gate
unchanged while advisory. Evaluator failures BLOCK via the existing pass
gate (watched failing in tests). Deviation from original scope: podcast has
no model-calling nodes, so cross-model evaluation exercises on social; the
estate-level golden-set re-run drift alarm rides ticket 19's follow-on (fog:
estate health-loop thresholds).
