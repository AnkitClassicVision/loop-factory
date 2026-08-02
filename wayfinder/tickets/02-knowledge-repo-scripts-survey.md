---
title: Survey knowledge_repo concept-to-process + eval scripts
status: closed
type: research
assignee: wf-02-knowledge-repo
blocked_by: []
---

## Question

Which patterns in `/mnt/d_drive/repos/learning_github/knowledge_repo/scripts`
(atlas_to_concept.py, concept_to_process.py, evaluate_node.py,
improve_node.py, qa_agent_package.py, heal_agent_package.py, models.json) and
its `concepts/_template*` packages are worth porting into Loop Factory v2 —
specifically for (a) the evaluator framework (ticket 09), (b) the F1–F3
authoring flow, and (c) node improvement/heal loops? Report per-script: what
it does, its input/output contract, and a port/skip/wrap recommendation.

## Resolution

Full findings: `wayfinder/research/02-knowledge-repo-scripts-survey.md`.

knowledge_repo's "AAC Agent Package" pipeline is a separate authoring
substrate (SQLite concept-map DB + AAC JSON process cards) that duplicates
decisions loop-factory already made (markdown concept-map + subgraphs.json).
So `atlas_to_concept.py`, `concept_to_process.py`, `new_agent_package.py`,
and both `_template*` concept-map packages are **SKIP** as scripts — F0-F3
already have a fit-for-purpose equivalent; only a couple of sub-patterns
(refuse-with-exact-fix generator discipline, the D/C/A/H runtime-mode keyword
heuristic) are worth citing elsewhere, not porting.

`evaluate_node.py`, `qa_agent_package.py`, and `models.json` are **direct
PORTs** for ticket 09: the golden-set truth rule + open/holdout split
discipline, the weighted critical/major/minor block/revise/allow verdict,
and the deterministic-scenarios-first-then-blind-cross-model-LLM-scenarios
split answer ticket 09's core questions almost exactly as posed.
`improve_node.py`'s holdout-gated cheaper-or-better model-swap logic is a
**PORT (adapted)** for the evaluator + heal L2 acceptance test.
`heal_agent_package.py` is a **PORT of its fix-class taxonomy only** — it
does not duplicate `factory/heal_ladder.py` (that's an escalation state
machine; this is an auto-fixable-vs-always-human classification), and the two
should compose: tag every heal action with a fix class so meaning/runtime/
grading/leak classes can never auto-apply regardless of heal level.
`export_agent_map.py` is a low-priority **WRAP** candidate if a combined
concept↔process Mermaid view is ever wanted (no open ticket needs it yet).
