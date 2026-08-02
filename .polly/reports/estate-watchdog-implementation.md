# Estate watchdog stabilization yardstick

## Requirements contract

- U1: Add uniquely named loop-factory user service and timer files that invoke
  the repository's real `loopfactory.py estate` entrypoint. They must not
  collide with the existing open-engine `estate-manager.service`.
- U2: Add a deterministic deadman that alarms through the factory human
  outbox when the estate heartbeat or registry cannot be trusted, or when the
  heartbeat is older than the configured threshold.
- U3: Add a poisoned-registry self-test that corrupts only a temporary copy and
  proves the deadman reports an alarm condition.
- U4: Add an idempotent installer whose default behavior is display-only and
  whose `--apply` path alone copies units and invokes `systemctl --user`.
- U5: Prove behavior with isolated tests and the repository-wide check.

## Non-goals

- Do not install, enable, start, stop, or reload real user units during build or
  verification.
- Do not modify department state, estate state, governance files, department
  maps, charters, releases, or runtime behavior.
- Do not repair the stalled social department or change estate-manager policy.

## Implementation units and proof

| Unit | Expected proof |
|---|---|
| U1 | Static unit assertions plus `systemd-analyze verify` against temporary copies |
| U2 | Focused tests for healthy, stale, unreadable, malformed, future, and inconsistent state |
| U3 | CLI self-test exits zero and reports that poisoned registry input was detected |
| U4 | Dry-run output exactness and mocked apply-path tests; no real `systemctl` calls |
| U5 | `python3 loopfactory.py check` passes |

## Decision residue

- Hardest decision: make unreadable registry/heartbeat inputs alarm without
  relying on the estate manager's own healthy claim.
- Rejected alternative: reuse `estate-manager.service`; it would preserve the
  name collision that caused this outage. Also rejected: a deadman that checks
  only file mtime, because malformed or false-green content could pass.
- Least-confident assumption: the canonical checkout remains
  `/mnt/d_drive/repos/loop-factory`, matching the repository's existing
  systemd convention. The installer validates this path before applying.

## Verification receipts

- Focused tests after fresh-QA fixes: `35 passed in 0.25s`.
- Poisoned registry: `INFO poisoned-registry self-test passed: detected
  estate_registry_unreadable`.
- Unit parser: all four repo-local units returned exit 0 from
  `systemd-analyze verify`.
- Repository check after all accepted BLOCK/HIGH fixes: `173 passed in 3.59s`,
  `CHECK PASS`.
- Live-state actions during build and review: zero.
