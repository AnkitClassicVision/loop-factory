# Fix spec: the open-loop comms gap

Owner: Ankit
Author: Claude Code (coordinator), 2026-08-03
Status: SPEC — no code changed, no governance file edited, nothing sent
Trigger: two guest referrals arrived (Aug 2 12:16 ET, Aug 3 08:00 ET) and no
system replied, recorded, or harvested them.

---

## 0. ELI5

The podcast system is good at asking past guests "know anyone for the show?"
It is not able to hear the answer.

On Jul 31 it asked three past guests. Two people answered with a name. Both
answers are still sitting unread. Meanwhile, this morning at 08:44, the same
system escalated that the guest pipeline is 1 booked against a target of 6.

It is starving while the food sits on the counter.

---

## 1. What is actually broken

The outbound half works. The inbound half breaks in four independent places.
Fixing any one alone changes nothing, which is why this looks like a mystery
instead of a bug.

| # | Break | Location | Effect |
|---|---|---|---|
| B1 | `inbox_review.py` is never scheduled | `scripts/inbox-review-cron.sh` documents a manual `crontab -e`; `scripts/deploy-podcast-vps.sh` never installs it; no systemd timer references it | The only component that reads inbound guest mail and drafts a reply has never run |
| B2 | Referral ledger written to the wrong key | `server/pipeline/inbox_review.py:224` appends to `ledger["received"]` | `server/pipeline/candidate_ingest.py:188` reads `document.get("referrals")`. A recorded referral lands in a key nothing reads |
| B3 | Referral ledger written in the wrong shape | `inbox_review.py:361-367` writes `{received_at, from, thread_id, snippet, draft_id}` | `candidate_ingest.py:212-214` requires `record["state"]` to be in `referral_lifecycle.STATES`. No `state` field means the record is rejected even if B2 is fixed |
| B4 | Reply harvest structurally yields zero | `referral-flywheel` step R3 | The touch tracker detects 16 inbound replies and 8 engaged guests, then R3 processes 0: "its engaged rows require review rather than another automatic request." Detection exists; harvest does not |

Additional defect, lower severity:

| # | Break | Location | Effect |
|---|---|---|---|
| B5 | Schema loss on fresh ledger | `inbox_review.py:223` default is `{"asked": [], "received": []}` with no `schema` key | If `REFERRALS.json` is ever absent, the recreated file lacks `schema: referral-ledger-v1`, and `candidate_ingest.py:180` hard-rejects the whole document |
| B6 | Ask state never advances | Nothing writes `asks[].state`; only `scripts/funnel_status.py` reads it, read-only | Gina Wesley still reads `"state": "asked"` five days after she answered. Reporting permanently understates conversion |

### What is NOT broken (verified, do not "fix")

- `loop_runner.assemble_world` **does** read the referral ledger
  (`loop_runner.py:126, 137, 153`). The note inside `REFERRALS.json` claiming
  it "currently hard-codes referrals: []" is **stale and should be deleted**.
- `referral_lifecycle.py` is a complete, correct state machine.
- The flywheel does **not** spam. It is `read-propose-only`, `external actions
  taken: 0`, and explicitly excludes asked-no-reply guests from re-selection.
  No automatic re-ask will fire at Gina.
- `followup_due_days` in `REFERRALS.json` has **zero consumers** anywhere in
  the repo. It is an inert field, not a scheduled nudge.
- The loop escalated correctly and on time this morning
  (`referral-flywheel-20260803.ESCALATE`, delivered 08:49).

---

## 2. Root cause, stated as mechanism

Every component needed to close this loop exists and is individually correct.
Nothing connects them, and **nothing measures that they are disconnected.**

The system counts what it sends. It counts what arrives. It never compares the
two. A pipeline that asks 57 times, receives 16 replies, and harvests 0 reports
itself as healthy on every stage it does measure, then separately escalates a
guest shortage it cannot explain.

That missing comparison is the actual defect. B1 through B6 are symptoms of it.

---

## 3. Fix units

Each unit has a stable ID, an owner lane, and executed proof. No unit is done
on inspection.

### U1 — Correct the ledger contract (do this first)

Change `_append_referral_ledger` to write a `referral_lifecycle.record_referral`
shaped record into `referrals[]`, preserving `schema` and `asks`.

- Files: `server/pipeline/inbox_review.py`
- Fixes: B2, B3, B5
- PROOF: a test that calls `_append_referral_ledger`, then
  `candidate_ingest.load_referral_records` on the same file, and asserts the
  record round-trips with zero problems.
- RED: assert the test fails against current `main` before the fix. A test that
  has never been watched fail is not a test.

### U2 — Advance ask state on reply

When a reply is recorded for a guest present in `asks[]`, transition that entry
out of `asked`.

- Files: `server/pipeline/inbox_review.py`, `server/pipeline/referral_lifecycle.py`
- Fixes: B6
- PROOF: seed an `asks[]` entry, record a reply, assert the state changed and
  `funnel_status.py` reports the guest as replied.
- RED: same test against current `main` shows the entry still reads `asked`.

### U3 — Delete the stale note

Remove the `note` field in `episodes/REFERRALS.json` claiming `assemble_world`
hard-codes `referrals: []`. It is false and it misled this investigation for
several steps.

- PROOF: `grep -c "hard-codes" episodes/REFERRALS.json` returns 0.
- RED: returns 1 today.

### U4 — Schedule `inbox_review`

Install the job properly instead of as a documented manual step.

- Files: `scripts/deploy-podcast-vps.sh`, new systemd unit + timer
- Fixes: B1
- PROOF: `systemctl --user list-timers` shows the unit; the job's own log file
  exists and has a fresh entry after a forced run.
- RED: confirm the log path is absent before install, so its later presence
  means something.
- GATE: this is the first unit that touches a live send path. Even after U4,
  REFERRAL intent remains draft-only by design
  (`inbox_review.py:41`, `AUTOSEND_INTENTS` excludes REFERRAL). Do not widen
  that list as part of this fix.

### U5 — Close the harvest gap (R3)

Give the flywheel's reply-harvest step a real path from "engaged row" to
"nominee record." Today it defers to a human review that does not happen.

- Files: `podcast-referral-flywheel-runbook/RUNBOOK.md`, R3 step logic
- Fixes: B4
- PROOF: run the flywheel against a fixture with a known inbound referral and
  assert R3 emits exactly 1 nominee.
- RED: same fixture against current logic emits 0.
- NOTE: this is the largest unit and the least specified. See §6.

### U6 — The reconciliation sensor (this is the actual deliverable)

See §4. Without it, this class of failure recurs silently in the next channel.

---

## 4. Process change: the open-loop invariant

**Rule: no outbound ask without a proven return path.**

An "ask" is any outbound message that requests a response — referral requests,
booking requests, guest confirmations, prep-doc chases, review requests, any
future channel. For every ask class the system must be able to answer four
counts:

```
asked  →  replied  →  harvested  →  acted
```

**The invariant: if an upstream count is non-zero and the next count downstream
is zero for longer than that class's SLA, the loop is open and it escalates.**

Applied to today: `asked=57, replied=16, harvested=0`. The drop from 16 to 0 is
the alarm. It has been true for weeks and nothing was watching, because every
component was individually reporting success.

This is a pure counting check. It is deterministic, needs no model, and
generalizes to every comms channel. Per this repo's own rule — "if a decision
is finite and enumerable, it is a state machine, not an LLM" — it belongs in
the department's sensor layer, not in a prompt.

### Where it goes

The podcast department in `loop-factory` is the correct home. It is already a
read-only watchdog over exactly this estate, it already runs every 30 minutes,
and its F1 interview (`interview/intent-interview.md:62`) **already named
"inbox review" as a process it was supposed to watch.** That sensor was never
implemented. This is not new scope; it is the unbuilt half of the original
charter.

Proposed: `departments/podcast/runtime/comms_reconcile_sensor.py`, following the
existing sensor pattern (`hopper_sensor.py`, `pipeline_sensor.py`).

### Charter addition (governance — requires Ankit's sign-off, not an agent edit)

```yaml
comms_loop_invariant:
  applies_to: every outbound ask class
  requires:
    - return_path: the named component that reads the answer
    - return_sla_hours: how long a reply may sit unharvested
  escalates_when: upstream_count > 0 and downstream_count == 0 beyond sla
  never: an ask class may ship without a declared and executed return path
```

### Factory-level guard (department-agnostic, so every future department inherits it)

Add to `loopfactory.py validate`: any node that emits an outbound ask must
declare `return_path` and `return_sla_hours`, or the lint fails. This is what
stops the next department from repeating this exactly.

---

## 5. Governance path — how this actually lands

`runbooks/process-change-qa.md` is explicit, and this spec follows it rather
than routing around it:

1. Change card — this document.
2. Patch the maps first: `procedural-graph.md` **and** `subgraphs.json`.
3. `python3 loopfactory.py validate --name podcast` must PASS.
4. Re-author the affected nodes to match the patched graph.
5. Re-shadow: executed checks, simulated sinks, `delivered_count == 0`.
6. Re-pin: `loopfactory.py release pin --name podcast --source-ref <sha> --flip`.
7. Verify: `loopfactory.py qa --name podcast`, drift clean.

Two hard limits this spec does not cross:

- **The charter edit in §4 is a governance file.** Per `CLAUDE.md`, charters,
  thresholds, and runbooks are humans-only, forever. It needs Ankit's sign-off
  and cannot be applied by an agent or a heal.
- **No autonomy level rises here.** The department stays in shadow. REFERRAL
  replies stay draft-only. This fix makes the system *hear*; it does not make
  it *speak*.

---

## 6. What is not specified yet, honestly

U5 (the R3 harvest gap) is the one unit I cannot size from here. The blocking
question is:

> Should R3 extract the referred person's name automatically from the reply, or
> only flag the thread and let Ankit name them?

Automatic extraction is a model call on inbound mail with a real error mode:
a wrong name becomes outreach to a person who was never referred. Flagging only
is safe, is honest about what the machine knows, and still collapses the work
to one click. That question decides whether U5 is a small unit or a large one,
and it is Ankit's call, not mine.

Everything else in §3 is specified tightly enough to implement.

---

## 7. Immediate, outside the code

Two warm referrals are unread as of this writing. Kevin CC'd the referred guest
directly, so a delayed reply is visible to a third party. Replying is a human
action today, not a fix unit, and no automation in this spec would have sent it
for you — REFERRAL is draft-only by design.

---

## 8. Corrections to the investigation record

Two claims I made before finishing verification were wrong, and both would have
sent work in the wrong direction:

1. I repeated `REFERRALS.json`'s own note that `assemble_world` hard-codes
   `referrals: []`. It does not; `loop_runner.py:126` reads the ledger. I took a
   comment as current state without checking the code it described. That note is
   now U3.
2. I said a follow-up nudge would re-ask Gina on Aug 7. It will not.
   `followup_due_days` has no consumer, and the flywheel is read-propose-only
   with explicit asked-no-reply exclusion. I asserted a dated consequence
   without confirming a consumer existed.

Both are the same error: concluding from a plausible artifact before running the
cheap check that would settle it.
