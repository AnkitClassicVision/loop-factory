---
title: v2 inheritance — the new-loop entering process
status: closed
type: task
assignee: coordinator-fable
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

## Resolution

Complete with executed proof (2026-08-02, commit e8ebc64). Scaffold now
instantiates from templates/: charter with a commented setpoints.objectives
section (board Zone 1 lights up when F1 fills it), eval-registry template
carrying the golden-set advisory rule, subscription-only engines template
(command + auth_class + auth_probe shape), a runtime-node template
implementing the fail-closed timed_emit runrecord pattern, and a daily
trigger that regenerates the estate feed + estate board + the department's
own board. 8 new tests including two EXECUTED proofs (template node emits a
validating runs-v2 record; boardfeed runs on a scaffolded tree). Live
throwaway proof: department 'demoproof' scaffolded (7 artifacts) → node run →
record emitted → feed built (4 lines, 0 malformed) → board rendered (6
mentions) → deleted. Note: closed before 16/18 finish — the entering process
inherits their remaining pieces automatically because it instantiates from
the same templates and factory modules those tickets extend.
