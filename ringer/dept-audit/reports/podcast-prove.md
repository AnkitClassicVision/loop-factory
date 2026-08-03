# podcast — prove drills in an ISOLATED WORKTREE sandbox (tracked state only; live state untouched)
{
  "schema": "proof-bundle/v1",
  "department": "podcast",
  "pinned_release": "4eaacf26d267cd62",
  "generated_at": "2026-08-03T16:52:20.842714+00:00",
  "allow_unsupported": true,
  "pass": true,
  "drills": [
    {
      "name": "duplicate-trigger dedupe",
      "pass": true,
      "evidence": "same_run_id=True; duplicate=True; executions=1"
    },
    {
      "name": "auth block",
      "pass": false,
      "evidence": "unsupported: no Stage 11 auth fixture contract exists"
    },
    {
      "name": "record-write failure blocks advancement",
      "pass": true,
      "evidence": "record_error=True; advanced=False"
    },
    {
      "name": "worker kill",
      "pass": false,
      "evidence": "unsupported: no bounded worker-kill fixture is exposed by the scaffold"
    },
    {
      "name": "evaluator rejection",
      "pass": false,
      "evidence": "unsupported: the scaffold has an eval registry but no executable evaluator fixture"
    },
    {
      "name": "objective breach surfaces",
      "pass": true,
      "evidence": "OBJECTIVE_BELOW_MIN rows=1"
    },
    {
      "name": "escalation delivery",
      "pass": true,
      "evidence": "escalated=True; outbox_rows=1"
    },
    {
      "name": "receipt rebuild",
      "pass": true,
      "evidence": "written=2; rebuilt=2; identities_preserved=True"
    },
    {
      "name": "board truth",
      "pass": false,
      "evidence": "unsupported: no canonical Stage 11 board-truth assertion is defined"
    },
    {
      "name": "drift",
      "pass": false,
      "evidence": "unsupported: no non-mutating drift injection API exists"
    },
    {
      "name": "zero-external-effects",
      "pass": true,
      "evidence": "records=1; external_actions_taken=[0]"
    }
  ],
  "report_path": "proof/podcast-verification-20260803T165220Z.md",
  "bundle_path": "proof/podcast-verification-20260803T165220Z.json"
}
exit_code=0
