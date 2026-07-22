# Process-Change QA Runbook

Rule: **if the process changes, the procedural map changes, and the change
goes through QA — no exceptions, no silent edits.** The enforcement is
deterministic, not honor-system:

1. Releases are content-addressed over charter + graph + subgraphs + runtime
   code (`factory/release.py`). Any edit changes the tree hash.
2. `factory/graphs.py check_drift` compares the live tree to the pinned
   `current` release. Live ≠ pinned → drift alarm: "process changed without
   re-pin."
3. `loopfactory.py release pin` REFUSES to pin while the map lint or
   traceability check fails — an invalid map cannot become a release.

## The only legal path to change a department's process

1. **Change card** — write what changes and why (one card per change; heals at
   L4 file this automatically).
2. **Patch the maps first**: procedural-graph.md (human form) AND
   subgraphs.json (machine form). If the WHY changed — not just the HOW —
   reopen the F1 interview instead; a heal can never silently reinterpret
   intent.
3. **Lint**: `python3 loopfactory.py validate --name <dept>` must PASS
   (guard-matrix ordering + node↔artifact traceability).
4. **Re-author affected nodes** to match the patched graph. Update any runbook
   or skill the nodes reference (they are release artifacts too).
5. **Re-shadow the changed nodes** — executed checks, simulate sinks,
   delivered_count==0.
6. **Re-pin**: `python3 loopfactory.py release pin --name <dept> --source-ref
   <git sha> --flip`.
7. **Verify**: `python3 loopfactory.py qa --name <dept>` — drift check must be
   clean.

## What a change may never do

- Touch an immutable safety invariant (the manager and heal ladder raise on
  any attempt — `ImmutableInvariantError` / `ImmutableHealError`).
- Raise an autonomy level. Heals restore quality at the current level;
  promotion has its own evidence path (promotion-ladder.md).
- Edit governance files (charter, thresholds, this runbook). Those are human
  charter edits with owner sign-off.
- Skip the re-shadow because "it's a small change." Small unshadowed changes
  are how silent regressions ship.
