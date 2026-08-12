# Loop Drive Contract: podcast estate loops

Status: **APPROVED (design)** by Ankit, 2026-08-06, Claude Code session.
Scope: the seven estate loops in `/mnt/d_drive/repos/podcast` driven by
`scripts/run_podcast_loop.sh`. NOT the loop-factory `departments/podcast`
watchdog, which stays a sensing department and never sends at any autonomy level.

## Spine

A loop run succeeds only if it moved a funnel number or proved no legal move
existed. Sending is the loop's job, not the owner's.

## Problem, with evidence

On 2026-08-06 all five due loops ran, all returned `VERDICT: NEEDS_ANKIT`, two
passed cross-model QA, and the estate produced zero sends and zero drafts
against a guest pipeline sitting at 3 of a required 6.

Four mechanisms cause it:

1. **`+0` is a passing grade.** The runbook output contract carries
   `Funnel delta this run: outreach <signed count> | ...` with no floor under it.
   The 2026-08-06 guest-acquisition receipt reported `outreach +0; first
   responses +0; conversations +0; pre-call +0; booked +0` and cross-model QA
   returned PASS. The Outcome line 200 lines earlier says "Keep >= 6 guests in
   the recording pipeline" and nothing in the graded contract binds the run to
   that number.
   Evidence: `podcast-guest-acquisition-runbook/RUNBOOK.md:43` and `:248-268`;
   `episodes/_loop_receipts/guest-acquisition-20260806.md`,
   `guest-acquisition-20260806.QA.md`.

2. **The loop that finds the work is forbidden from doing it.**
   `RUNBOOK.md:171`: "Do not draft the reply inside A5. The proposed next action
   is a request to run the existing A4 draft-and-QA path." Nothing on the host
   consumes that request, so an A5 finding cannot schedule an A4 pass. Live
   example: Mike Guelcher supplied bio, links, pronunciation and a headshot at
   2026-08-06 16:19 UTC; the 13:00 rerun classified him `needs_reply`, drafted
   nothing, and the acknowledgement was ultimately sent by hand.

3. **`NEEDS_ANKIT` is a terminal success state.** `run_podcast_loop.sh` runs one
   worker pass, one QA pass, writes the receipt, delivers the escalation, exits
   0. Missing a target is not a retry condition anywhere in the runner.

4. **Cross-loop gates have no owner and no repair drive.** Referral flywheel had
   a verified referral ask ready and recorded
   `{"cards_created": 0, "drafts_created": 0, "sent": false, "reason": "Health QA
   is not PASS", "status": "blocked"}`. Health QA was REVISE. No repair task was
   created, so the same block recurs daily and silently.
   Evidence: `episodes/_loop_receipts/referral-flywheel-20260806.automation.json`,
   `health-20260806.QA.md`.

Related defect found in the same pass: `run_podcast_loop.sh:183`
`[ ! -f "$marker" ] || return 0` skips delivery when
`<loop>-<date>.ESCALATE.delivered` already exists. The 07:00 run created the
marker at 08:36, so the 13:09 rerun escalation naming Mike was never delivered.
Any same-day rerun is currently unable to alert.

## Decisions locked by the owner (2026-08-06)

| ID | Decision | Provenance |
|---|---|---|
| D1 | Zero sent is a failed run, not a reported one. Success is sends. | "if 0 sent that is not success only success is you actually sent them out" |
| D2 | Autosend QA-passed warm outreach and booked-guest logistics. This reaffirms the existing `estate_decisions.autosend_promotion` (charter, signed 2026-07-22) which the runbook never implemented. | AskUserQuestion, send authority |
| D3 | A blocking gate must return why, so the letter is revised and re-gated rather than abandoned. | "the letets need to know what whent wrong and updt he messgae beore sendign" |
| D4 | The loop patches its own outreach templates and style rules when the same gate violation recurs, cross-model QA'd, diff in the receipt. | AskUserQuestion, template repair |
| D5 | The 5 prose-only gates become executable checks (new unit U7). Promoting a gate from instruction to code changes what can block a live send, so it was put to the owner as a question rather than assumed. | "yes to both", 2026-08-06, after the round 1 audit reported 7 code / 5 prose / 0 absent |

## Definitions

**Quota**, wherever this spec says it, means the charter's
`thresholds.funnel_drive` weekly outreach quota prorated to the run's day:
`new_outreach_per_week_rebuild: 8` while `hopper_depth < 6`, otherwise
`new_outreach_per_week_steady: 4`. Today the estate is in rebuild
(pipeline 3 of 6), so the quota is 8/week, roughly 2/day.

**Eligible candidate** means a candidate that passes identity, dedupe, fit,
channel, and context gates and is not suppressed, held, or inside the 4-day
per-contact cadence floor.

**Disqualifier** means the single specific reason a candidate is not eligible,
paired with the file path, thread reference, or ledger field that proves it.

## Requirements

Stable unit IDs. Each unit names its own executed proof. A unit without a
passing proof is not done.

### U1: Verdict becomes computed, not chosen

Replace `VERDICT: OK | NEEDS_ANKIT` with five verdicts derived from the ledger
delta plus the candidate table. The worker may not author its own verdict line;
a deterministic step computes it from the receipt's structured fields.

| Verdict | Condition | Re-enters |
|---|---|---|
| `DROVE` | sends > 0 this run | only while quota unmet |
| `EXHAUSTED` | 0 sends AND every candidate carries an evidence-cited disqualifier | no |
| `CAPPED` | a hard ceiling stopped the run, ceiling named | no |
| `FAILED` | 0 sends with at least one eligible candidate, OR any candidate lacking a cited disqualifier | yes |
| `BLOCKED` | an upstream gate owned by another loop stopped it | no, emits a repair task |

An unproven zero is `FAILED`. "No candidates available" without the enumerated
candidate table with per-candidate disqualifier and evidence is an unproven zero.

Proof: unit test feeding a receipt with `sends: 0` plus one eligible candidate
returns `FAILED`; same receipt with every candidate disqualified and cited
returns `EXHAUSTED`. Negative test: a worker-authored `VERDICT: OK` line in a
receipt with `sends: 0` must be overwritten to `FAILED`, not honored.

### U2: Gates return structured failure

**Measured 2026-08-06: most of this already exists and is simply not wired into
the loops.** `scripts/obe_draft_voice_qa.py` (371 lines) already implements the
repair loop: deterministic mechanical checks via `voice_qa.check_draft` producing
a `failures` list, an LLM judgment returning
`{"verdict": "pass"|"revise", "reasons": [...], "rewrite": "<corrected body>"}`,
a two-iteration auto-revise loop (`for iteration in (1, 2)`) that rewrites the
draft and re-gates, deterministic BCC remediation, a per-iteration log in the
receipt, and a hard fail to human review for non-rewritable placeholder or
DO-NOT-SEND failures. It exits 0 on pass and 2 on fail.

So U2 is not "build a repair loop." U2 is four smaller changes:

- **U2a.** The loops must create the draft. `obe_draft_voice_qa.py` takes
  `--draft-id`, so it can only gate a draft that already exists, and today no
  loop creates one. Wire the A4 draft step so an A5 finding produces a real Gmail
  draft, then hand its id to the gate.
- **U2b.** Consume the gate's exit code. Exit 2 currently ends the path. It must
  instead escalate that one letter carrying every iteration from the receipt's
  `iterations` array, and let the loop continue to the next candidate.
- **U2c.** Keep the existing two iterations rather than inventing a third. The
  implemented count is the contract; do not change a working number for
  symmetry.
- **U2d. Closed by the round 1 audit, 2026-08-06. No work required.** The audit
  classified all 12 runbook gates and found 7 code, 5 prose, 0 absent. Of the 7
  code gates, 6 already return structured failure:
  `crm_write_policy.evaluate_gateway_action` returns `{allowed, category, reason}`;
  `content_qa.run_content_qa` returns
  `{passed, hard_findings, style_findings, llm_verdict, ...}`;
  `capability_preflight` returns `{verdict, blocked, placeholder_mode, ...}`;
  `date_safety.find_date_unsafe_phrases` returns the list of matched phrases. The
  7th, `never_set_nominated_status_on_manual_outreach`, is
  enforcement-by-construction rather than a runtime check:
  `record_nominee_outreach_sent` in `server/pipeline/referral_extractor.py:403`
  hardcodes `podcast_status: outreach-sent` with no branch that could emit
  `nominated`, so it cannot fail and has nothing to report. Verified by hand
  against the file. No code gate needs a return-shape upgrade. The prose gates
  move to U7.

Proof: a deliberately style-violating draft (em dash plus a banned phrase) is fed
through the wired path; the test asserts the gate names the offending failure,
the auto-rewrite clears it, and the send proceeds. Second test: an unfixable
draft escalates after exactly two iterations with both verdicts attached, and the
run continues to the next candidate rather than exiting. Third test: a
placeholder or DO-NOT-SEND draft hard-fails to human review and is never
auto-rewritten into something sendable.

### U3: Template repair on recurrence

The same `{gate, violation}` fingerprint blocking three times across runs opens
a template fix the loop executes itself: patch the outreach template or style
rule, cross-model QA the patch, put the diff in the receipt. The fingerprint
counter resets on a successful fix.

If the same fingerprint returns after a fix, that is a department defect: it
escalates and must not be re-patched.

Scope is `server/pipeline/outreach.py` templates and style rule files only.
`charter.yaml`, `floors.yaml`, autonomy state, send caps, the kill switch, and
every entry in `never:` and `immutable_safety_invariants:` stay human-only,
forever.

Proof: fingerprint counter test at 1, 2, 3 occurrences; the third produces a
patch task, the patch diff appears in the receipt, and a post-fix recurrence of
the same fingerprint escalates instead of patching. Negative test: a patch
attempt targeting `charter.yaml` is refused.

### U4: Re-entry replaces quitting

The runner stops treating process exit as done. On `FAILED`, re-enter the same
day under the same run identity so the receipt and QA file accumulate rather
than scatter. Stop re-entering on `DROVE` with quota met, `EXHAUSTED`, `CAPPED`,
or `BLOCKED`.

Brakes are the existing ceilings, not giving up: 12 sends/day, 5 new contacts/
day, 4-day per-contact cadence floor, 300 touches/week, 1680 worker-minutes/week.
Hitting one produces `CAPPED`, which is a clean success.

Re-entry is bounded only by those ceilings, and every receipt carries the
re-entry count for the day so thrash is visible without needing a separate cap.

Proof: a harness run forced to `FAILED` re-enters and the second pass appends to
the same receipt; a run at the daily send cap returns `CAPPED` and does not
re-enter; a run at the weekly worker-minute ceiling returns `CAPPED` naming that
ceiling; the re-entry count is present in every receipt including first passes,
where it reads 0.

### U5: Escalation marker keys on content

Change `run_podcast_loop.sh` so the `.delivered` marker records a content hash of
the escalation body rather than mere existence. A rerun whose escalation body
differs from the delivered hash delivers; an identical body does not.

Proof: reproduce today's failure first (write ESCALATE, deliver, overwrite
ESCALATE with new text, confirm no delivery under current code), then confirm the
new code delivers the changed body and still suppresses an identical one.

### U6: Cross-loop gates get an owner

A gate sourced from another loop's verdict stops being a silent zero. The
blocked loop returns `BLOCKED: <owning loop>` and emits a repair task assigned to
that loop with a deadline inside the charter's `detection_latency_hours: 26`. The
same cross-loop block persisting two consecutive days escalates as a department
defect.

Proof: with health QA at REVISE, referral flywheel returns `BLOCKED: health` and
a health repair task exists with a deadline; the run is not recorded as a
success. Second test: two consecutive days of the same block produces the defect
escalation.

### U7: The 5 prose gates become executable checks

Owner-approved 2026-08-06 (D5). Round 1 found these five gates enforced only as
instructions written for a model to follow, with nothing executing them:

| Gate | What it must actually check |
|---|---|
| `source_truth_resolved_before_intake` | H1 source-truth packet is current, not stale or blocked, before candidate intake runs |
| `channel_rule_cold_postcard_linkedin_warm_email_text` | a cold prospect is never routed to email or text; warm may be |
| `flagship_required_for_human_communication_output` | the final draft copy came from a flagship model, not a context-gathering one |
| `neutralize_preexisting_nominated_before_draft` | a candidate already `nominated` with a real email is blocked from drafting until a human clears it (CMQA-001) |
| `cross_model_qa_pass_before_done` | the QA verdict exists, came from a different model than the worker, and actually passed |

Each becomes a real check returning `{gate, violation, offending_span, fix_hint}`
so it can join the U2 repair loop. Each must be able to BLOCK, which is exactly
why this needed owner approval: it changes what can stop a live send.

`cross_model_qa_pass_before_done` is the highest-value one and should land first.
It being prose is why 2026-08-06's zero-send runs passed QA at all: the gate meant
to stop a hollow done-claim was itself a suggestion.

Land in shadow first, per the factory hard rule. A newly executable gate that
blocks correctly in shadow but was never watched failing is not proven.

Proof, per gate: a fixture that violates the rule is BLOCKED with the violation
named, and a fixture that satisfies it PASSES. Negative test per gate: watch it
fail before trusting it, and record that the red was observed rather than
asserted. Additional negative test for `cross_model_qa_pass_before_done`: a QA
verdict produced by the SAME model as the worker must block, since self-graded QA
is the failure it exists to catch.

## Non-goals

- No change to the cold-channel rule. Cold stays postcard and LinkedIn, never
  email or text.
- No publish, no CRM writes beyond the two already-approved contact properties
  (`obe_funnel_stage`, `obe_next_action_on`), no spend authority.
- No change to the loop-factory `departments/podcast` watchdog, which remains in
  shadow and never sends.
- No new engine lanes. Subscription/OAuth only, per `budget.engine_policy:
  subscription_oauth_only`. API spend stays forbidden.

## Blast radius and risks

1. **A loop that must send will find a way to send.** A worker could mark a good
   candidate "held" to earn a cheap `EXHAUSTED`. Guard: every disqualifier cites
   evidence, and QA spot-checks at least one disqualifier per run against the
   file or thread it cites.
2. **Self-patched templates drift the owner's voice.** Guard: the diff is in the
   receipt, the QA judge is instructed from the Style Graph, and a
   post-fix recurrence escalates rather than re-patching.
3. **Re-entry burns worker minutes.** Guard: `CAPPED` is a legitimate success
   verdict, so the run stops cleanly at the 1680 min/week ceiling.
4. **More sends means more chances to send something wrong.** Existing guards
   stay: send caps, cadence floor, style validator, cross-model QA,
   `kill_if: reputation_damaging_autosend`, kill switch.

## Decision residue

- **Hardest decision:** whether the loop may patch its own outreach templates.
  It is the difference between a loop that pays the same repair tax forever and
  one that compounds, but it also means the system edits the copy that
  represents the owner. Resolved by bounding scope to templates and style rules,
  requiring the diff in the receipt, and escalating any post-fix recurrence.
- **Alternatives rejected:** draft-and-nag only (leaves the owner as the
  bottleneck, so it does not satisfy D1); split autosend by class (slower risk
  ramp, but the owner had already signed full autosend on 2026-07-22 and
  reaffirmed it here); log recurring gate failures without patching (repair tax
  never goes away).
- **Assumption tested and resolved, 2026-08-06:** the open question was how many
  of the runbook's 12 listed gates are executable code versus prose instructions
  to a model, since prose cannot return a structured failure. Measured rather
  than assumed. The one that matters most, the voice and style gate, is real code
  with a working revise loop (`obe_draft_voice_qa.py`), and four more gate
  modules exist (`date_safety.py`, `crm_write_policy.py`,
  `capability_preflight.py`, `content_qa.py`). U2 shrank from "build the repair
  loop" to "wire the existing one and consume its exit code."
- **Second assumption tested and resolved, 2026-08-06 round 1.** The open question
  was how many gates are model-instruction only. Answer, measured across 3
  independent audit lanes with every citation validated against the real file: 7
  code, 5 prose, 0 absent, and 6 of the 7 code gates already return reasons. U2d
  closed with no work. The 5 prose gates became U7 by owner decision D5.
- **Third finding that changed the plan, from lane C.** The spec originally said
  no loop creates a Gmail draft. Measured truth: three creators exist, and exactly
  one is wired to the runner
  (`server.pipeline.referral_touch_automation.create_gmail_draft`), reachable only
  for referral-flywheel and only when that loop's cross-model QA is `QA: PASS`.
  It was blocked on 2026-08-06 inside `validate_inputs` because Health QA was not
  PASS. Guest-acquisition and booking-readiness, the loops that find owed replies,
  have no draft path at all. So U2a should GENERALIZE the referral path rather
  than build a new one.
- **New least-confident assumption:** that `flagship_required_for_human_communication_output`
  is checkable at all. The other four U7 gates inspect state that exists in a file
  or a ledger, but proving which model wrote a piece of copy needs a provenance
  record that may not be written anywhere today. If no such record exists, that
  gate needs a producer before it needs a check, and U7 is four gates plus a
  plumbing task. Sizing question: does any receipt or artifact record the model
  identity that emitted the final draft body.

## Verification before any done claim

- `python3 loopfactory.py check` (compileall plus full pytest)
- `bash scripts/loop_receipt_check.sh` against a forced-`FAILED` fixture
- One shadow-mode end-to-end pass with sinks simulated and
  `external_actions_taken: []` before any live send
- Spot-check one passing artifact per run, per the estate rule that a green
  check is evidence and not acceptance
