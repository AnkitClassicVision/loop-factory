# Drift Reconciliation — podcast + social vs pinned releases (2026-08-02)

Analysis only. Every re-pin / revert below is an OWNER action to run post-merge
per `runbooks/process-change-qa.md`. Nothing in this report was remediated
automatically, and the drift-alarm wiring shipped alongside it never
remediates either — it reports and escalates.

How measured (read-only, no state written):

- `python3 loopfactory.py qa --name podcast` / `--name social` in this branch's
  worktree (committed tree @ master 3b7eaf9) — full output in the appendix.
- The same command with `--root /mnt/d_drive/repos/loop-factory` against the
  live checkout, to capture its uncommitted local state.
- `git log` / `git diff <pin source_ref>..HEAD` per artifact for provenance.

## Snapshot

| department | pinned `current` | pin source_ref | pinned at | drifted artifacts |
|---|---|---|---|---|
| podcast | `77a3e3d095f3cc07` (committed) | `eea9c022` (= 13a351b lineage, PR #6) | pre-2026-07-31 | 6 |
| social (committed tree) | `31d45329ef6cfeec` | `94534f9` (pinned by 33fa44c, 2026-07-28) | 2026-07-28 | 10 |
| social (live checkout) | `ac07729f5044185d` — **flip is uncommitted** | `57b161d` (2026-07-28) | uncommitted | same 10 |

The live social checkout carries an uncommitted re-pin: the `ac07729f` release
dir is untracked and `releases/current` is locally modified to point at it.
That re-pin legitimately captured the owner's burn-in charter change
(`57b161d`, "Ankit 2026-07-28"), but it was never committed, and two later
merged commits drifted past it again.

## podcast — 6 artifacts, all from merged PR #8 (`feat/dag-supervisor`)

All six diverge because of two commits, both merged to master via PR #8
(merge `3b7eaf9`): `645530b` (dag_supervisor node, 2026-07-31 10:54 -0400) and
`692ffe4` (daily-cycle wiring, 2026-07-31 11:33 -0400).

| artifact | diff vs pin `77a3e3d0` | provenance | classification | recommendation |
|---|---|---|---|---|
| `knowledge/concept-map.md` | +3 lines: DAG-receipt concept node | `645530b` | intended-improvement | re-pin |
| `procedural-graph.md` | +18/−2: dag supervisor node + transitions | `645530b` | intended-improvement | re-pin |
| `subgraphs.json` | +17: N1 dag_supervisor node with impl | `645530b` | intended-improvement | re-pin |
| `runtime/dag_supervisor.py` | new file, +283: hashed DAG-projection verifier | `645530b` | intended-improvement | re-pin |
| `runtime/compare_charter.py` | +12: charter transitions for dag + ledger sensors | `692ffe4` | intended-improvement | re-pin |
| `runtime/podcast_daily.sh` | +13: dag_supervisor in the daily chain, exit-2 alarm handling | `692ffe4` | intended-improvement | re-pin |

Runbook compliance of PR #8: steps 1–4 were followed — change documented in
the PR, maps patched IN THE SAME commits as the runtime (concept map,
procedural graph, subgraphs all updated), department tests added
(`tests/test_dag_supervisor.py`, +229 lines, plus a fixture), and
`loopfactory.py validate --name podcast` PASSES today
(`{"charter": {"ok": true}, "maps_ok": true, "ok": true}`). Step 6 (re-pin)
was simply never run. The loop has been executing the new process every 30
minutes since 2026-07-31 under shadow, which covers the spirit of step 5 but
not its letter (no recorded re-shadow verdict).

**Podcast verdict: 6/6 intended-improvement. Re-pin; nothing to revert.**

## social — 10 artifacts: merged PR #7 tail + one uncommitted local edit

Nine of ten diverge (vs live pin `ac07729f` @ `57b161d`) because of two
commits merged to master via PR #7 (merge `22c0a6e`): `7789d7c` ("engine
fallback, thumbnail pipeline, Linear listener", 2026-07-29 22:57 -0400) and
`9104e0b` ("auto-create Linear review card after successful run", 2026-07-29
23:06 -0400). The tenth (`social_daily.sh`) additionally carries an
**uncommitted** live-checkout edit.

| artifact | diff vs pin | provenance | classification | recommendation |
|---|---|---|---|---|
| `charter.yaml` | 1 line: `engine_allowlist` widened `[codex_oauth, claude_subscription]` → `[..., glm_oauth]` | `7789d7c` | intended, **but a governance file was edited inside a feature commit with no owner sign-off cited** (contrast `57b161d`, which cites "Ankit 2026-07-28") | owner ratifies the allowlist widening explicitly, or reverts the line, before re-pin |
| `runtime/assemble_context.py` | +1: `thumbnail_url` passthrough into the manifest | `7789d7c` | intended-improvement | re-pin (after maps patched) |
| `runtime/draft_post.py` | +1: `thumbnail_url` passthrough into the draft | `7789d7c` | intended-improvement | re-pin (after maps patched) |
| `runtime/guards.py` | +2: canonicalize optional `thumbnail_url` string | `7789d7c` | intended-improvement | re-pin (after maps patched) |
| `runtime/inventory_backcatalog.py` | +7: thumbnail plumbing | `7789d7c` | intended-improvement | re-pin (after maps patched) |
| `runtime/dispatch.py` | +21/−21: engine-fallback rework | `7789d7c` | intended-improvement | re-pin (after maps patched) |
| `runtime/create_review_card.py` | new file, +147 | `9104e0b` | intended-improvement, **untraced**: no graph node, no `untraced_allowed` rationale | patch maps, then re-pin |
| `runtime/linear_read_comments.py` | new file, +74 | `7789d7c` | intended-improvement, **untraced** | patch maps, then re-pin |
| `runtime/linear_close_issue.py` | new file, +70 | `7789d7c` | intended-improvement, **untraced** | patch maps, then re-pin |
| `runtime/social_daily.sh` | committed: +58/−? (review-card step, engine fallback). Uncommitted on the live box: 4 lines — `DRAFT_ENGINES` reordered codex-first, `QA_ENGINE` `codex_oauth` → `claude_subscription` | committed part: `7789d7c` + `9104e0b`. Uncommitted part: **no commit, no author, no card** | committed part: intended-improvement. Uncommitted part: **unratified local drift** — plausibly a manual hotfix for the codex-engine outage that stalled the loop on 2026-07-31 (QA retries collapse to `qa_engine_unavailable`), but from the record it is indistinguishable from tamper | owner decides: adopt (commit with a change card) or revert to HEAD (`git checkout -- departments/social/runtime/social_daily.sh` in the live checkout). Both engine orders stay inside the charter allowlist and keep QA cross-model |

Runbook compliance of PR #7: step 2 (patch the maps FIRST) was **skipped** —
`subgraphs.json` and `procedural-graph.md` were not touched, so
`loopfactory.py validate --name social` FAILS today with three traceability
errors (the three new Linear files trace to no node). Consequence: the
re-pin is mechanically blocked — `loopfactory.py release pin` refuses while
map QA fails (`loopfactory.py` cmd_release, blocked_by_map_qa) — so social
CANNOT be re-pinned until the maps are patched. The runbook enforces itself
here; no shortcut exists or should be taken.

**Social verdict: 9/10 intended-improvement (with the map-patch step skipped);
1/10 mixed (intended committed change + an unratified uncommitted local edit
requiring an owner adopt-or-revert decision). No pure rot: every divergence
traces to a deliberate, attributable change.**

## Totals

| classification | count |
|---|---|
| intended-improvement, merged via PR, re-pin missed | 15 |
| mixed: intended committed change + unratified uncommitted local edit | 1 (`social/runtime/social_daily.sh`) |
| rot / accident (no attributable intent) | 0 |

The systemic failure is not bad changes — it is that step 6 of
`runbooks/process-change-qa.md` (re-pin) is not on anyone's critical path, and
until this PR nothing alarmed on the gap. Podcast even did the hard part
(maps patched with the code) and still shipped 6 drifted artifacts for 2+ days.

## Recommended owner sequence (post-merge, human-run)

Podcast (unblocked today):

1. Optionally record one clean shadow cycle at HEAD (runbook step 5's letter).
2. `python3 loopfactory.py release pin --name podcast --source-ref 3b7eaf9 --flip`
3. `python3 loopfactory.py qa --name podcast` → drift clean; the new
   manager-tick alarm goes quiet on its own.

Social (blocked until maps are patched):

1. Decide the uncommitted `social_daily.sh` engine swap: adopt (commit, with a
   change card) or revert to HEAD in the live checkout.
2. Ratify or revert the `charter.yaml` `engine_allowlist` widening —
   charter edits are owner sign-off territory.
3. Patch `procedural-graph.md` + `subgraphs.json` for the Linear
   review-card lane (`create_review_card`, `linear_read_comments`,
   `linear_close_issue`) — nodes with impls, or `untraced_allowed` with real
   rationales. `python3 loopfactory.py validate --name social` must PASS.
4. Commit the currently-untracked `releases/ac07729f5044185d/` dir and the
   `current` flip (they are history), or supersede both with the new pin.
5. Re-shadow the changed nodes (simulate sinks, delivered_count==0).
6. `python3 loopfactory.py release pin --name social --source-ref <sha> --flip`
7. `python3 loopfactory.py qa --name social` → clean.

## Appendix — executed QA output (worktree, 2026-08-02)

`python3 loopfactory.py qa --name podcast` (exit 1):

```json
{
  "department": "podcast",
  "ok": false,
  "lint": [],
  "traceability": [],
  "drift": {
    "ok": false,
    "current": "77a3e3d095f3cc07",
    "reason": "live tree differs from the pinned release — process changed without re-pin (run the process-change runbook)",
    "mismatches": [
      "knowledge/concept-map.md",
      "procedural-graph.md",
      "runtime/compare_charter.py",
      "runtime/dag_supervisor.py",
      "runtime/podcast_daily.sh",
      "subgraphs.json"
    ]
  }
}
```

`python3 loopfactory.py qa --name social` (exit 1):

```json
{
  "department": "social",
  "ok": false,
  "lint": [],
  "traceability": [
    "runtime artifact 'runtime/create_review_card.py' traces to no graph node (add a node impl or an untraced_allowed rationale)",
    "runtime artifact 'runtime/linear_close_issue.py' traces to no graph node (add a node impl or an untraced_allowed rationale)",
    "runtime artifact 'runtime/linear_read_comments.py' traces to no graph node (add a node impl or an untraced_allowed rationale)"
  ],
  "drift": {
    "ok": false,
    "current": "31d45329ef6cfeec",
    "reason": "live tree differs from the pinned release — process changed without re-pin (run the process-change runbook)",
    "mismatches": [
      "charter.yaml",
      "runtime/assemble_context.py",
      "runtime/create_review_card.py",
      "runtime/dispatch.py",
      "runtime/draft_post.py",
      "runtime/guards.py",
      "runtime/inventory_backcatalog.py",
      "runtime/linear_close_issue.py",
      "runtime/linear_read_comments.py",
      "runtime/social_daily.sh"
    ]
  }
}
```

Against the live checkout (`--root /mnt/d_drive/repos/loop-factory`,
read-only) the outputs are identical except social's `current` reads
`ac07729f5044185d` — the uncommitted local pin described above.
