# HANDOFF: current state, read this first

_Updated 2026-08-06 by Claude Code. One job per entry, newest at top. Keep it lean:
reference files, do not restate them._

---

## Podcast loop drive contract

### Goal

Podcast estate loops must be target-driven: a run succeeds only if it moved a
funnel number or proved no legal move existed, and sending is the loop's job, not
Ankit's. Today's trigger: all 5 due loops ran, 2 passed cross-model QA, and the
estate produced **zero sends** against a guest pipeline at 3 of a required 6.

Spec (authoritative, 7 units, each with an executed proof and a negative test):
`tasks/2026-08-06-loop-drive-contract.md`

### Current state

**Done**
- Spec approved and committed: `6e7e6eb`, master, **not pushed**. Owner decisions
  D1 to D5 with provenance are in the spec's decisions table.
- Ringer job `loop-drive-contract` round 1 (read-only audit) COMPLETE, 3/3 lanes
  PASS. Validator proven 18/18 against good and bad fixtures before any worker ran.
- **U2d closed with no work.** Gates are 7 code, 5 prose, 0 absent. 6 of 7 code
  gates already return structured reasons; the 7th cannot fail by construction
  (`server/pipeline/referral_extractor.py:403` hardcodes the safe value).
- **U2a resized by lane C.** Draft creation is NOT reachable from a loop run.
  Three creators exist; only `referral_touch_automation.create_gmail_draft` is
  wired to the runner, and only for referral-flywheel when its cross-model QA is
  `QA: PASS`. Blocked 2026-08-06 in `validate_inputs` because Health QA was REVISE.
  Guest-acquisition and booking-readiness have no draft path at all. **U2a should
  generalize the referral path, not build a new one.**
- U5 defect confirmed: `run_podcast_loop.sh:183` skips escalation delivery when the
  `.delivered` marker merely exists, so no same-day rerun can alert.

**In-flight:** nothing.

**Not done:** all 7 units. Zero production code changed. Nothing pushed.

### Mistakes and lessons

| Mistake | Cost | Lesson |
|---|---|---|
| Wrote "13 gates" from a hand count; it is 12 | wrong number reached the spec and a handoff | extract counts programmatically before a number enters a spec |
| Specced 3 revise iterations when the working code does 2 | caught pre-ship | never change a working number for symmetry |
| Specced "no loop creates a draft" and "gates need return-shape upgrades" | both wrong; the audit corrected them | **the recurring one:** measure the code before asserting its shape in a spec |
| Passed `--add-dir A --add-dir B` to claude-lean | lost the plan-setting lane, 2 attempts, 0 tokens | variadic flags eat a trailing positional; use `--flag=value` through any template you do not control |

Two heuristics worth keeping: a sub-5-second failure with zero token spend is a
harness fault, so read the rendered command in the raw worker log instead of
spending the retry. And prove the checker against good and bad fixtures before
spawning a swarm. Round 1 lost one lane to a harness bug and zero to a checker bug.

Both entries are captured in OB_mybcat under `mistake_ledger_v1`
(`ringer-claude-lean-add-dir-swallows-spec`, `spec-shape-asserted-before-measuring`).

### Standing constraints

- **Two repos.** Spec and tooling in `loop-factory`; every line of production code
  to change is in `/mnt/d_drive/repos/podcast`. The `loop-factory`
  `departments/podcast` watchdog is NOT the target: it stays in shadow and never
  sends at any autonomy level. A worker that "fixes" the watchdog has done nothing.
- Engines: OAuth and subscription only (`charter.yaml` `budget.engine_policy`).
  API-billed lanes are forbidden; escalate instead of spending.
- Shadow before any live send. Review before fix, never the same worker for both.
- Workers never commit, push, or send.
- Build order: U5, then U1 + U6 in parallel, then U4, then U2a-c, then U3, then U7.
  U4 is blocked by U1 (re-entry keys on the new verdicts). U3 and U7 are last
  because they change what can edit copy and what can block a send.

### Open sizing question, before U7 is built

`flagship_required_for_human_communication_output` may not be checkable. The other
four U7 gates inspect state in a file or ledger; proving which model wrote a piece
of copy needs a provenance record that may not exist. If nothing writes one, U7 is
four gates plus a plumbing task. Measure before speccing it.

### ONE next pickup action

Compile round 2 of Ringer job `loop-drive-contract` as a fix swarm for **U5**: make
the `.delivered` marker key on a content hash of the escalation body. The check must
reproduce the bug first (write an ESCALATE, deliver it, overwrite with different
text, confirm no delivery under current code) before proving the fixed code delivers
a changed body and still suppresses an identical one. Pitch engines to Ankit before
launching; round 1 needs no rerun.

### Files

| Path | What |
|---|---|
| `tasks/2026-08-06-loop-drive-contract.md` | the spec, 7 units, D1-D5 |
| `ringer/loop-drive-contract/build_r1.py` | manifest generator (carries the `--add-dir=` fix and why) |
| `ringer/loop-drive-contract/checks/audit_check.py` | round 1 validator, proven 18/18 |
| `ringer/loop-drive-contract/manifest-r1-gate-audit.json` | round 1, 3 lanes |
| `ringer/loop-drive-contract/manifest-r1b-lane-a-rerun.json` | lane A rerun, fixed flags |
| `/mnt/d_drive/ringer-work/loop-drive-contract-r1{,b}/` | lane reports and raw worker logs |
| `/mnt/d_drive/ringer-state/artifacts/live/loop-drive-contract.html` | Ringside artifact page |
| `departments/podcast/charter.yaml` | objectives, floors, funnel quotas, engine policy |
| `/mnt/d_drive/repos/podcast/scripts/run_podcast_loop.sh` | the loop runner (U5 target at :183) |
| `/mnt/d_drive/repos/podcast/scripts/obe_draft_voice_qa.py` | the working 2-iteration repair loop (U2 reuses it) |
| `/mnt/d_drive/repos/podcast/server/pipeline/referral_touch_automation.py` | the only runner-wired draft creator (U2a generalizes it) |
| `~/handoffs/2026-08-06-loop-drive-contract.md` | full-detail archival handoff |
| `~/repos/ringer/docs/MODEL-NOTES.md` | engine judgment, incl. this round's harness rule |

### Resume

```bash
cd /mnt/d_drive/repos/loop-factory && claude "Read HANDOFF.md, then tasks/2026-08-06-loop-drive-contract.md, then execute the ONE next pickup action."
```
