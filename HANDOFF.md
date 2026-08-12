# HANDOFF: current state, read this first

_Updated 2026-08-07 by Claude Code. One job per entry, newest at top. Keep it lean:
reference files, do not restate them._

---

## FIRST LIVE RESOLUTION EXECUTED + 16h loop outage found and fixed (2026-08-11, night)

- **ANK-348 RESOLVED END-TO-END**: owner Telegram tap 19:05 → decision row
  (verdict_line "APPROVE external_send") → kernel receipt (decision_hash
  0775a355, handler exit 0) → Guelcher's address in CANDIDATE-INBOX.json with
  full provenance (approved_via ANK-348) → close comment on card → live
  replay-safe (second run: nothing open, one receipt). First resolution in the
  system's life. Fixes from the live path: listener tap-grammar (first-token
  verb; podcast `03ff4b2`), plain bare cards (owner correction; `ae65cd4`,
  Taste signal 80). u26 fixture now byte-matches the real tap.
- **DEPARTMENT LOOP WAS DEAD 03:00→20:00** (crash every 30 min, found via the
  missing 19:00 receipt): U12/N15 wired+pinned without registry rows; first
  'unknown' killed Compare; dead loop kept the evidence missing = self-
  sustaining outage. THIRD recurrence of wired-sensor-without-registries
  (ledger 07-31, hopper 08-05). Fixed loop-factory `5cea63c` (FAILURE_CLASSES)
  + `c14beac` (QUESTIONS, MEANINGS — the 19:30 run exposed my table-lookup
  proof as too narrow; full-path synthetic drive now proves all four statuses).
  Both mistakes captured to OB ledger. 20:00 fire = live proof (watcher armed
  at write time). Release re-pin pending after green run.
- Generic Open Engine dispatcher CLAIMED ANK-348 despite --dispatch-mode
  manual and left AGENT FAILED noise — suppress in the wiring round.

---

## r33 + ONE-CARD SLICE LANDED; first live card awaiting owner (2026-08-11, evening)

podcast `9e5d67f` (r33) + `1485011` (slice), feat/one-true-master, UNPUSHED
(ahead 3 with e0db19f). Claude lane FIXED by Ankit (bypassPermissions, both
lanes) and immediately proven: 4/4 PASS attempt 1 (r33 102s; slice lanes
129/232/317s), workers self-executing their checks.

- **r33**: strong bio signals earn a 250-word ceiling; live re-proof returns
  the guest's real 123-word bio verbatim, zero false candidates. u25 PASS
  (watched RED first), u21 regression green, 39 tests.
- **ONE-CARD SLICE** (the audits' resolve-gap): scripts/identity_card.py
  (ONE open card, YOUR MOVE body, ledger row last), identity_card_listen.py
  (human-only grammar, kernel dispatch, closes ONLY on receipt),
  apply_identity_approve.py (first registry handler: verified inbox write
  with provenance). u26 seam check PASS end-to-end offline (watched RED
  pre-build), 29 module tests. Registry live at
  episodes/_loop_state/approve-registry.json.
- **LIVE**: Linear ANK-348 "Guest identity approval: Michael Guelcher"
  created by the real script (--post), payload comment posted, Telegram
  Approve/Fix/Skip prompt delivered via obe_tg_notify.notify_card_created
  (production VPS transport; owner tap posts the human APPROVE via
  tg_approval.py). Awaiting owner reply; monitor armed. On APPROVE: run
  scripts/identity_card_listen.py --root /mnt/d_drive/repos/podcast
  --comments-file <fetched via scratchpad linear_card_io.py comments> and
  verify inbox write + post close comment from out/identity-card-close.md.
- **Wiring not done yet**: runner does not invoke identity_card.py (no
  scheduled card creation, no automatic doorbell); listener not on a timer;
  that is the next Ringer round. Doorbell one-off went through
  notify_card_created because the runner's telegram() path is hook-blocked
  from an interactive session (fine in production where the runner calls it).

---

## r32 bio extraction LANDED; claude lane diagnosed; r33 queued (2026-08-11, ~16:30)

podcast `f3cbad2` (feat/one-true-master, UNPUSHED — local ahead 2 with e0db19f).
r32's three defects fixed and live-proven: u21 v2 PASS at repo, 35 tests green
(module + guest_assets seam), live Guelcher run shows the footer/owner-ask/
signature false candidates GONE. Patch authored by the ringer claude lane
(sonnet), reviewed by the coordinator and hand-applied (see below), byte-identical
to the worker artifact preserved at
/mnt/d_drive/ringer-work/loop-drive-contract-r32/attempt2-preserved.patch.

**Claude-engine "spawn failure" root-caused — NOT claude-in-claude.** Two stacked
harness defects: (1) 0.0s ERROR = stale-worktree collision (`ringer.py` ~7173
refuses an existing taskdir; the dead codex attempt left one — after any FAILed
worktrees run, remove the worktree before rerunning the same manifest);
(2) engine template `--permission-mode acceptEdits` lets a headless worker edit
but never execute — it wrote the full correct fix, could not run pytest, and
honestly declined to fake its summary. **PENDING ANKIT:** flip both claude lanes
to `bypassPermissions` in `~/.config/ringer/config.toml` (this session's edit was
classifier-blocked; command is in the session report). Until then the claude lane
cannot verify its own work; codex locked out until Aug 17.

**New live defect found minutes after landing → r33 QUEUED.** The real guest bio
measures 123 words; the 25..120 ceiling still excludes it even though every r32
signal fires (probe: scratchpad `bio_signal_probe.py`, session
3f9636d7-80e5-4560-b0ce-90656e0f243a). Check
`ringer/loop-drive-contract/checks/u25_bio_length_check.py` written with
self-validating fixture bands, watched RED at `f3cbad2` (L1/L2 fail, guards
green). Manifest `manifest-r33-bio-length.json` linted clean — run it the moment
the claude lane is unblocked. MODEL-NOTES updated (discount the two claude
scoreboard FAILs — both harness).

---

## Podcast guest-acquisition: audit-driven hardening r29-r30 (2026-08-11, afternoon)

podcast `d641ec4` `e0db19f`; loop-factory `501d98e`. feat/one-true-master PUSHED
to origin (through d641ec4). Fable 5 as chief from midday.

**Two fusion audits ran** (packets: /mnt/d_drive/ringer-work/audit-fusion-20260811
and drive-fusion-20260811). Verdicts: day's architecture right, NOT fully
self-driving — the system writes and notifies but does not resolve. Worst defect
(4/4 lanes, reproduced by probe): LinkedIn adapter read one page of 100 chats and
certified complete coverage → false NO_CONTACT_FOUND possible. FIXED r29a
(pagination to cursor exhaustion, fail-closed partial reads, u22). Also fixed:
provider exception text leaking addresses into alias-only receipts (r29b, u23);
identity queue now GATED and two-channel, generator consumes the queue only,
runner invokes it after the feeder, advisory-not-gating (r30, u19v2/u20v2/u24).
Live proof: 14 considered → 10 queued (6 linkedin, 4 email), gates fired on the
identity path for the first time (2 held, 1 cadence, 1 CONTACTED excluded).

**Owner rulings today:** hold FIRST 3 drafts for review then flow (autosend after
is intended); guest-sent material is DIRECT-WRITE to canonical state (Mike
Guelcher's bio/headshot/links/pronunciation written to his episode.json on the
VPS, verbatim, with provenance; backup kept); warm no-address referrals are
queued for email identity, not dropped; drafts route to Linear ANK cards (W2,
not yet built).

**PR work:** five stale podcast PRs processed in worktrees under
/mnt/d_drive/ringer-work/pr-rebase/ — #52 and #44 rebased clean AWAITING
force-push (blocked by git-main-guard hook; Ankit must push or choose fresh-PR
route), #55 SUPERSEDED (recommend close), #54 and #45 BLOCKED on real conflicts
(re-implement as checked rounds). loop-factory #22 left to its agent; #14 shown
to owner with stale-merge-condition caveat.

**W-queue remaining:** W1 approve→execute leg (worst stall: APPROVE is a dead
end, nothing executes approved work). W2 Linear cards with buttons for all
drafts/proposals (buttons grep still 0 in factory/). W4 guest-reply reconciler +
pre-run source-truth refresh + bio extraction fix (three proven defects in
guest_email_context.py: _FIRST_PERSON_RE rejects third-person bios, Outlook
From: quoting not stripped, full-body-extract rejection; U21 exists UNCOMMITTED
and must be fictionalized — carries real contact data). W5 review-queue triage
(2,380 pending, oldest 61d). W6 promotion boundary (systemd runs the mutable
tree). Next scheduled guest-acquisition fire proves the r30 seam end to end.

---

## Podcast guest-acquisition: the gate is live (2026-08-11, later)

Rounds r18-r22, every task PASS on attempt 1. podcast `dda3515` `d22db4d`
`580d6e4` `04b7b39` `f730534` `ef52fbf`; loop-factory `151442b` `55e6c12`
`690b91c` `389986d` `ff65121`. Nothing pushed.

**The gate is wired and measured against real APIs.** The feeder resolves every
surviving candidate through the authority model and selects only
`NO_CONTACT_FOUND`. Live run, real credentials, read-only:
`considered 14, selected 0` with **all four channels reached**, and — the proof
that matters — the SCHEDULED systemd run 08:23:05 to 08:43:20, exit 0, reached
the feeder and receipted the same thing. One record dropped as
`contact verdict CONTACTED; gmail saw the touch`. That is the 2026-08-10
near-miss closed with evidence: real Gmail data independently confirmed what
the intake note claimed, instead of a string match guessing it.

**LinkedIn moved to Unipile** (Ankit's call). Its old source was a JSONL handoff
that has never existed — the producer fail-closes on an ungranted IAM read — so
one unwritable file was holding the whole loop shut. **The feeder is now given
credentials**; it was being invoked with none, which would have produced a
permanent, fully-receipted zero indistinguishable from a quiet week.

Checks U15 (contact gate, incl. alias identity), U16 (LinkedIn/Unipile), U17
(credentials arrive in the feeder process, executed not grepped). Each watched
failing first.

**Four defects, three of them mine, all caught before they mattered:**
1. My U15 fixtures keyed on an invented alias, so the worker abandoned the
   name-derived alias the draft ledger counts. Fixed r19; the check now asks the
   code for the alias and pins it against reordering.
2. My U17 stand-in matched the feeder path anywhere in argv, firing on the outer
   injector before injection; and its harness never defined `GMAIL_FULL_TOKEN`
   under `set -u`. The worker worked around both by contorting production. Fixed
   in the check; the runner invocation is the plain one.
3. My U15 CLI scenario set `PYTHONPATH`, so it never saw that the feeder cannot
   import `server.pipeline` under systemd. The scheduled run refused at the
   import line. Found by running the thing, not reading it.
4. HubSpot refused itself over a `HUBSPOT_PORTAL_ID` it stores and never reads,
   so it reported "credentials unavailable" while holding a valid key. Fixed
   r21; it is reached live now.

**Supply, now measured properly (r22).** The old "13 no email address" was one
bucket hiding two different problems. Live split: **7 cold** candidates queued as
`awaiting_identity` for a LinkedIn identity (Ankit's routing decision), **6 warm**
— referrals, the highest-value ones — dropped because warm routes to email or
text and they have no address, which is an intake data gap, and **1** already
CONTACTED. Nothing new reaches the producer; r22 is reporting only. U11 real Gmail draft still never executed. Progress-based
re-entry stop still unbuilt.

**Identity proposals (r23-r26).** Warm records with no address route to
HubSpot for an email; cold ones to Unipile for a profile. Exactly one plausible
match proposes; two or more propose nobody. Nothing is applied. The queue lives
at `episodes/_loop_receipts/identity-proposals-20260811.json`, 0600, in a
gitignored directory, and carries names deliberately because nobody can confirm
an identity against an alias.

Live: hubspot reached, unipile reached, **5 email proposals**, 8 no match.
Every proposal is confidence 55 on exact-name-match alone — nothing in the
intake notes corroborated CRM company or city.

**LinkedIn name search finds none of the 7 cold candidates** (3 of 7 sampled,
HTTP 200, results=0). The request is correct now; LinkedIn simply does not know
them by name. That fork is Ankit's: capture a LinkedIn URL at intake, use a
different identity source, or accept they stay unreachable.

Three more defects, all measured rather than guessed: Unipile people search is a
POST with a classic/people body, not a GET; its results carry first_name and
last_name, not name, so a successful search would have matched nobody; and
HubSpot 429s on the fifth back-to-back lookup, which one try around the whole
source loop turned into the loss of four good proposals.

Earlier detail: `~/handoffs/2026-08-11-podcast-contact-truth.md`. Supersedes the
2026-08-10 entry below, which is kept for its defect log.

**Done.** U12 absence alarm WIRED AND PINNED as SG-WATCHDOG N15 (release
`4fd7685746c604dc`, full seven-step governed path, `qa` drift-clean). Contact-state
resolver built to Ankit's authority ruling — actual data wins, ledgers are
second order, timestamps arbitrate, a Bee-recorded call after a Gmail thread is
the later truth — plus four adapters (gmail, linkedin, bee, hubspot) built as
four sequential Ringer rounds r14-r17, all PASS on attempt 1, landed and
re-verified against landed code. U13 and U14 checks added.

**Five defects found that green checks had been hiding:** two un-symlinked
ringer checkouts meant the loop could not start its worker at all; U8 and U9
were never invoked by the runner, so every prior green seam measured
hand-supplied inputs; a cold-open near-miss caught by cross-model QA; the
placeholder Gmail reused one draft id, defeating the hold-3 gate; and the time
bounds were work limiters rather than hang detectors.

**Not done.** Wire the resolver into the feeder (the next action). Credentials
for gmail/linkedin/hubspot — bee is the only channel currently reachable, so
the resolver returns UNKNOWN and nothing clears a cold open. U11 real draft
still unexecuted; the chain now reaches QA, which said REVISE twice, correctly.
Candidate supply remains the real constraint: `considered 14, selected 0`.

---

## Podcast guest-acquisition: the silent zero is closed (2026-08-10)

### Goal

Finish the podcast automation so it works in production and cannot fail
silently. Spec: `tasks/2026-08-11-podcast-finish-line.md`.
Full detail: `~/handoffs/2026-08-10-podcast-silent-failure-closed.md`.

### Current state

**Done** (podcast `20647dc`/`16b499a`/`77d4051`; loop-factory `9a25bca`/`2cda307`;
nothing pushed)

- The seam now runs with the REAL revalidator and REAL feeder in the chain, no
  hand-written input: `drafted`, `drafts_created 1`, `tree_unchanged true`.
- **A zero says why.** The feeder dropped records at six bare `continue`
  statements, so an empty inbox and a full-but-ineligible inbox were the same
  observation. It now writes drop accounting; `no_candidate` names its reason
  and its denominator; a shape drift in the feed REFUSES instead of collapsing
  to an empty list that reads as an honest drought.
- **U10:** ceiling counts derived from FUNNEL-LEDGER and passed. The counter
  fails closed and the runner refuses the loop when counts cannot be derived —
  absent counts and being under every ceiling produce the same draft.
- **Ankit's first-draft gate:** hold 3, counted by the producer's own ledger,
  honoured by the bridge, and a corrupt ledger holds everything.
- **U12 absence alarm:** built, five states proven, watched failing first.
  **Deliberately NOT wired** — see below.

**Ankit approved both open decisions the same evening.**

- **U12 is WIRED AND PINNED.** Full seven-step governed path; release
  `4fd7685746c604dc`; `qa --name podcast` drift-clean (`195cdf6`, `a182db6`).
  The node needed re-authoring first: it exited 2 on a non-ok state, and a
  Sense node exiting non-zero mid-chain stops the nodes behind it, so a drought
  would have taken the watchdog down with it.
- **U11 is NOT achieved.** Two blockers, one fixed:
  - FIXED `648d9e7`: two un-symlinked ringer checkouts. Ringer resolves its
    trusted codex-oauth wrapper relative to its own `ringer.py`; the config
    trusts `/mnt/d_drive/repos/ringer`, the runner defaulted to `~/repos/ringer`,
    so **every worker run was refused** and the loop escalated instead. Owner:
    /mnt/d_drive is canonical.
  - OPEN, and this is the "loops don't drive" mechanism: the producer sits
    behind the re-entry loop at `run_podcast_loop.sh:643`, which only exits if
    the worker claims a send or proves exhaustion. The live worker wrote
    `sends: 0` beside candidates it had just marked `eligible: True`, which is
    exactly what the verdict computer refuses, so it re-enters until the weekly
    worker-minutes cap and the producer is never reached. The fix is a RUNBOOK
    change (`sends: N` is a proposal the deterministic producer fulfils, the
    worker sends nothing), not code.
  - W33 worker-minutes: 29.8 min against a 28 min cap. The lane is over budget
    for this week.

**Standing constraint:** candidate supply, not machinery. 13 of 14 live inbox
records have no email. The automation now produces honest, explained zeros.

### Verification

u2a / u8_u9 / u11a / u12 checks all PASS; `validate --name podcast` ok,
traceability clean, drift null; 782 loop-factory + 318 podcast tests pass.
The 24 `tests/test_outbox_*` + reescalate + urgency failures are another live
session's uncommitted work — all 58 pass at a clean HEAD worktree.

---

## Sales budget telemetry + scheduler cadence (2026-08-07)

### Goal

Clear the two owner gates from the held-confirm handoff: the manager's standing
`budget_telemetry_missing` breach, then the scheduler cadence decision. Both
cleared; Ankit picked "both, telemetry then cadence" current-turn.

### Current state

**Done (commits 8ee50fc + 1307f82 + d9937aa; release 12c286d688055e5a, drift-clean, not pushed)**
- `factory/budget_telemetry.py` (department-agnostic, deterministic) derives
  `budget_used.json` from `runs-v2.jsonl` over the manager's rolling 7-day
  window; kernel BudgetBroker ledger merges per-kind max when present
  (`telemetry_ok` property added to the broker). Fail-closed: any evidence
  failure DELETES the output so the breach stands — stale numbers never pass
  as fresh. Metered-lane model calls abort as unpriceable spend. 12 tests;
  full train per change card
  `departments/sales/creation/change-card-2026-08-07-budget-telemetry.md`
  (840 factory + 33 sales tests, live-copy shadow, re-pin --flip, qa clean).
- **Live proof epoch 11:** findings `["pace_under"]` only — breach gone,
  escalations 0, zero external actions.
- **Cadence decided (Ankit, current-turn):** sales chain every 30 min
  (`sales-loop.timer`, matches podcast); cleaner daily 07:30 + ICP enrich
  08:00. Units in `~/.config/systemd/user/` (sales-loop.{service,timer},
  hubspot-cleaner-daily.{service,timer}); registry
  `estate/registry.d/sales.yaml` schedule set. **sales-loop.timer ENABLED by
  Ankit current-turn 2026-08-07** — first run under the unit green (epoch 12,
  findings pace_under only, 1.5s). hubspot-cleaner-daily.timer stays DISABLED
  (gated below); its enable is Ankit's:
  `systemctl --user enable --now hubspot-cleaner-daily.timer`.

**Both gates CLEARED later the same day (Ankit "do it all" directive):**
- Cleaner 400 defect fixed, measured not guessed: `hc_flag_status` never
  existed (setup-hubspot-properties is dry-run by default; flag lifecycle is
  contact-only). 14 contact flag properties created with `--apply`; runner
  guard skips buckets filtering on portal-absent properties
  (hubspot_cleaner `aa40592`, 3 tests, suite 184 green). Acceptance: full
  incremental dry-run rc=0, zero "Bucket search failed".
  **hubspot-cleaner-daily.timer ENABLED**, proven under systemd.
- Headless ICP-enrich loop built (`scripts/enrich_headless.py`,
  hubspot_cleaner `6acfecf`): Tier 1 of LLM_ENRICHMENT_SPEC — fetch →
  classify via Claude SUBSCRIPTION CLI (opus) → strict validation →
  conservative write (hc_llm_* always; hc_contact_role only >= 0.6; never
  funnel/priority overrides). Proof: 15/15 classified+written, 0 rejected,
  write verified by HubSpot read-back, conservative role policy held.
  **icp-enrich-daily.timer ENABLED** (08:00, after 07:30 cleaner).
- Three timers now live: sales-loop *:00/30 (first autonomous fire green,
  epoch 13), hubspot-cleaner-daily 07:30, icp-enrich-daily 08:00.
  Receipts: `hubspot_cleaner/knowledge/enrichment_runs.jsonl`.

**Still open (unchanged from held-confirm handoff):** Perplexity key
rotation, 9 companyless wave contacts, verdict blocking flip, conductor
cutover, P5 promotion, podcast S3/S4 sales taxonomy. Enrich Tiers 2-4
(web/Perplexity/LinkedIn) deliberately unbuilt — v1 sets needs_review
instead of escalating tiers.

### Lessons

- The producer's failure mode IS the alarm: on refusal it deletes its output
  and lets the manager's missing-telemetry breach fire, and the chain
  soft-fails past it so the manager (the enforcement surface) always runs.
- A live-copy shadow (`SALES_STATE_DIR=<copy>`) shadows the NODES only — the
  manager records to `--root`'s live state by design; expect the epoch to
  advance and the drift escalation to land in the real outbox pre-re-pin.

### ONE next pickup action

Tomorrow morning after 08:05, verify the first autonomous cleaner (07:30)
and enrich (08:00) fires: `journalctl --user -u hubspot-cleaner-daily.service -u icp-enrich-daily.service --since 07:00`
plus the newest row of `hubspot_cleaner/knowledge/enrichment_runs.jsonl`.
Then the next value gate is funnel motion: the 9 companyless wave contacts
(needs owner approval to create company records) or Perplexity key rotation.

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

**Done — 4 of 7 spec units plus 2 additions, all on podcast branch `feat/one-true-master`, not pushed**

| commit | what |
|---|---|
| `711914a` | **U5** escalation marker keys on a content digest; fail-closed when the digest cannot be computed |
| `98fc703` | **U1** verdict computed from the receipt's `loop-drive-v1` block, not typed by the worker |
| `d154226` | **U6** a cross-loop block resolves to its owning loop, opens a repair task with a 26h deadline, escalates a department defect on day two |
| `cfc502d` | **emergency fix.** U1 put raw JSON with 36 unescaped `"` into a double-quoted bash assignment in the worker prompt; bash ended the string early and the runner died under `set -u`. Every loop firing after 98fc703 would have failed. `bash -n` passes it clean |
| `2d1ce70` | **shadow harness** `scripts/loop_shadow_run.py` — runs the REAL runner with Ringer, Telegram, the Linear card and secret_exec stubbed |
| `3dd741c` | **U0** (not in the original spec, owner-approved 2026-08-07) no success verdict without corroboration the runner observed |

loop-factory tooling: `64d2512`, `fd300ef`, `54c94f9`. `loopfactory.py check` clean.

**Every unit took two Ringer rounds.** Round one passed its executed check and was
rejected in review, four times running: `--apply` ate the receipt title; the repair
opener sat past the `exit 1` it needed to precede; the digest pipeline masked its own
exit status. Each time the check gained the missing assertion, proven red then green,
before the rebuild ran. **The pattern:** a check written from the DEFECT's failure
modes misses the ones the FIX invents.

**The shadow harness is the answer to that pattern** and it earned its place on first
run by finding `cfc502d` about thirty minutes after I introduced it. Its meta-check
(`shadow_harness_check.py`) grades a harness by handing it the rejected r5 tree and
the accepted r5b tree — identical but for where one call sits — and requiring it to
tell them apart by RUNNING them. A grep-shaped harness fails that check.
**Every remaining unit edits the runner's control flow, so every remaining check must
drive the harness, not grep the script.**

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

**Not done:** U4, U2a-c, U3, U7. Nothing pushed (both repos). No live loop has yet
executed the new verdict path — the checks prove it against the live file, which is
not the same as watching a real loop emit a real block.

### Mistakes and lessons

| Mistake | Cost | Lesson |
|---|---|---|
| Wrote "13 gates" from a hand count; it is 12 | wrong number reached the spec and a handoff | extract counts programmatically before a number enters a spec |
| Specced 3 revise iterations when the working code does 2 | caught pre-ship | never change a working number for symmetry |
| Specced "no loop creates a draft" and "gates need return-shape upgrades" | both wrong; the audit corrected them | **the recurring one:** measure the code before asserting its shape in a spec |
| Passed `--add-dir A --add-dir B` to claude-lean | lost the plan-setting lane, 2 attempts, 0 tokens | variadic flags eat a trailing positional; use `--flag=value` through any template you do not control |
| Wrote the U5 check before knowing the fix would introduce hashing, so nothing covered "digest cannot be computed" | one extra round, ~40k tokens; a green check on a patch that silently drops alerts | when a fix swaps mechanism (existence → hash), ask what NEW way the mechanism can fail and add that scenario before accepting the patch. The old failure modes were covered; the new one was invented by the fix |
| Put raw JSON with 36 unescaped `"` into a double-quoted bash assignment (U1's worker prompt) | **broke every live loop for ~30 min**; `bash -n` passes it clean | a shell string containing an example payload must be escaped or heredoc'd. More important: **only executing the script finds this class**. It is why the shadow harness exists |
| Wrote each unit's check from the DEFECT's failure modes, four times | four extra Ringer rounds, ~180k worker tokens | when a fix swaps mechanism, ask what NEW way the mechanism can fail before freezing the check |
| Told Ankit the Jul-17 Ringer build "writes per-run pages instead of accumulating one page per job", inside a decision he then made | wrong claim shaped an owner decision; corrected same turn | **the recurring one again:** a dry-run's printed paths are not the artifact contract. One `grep -rl artifacts/live` over both trees settled it in seconds and said the opposite |

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
- **`~/.config/ringer/config.toml` is shared, live, and edited by other sessions.**
  On 2026-08-07 it was repointed at 10:01 to a second Ringer checkout and every
  podcast loop died on `engines.codex.bin must use the trusted engines/codex-oauth.sh
  wrapper` (production-publish 10:13); another actor repaired it at 10:30. If a Ringer
  run fails instantly on an engine error, check that file's mtime before debugging
  anything else, and run with `--config <copy>` rather than editing it underneath
  whoever else is working.
- Build order: U5 (done), then U1 + U6 in parallel, then U4, then U2a-c, then U3, then U7.
  U4 is blocked by U1 (re-entry keys on the new verdicts). U3 and U7 are last
  because they change what can edit copy and what can block a send.

### Open sizing question, before U7 is built

`flagship_required_for_human_communication_output` may not be checkable. The other
four U7 gates inspect state in a file or ledger; proving which model wrote a piece
of copy needs a provenance record that may not exist. If nothing writes one, U7 is
four gates plus a plumbing task. Measure before speccing it.

### ONE next pickup action

**Present Fable's plan to Ankit and get sign-off before building anything.**
Ankit's instruction 2026-08-07: stop after U0 and have Fable plan the rest. That
plan has now LANDED and its three actionable claims are verified; both live in
`~/handoffs/2026-08-07-loop-drive-contract-fable-plan.md`. Do not start U4, U2a-c,
U3 or U7 from the spec as written, and start no sending unit (U2a, U2b, U3) without
Ankit's sign-off — those are the ones that can put email in front of a guest.

Fable's plan in one paragraph: Wave 1 = U4 + U2a + U7-module in parallel with
disjoint file ownership; Wave 2 = U2b, with U2c folded into U2b's check instead of
specced as a unit; Wave 3 = U7 flagship producer, then U3. Bound U3's patch surface
to the `TEMPLATES` dict in `server/pipeline/outreach.py` and defer its patch
EXECUTOR until a real fingerprint reaches 2. Exclude the flagship gate from U7's
first cut. Fable rates **U2a the most dangerous remaining unit** — a read/propose-only
lane grows a real Gmail write, behind the D2 autosend that removed the human
APPROVE, at the moment U1/U4 give the worker a quota-shaped incentive to send.

Three measurements taken before the plan is acted on:
- no worker-minutes meter exists anywhere (only U0's ceiling enum names it), so U4
  must BUILD the estate-wide weekly ledger, not read one;
- `process/proofs/source_room_authority_manifest.json` is stamped 2026-06-25, mtime
  Jul 1, and no production loop refreshes it — a `source_truth_resolved_before_intake`
  gate keyed on 7-day staleness (`source_room.py:51`) would block guest acquisition
  permanently. Resolve this before U7's spec freezes that gate's input;
- `flagship_required_for_human_communication_output` is not checkable today (voice-QA
  receipt records no model; action artifact schema forbids extra keys at
  `referral_touch_automation.py:169-171`; codex lane records `model ''` /
  `model_source 'unpinned'`). U7 = four gates + a plumbing task + one late gate.

Standing requirements for whatever comes next, learned the hard way:
- every remaining unit edits `run_podcast_loop.sh` control flow, so every check must
  execute the runner through `scripts/loop_shadow_run.py`, not grep it;
- prove each check red on the defect, green on a correct fix, and red on the obvious
  cheat, BEFORE a worker runs;
- review every passing patch anyway. Four for four so far.

### Files

| Path | What |
|---|---|
| `tasks/2026-08-06-loop-drive-contract.md` | the spec, 7 units, D1-D5 |
| `ringer/loop-drive-contract/build_r1.py` | manifest generator (carries the `--add-dir=` fix and why) |
| `ringer/loop-drive-contract/checks/audit_check.py` | round 1 validator, proven 18/18 |
| `ringer/loop-drive-contract/manifest-r1-gate-audit.json` | round 1, 3 lanes |
| `ringer/loop-drive-contract/manifest-r1b-lane-a-rerun.json` | lane A rerun, fixed flags |
| `ringer/loop-drive-contract/checks/u5_marker_check.py` | U5 validator: reproduces the bug against HEAD before it accepts a fix. Proven on 4 fixtures. Reuse its two-phase shape for U1/U6 |
| `ringer/loop-drive-contract/build_r2.py`, `build_r3.py` | U5 manifest generators; r3 carries the round-2 finding in the spec |
| `/mnt/d_drive/ringer-work/loop-drive-contract-r{1,1b,2,3}/` | lane reports, patches, raw worker logs |
| `/mnt/d_drive/ringer-state/artifacts/live/loop-drive-contract.html` | Ringside artifact page |
| `departments/podcast/charter.yaml` | objectives, floors, funnel quotas, engine policy |
| `/mnt/d_drive/repos/podcast/scripts/run_podcast_loop.sh` | the loop runner; U5 landed here (`711914a`) |
| `/mnt/d_drive/repos/podcast/scripts/obe_draft_voice_qa.py` | the working 2-iteration repair loop (U2 reuses it) |
| `/mnt/d_drive/repos/podcast/server/pipeline/referral_touch_automation.py` | the only runner-wired draft creator (U2a generalizes it) |
| `~/handoffs/2026-08-06-loop-drive-contract.md` | full-detail archival handoff |
| `~/repos/ringer/docs/MODEL-NOTES.md` | engine judgment, incl. this round's harness rule |

### Resume

```bash
cd /mnt/d_drive/repos/loop-factory && claude "Read HANDOFF.md, then tasks/2026-08-06-loop-drive-contract.md, then execute the ONE next pickup action."
```
