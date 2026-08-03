# Stage 11 verification: pulse

- Schema: `proof-bundle/v1`
- Pinned release: `unpinned`
- Verdict: **FAIL**

| Drill | Result | Evidence |
|---|---:|---|
| duplicate-trigger dedupe | PASS | same_run_id=True; duplicate=True; executions=1 |
| auth block | FAIL | unsupported: no Stage 11 auth fixture contract exists |
| record-write failure blocks advancement | PASS | record_error=True; advanced=False |
| worker kill | FAIL | unsupported: no bounded worker-kill fixture is exposed by the scaffold |
| evaluator rejection | FAIL | unsupported: the scaffold has an eval registry but no executable evaluator fixture |
| objective breach surfaces | PASS | OBJECTIVE_BELOW_MIN rows=1 |
| escalation delivery | PASS | escalated=True; outbox_rows=1 |
| receipt rebuild | PASS | written=2; rebuilt=2; identities_preserved=True |
| board truth | FAIL | unsupported: no canonical Stage 11 board-truth assertion is defined |
| drift | FAIL | unsupported: no non-mutating drift injection API exists |
| zero-external-effects | PASS | records=1; external_actions_taken=[0] |
