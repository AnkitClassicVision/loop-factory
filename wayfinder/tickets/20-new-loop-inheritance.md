---
title: v2 inheritance — the new-loop entering process
status: open
type: task
assignee:
blocked_by: [16, 17, 18]
---

## Question

Make every FUTURE loop inherit v2 at birth (owner directive, Ankit
2026-08-02: "keep going until we are completed with the new loop for podcast
and the new loop entering process"). Update the factory's entering path —
`templates/`, `factory/scaffold.py`, and the F0–F3 runbook steps — so that
scaffolding a new department automatically provides: (1) runs-v2 record
emission wired into the node templates and the daily-trigger template;
(2) the evaluator registry seed with per-department override file (ticket 09);
(3) a charter section for objective setpoints (setpoint/min/target — the
board's Zone 1 data, ticket 10 v1.1); (4) board-feed emission so the new
loop appears on the estate board with zero board work (Board Template v1);
(5) the L0–L5 heal-ladder wiring defaults (ticket 13/18); (6) the auth-route
policy defaults (ticket 14). Executed proof: scaffold a THROWAWAY department
in a temp checkout, run validate + a smoke shadow cycle, assert runs-v2
records + board-feed rows appear, then delete it — the entering process is
proven by entering, not by inspection.
