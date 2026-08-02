# Estate watchdog cross-review record

## Fresh QA run 1

- Worker: fresh read-only Codex context, static security fallback.
- Verdict: BLOCK.
- Codex Security OAuth: not run because its required artifact location was
  outside the user-authorized worktree boundary. Coverage is explicitly
  degraded to static review.
- QA-001 HIGH: deadman unit skipped when the registry directory was missing.
- QA-002 HIGH: registry decode errors could exit before writing an alarm.
- QA-003 HIGH: boolean epochs passed Python's integer type check.
- QA-004 HIGH: installer lacked a tested rollback path.
- QA-005 BLOCK: no temporary-copy `systemd-analyze verify` test.
- QA-006 BLOCK: no mocked explicit-apply test.
- QA-007 BLOCK: full repository check evidence was absent from the review
  packet.

## Resolution

- Removed the deadman unit condition so missing registry data reaches the
  fail-closed evaluator.
- Converted registry decoding failures into alarm findings and required exact
  integer epoch types.
- Added pre-mutation self-test/unit verification, an explicit unique-unit-only
  rollback command plan, stop-on-failure guidance, and mocked apply/failure
  coverage.
- Added temporary-copy systemd verification and reran the full repository
  gate: `159 passed in 3.60s`, `CHECK PASS`.

## Fresh QA run 2

- Worker: second fresh read-only Codex context, static security fallback.
- Verdict: REQUEST_CHANGES.
- QA-008 HIGH: non-UTF-8 heartbeat bytes exited before recording the required
  outbox alarm.
- Resolution: heartbeat decoding errors now become
  `estate_heartbeat_unreadable`; an exact false-green regression test covers
  healthy STATE.json plus non-UTF-8 heartbeat bytes.
- Verification after fix: focused `22 passed in 0.23s`; full repository
  `160 passed in 3.75s`, `CHECK PASS`.

## Fresh QA run 3

- Worker: third fresh read-only Codex context, static security fallback.
- Verdict: REQUEST_CHANGES.
- QA-009 HIGH: same-epoch STATE.json and heartbeat rows with different but
  individually fresh timestamps could report healthy.
- Resolution: state and heartbeat timestamps must now identify the same exact
  estate cycle; same-epoch mismatches produce `estate_timestamp_mismatch`.
- Verification after fix: focused `23 passed in 0.23s`; full repository
  `161 passed in 3.67s`, `CHECK PASS`.

## Fresh QA run 4

- Worker: fourth fresh read-only Codex context, static security fallback.
- Verdict: REQUEST_CHANGES.
- QA-010 HIGH: an invalid earlier heartbeat row could be hidden by a valid
  final row.
- QA-011 HIGH: rollback stopped on missing/inactive units before completing
  file removal and daemon reload.
- Resolution: every nonblank heartbeat row must parse as an object before the
  last receipt is trusted. Rollback now tolerates absent-unit systemctl
  results, completes all cleanup commands, and fails only after reporting
  critical file-removal or daemon-reload errors.
- Verification after fixes: focused `26 passed in 0.26s`; full repository
  `164 passed in 3.70s`, `CHECK PASS`.

## Fresh QA run 5

- Worker: fifth fresh read-only Codex context, static security fallback.
- Verdict: REQUEST_CHANGES.
- QA-012 HIGH: earlier heartbeat JSON objects were not checked for the
  estate-manager heartbeat schema, so a valid final row could hide `{}` or a
  wrong-emitter historical row.
- Resolution: every row now requires a timezone-aware timestamp,
  estate-manager/cycle identity, a payload object, and exact integer epoch,
  findings, and escalation counters.
- Verification after fix: focused `29 passed in 0.24s`; full repository
  `167 passed in 3.77s`, `CHECK PASS`.

## Fresh QA run 6

- Worker: sixth fresh read-only Codex context, static security fallback.
- Verdict: REQUEST_CHANGES.
- QA-013 HIGH: final heartbeat finding/escalation counters were not bound to
  STATE.json, and required state fields were not fully type-validated.
- Resolution: STATE.json now requires a nonnegative integer epoch, a typed
  department-epoch map, a findings list, and a nonnegative integer escalation
  count. The final heartbeat counters must equal state findings/escalations.
- Verification after fix: focused `35 passed in 0.25s`; full repository
  `173 passed in 3.59s`, `CHECK PASS`.

## Fresh QA run 7

- Worker: seventh fresh read-only Codex context, static security fallback.
- Verdict: PASS.
- Issues: none.
- OAuth security scan: not run because its required artifact location would
  violate the worktree-only boundary. Static security coverage remained
  explicitly degraded; no live system or external state was touched.
