# U2a path decision — authored by the coordinator (Opus 5), 2026-08-10

CANARY: blue paperclip

Fable was spawned for this decision and died on "out of usage credits" before
writing anything. Design/architecture is coordinator-retained work anyway; Fable
was an escalation for extra insight, not a dependency. Every claim below is
measured, with file:line. Nothing here is inferred from a spec.

## The finding that resolves the whole question

**The autosend lane is not a conflict. It is implemented, owner-promoted,
hardened, and running live.**

- `scripts/obe_draft_to_linear_bridge.py:888` — when the bridge's QA chain
  passes, the card carries `auto_approved_class: podcast_outreach_qa_passed`
  plus the payload-bound `qa_receipt`. The card text says the mail "will
  AUTOSEND on the next executor run unless a human replies `SKIP`".
- `~/scripts/open_engine_approved_send_executor.py:387-393` — verbatim:
  "Owner promotion (Ankit, 2026-07-22, podcast dept intent interview),
  re-enabled after the Codex review hardening: podcast outreach whose bridge QA
  chain fully passed sends WITHOUT a human APPROVE. Scope is exactly this class."
- Guards that still apply at send time, per the same comment: human SKIP,
  voice_qa, mechanical recheck, recipient deny, SEND-HOLDS recheck, payload-hash
  binding, idempotency, caps.
- Both services are live with real execution: `obe-draft-bridge.service`
  (`--execute --create-markdown-drafts`, ~30 min) and
  `obe-approved-send-executor.service` (`--execute`, ~10 min).

So spec `tasks/2026-08-06-loop-drive-contract.md` is wrong where it says D2's
autosend "the runbook never implemented". D2 is live. The gap is upstream of it.

**Today's bridge journal: `"execute": true, "candidates": 0, "results": []`.**
The machine is armed and starved. Nothing produces a candidate.

## 1. PATH DECISION

**Chosen: (b), with the smallest honest extension — the loop produces a Gmail
draft; the existing bridge does the rest.** Rejected (a) generalizing
`referral_touch_automation`.

Why (b):

- Blast radius. (a) means editing a module that today runs live for referral
  touches under `secret_exec` with a HubSpot key. Its contract REQUIRES a Linear
  card or it raises and deletes the draft
  (`server/pipeline/referral_touch_automation.py:297-320`). Generalizing it
  means loosening that invariant for a second caller — the exact shape of change
  that breaks a working safety path for a new use case.
- Reversibility. Under (b), guest-acquisition's contribution is "a draft
  exists". Stop producing drafts and the funnel stops; nothing else regresses.
  Under (a), rollback means reverting a live send module.
- Double-carding. The bridge is ledger-idempotent, one card per draft id
  (docstring, line 19). Under (b) there is exactly one producer of cards. Under
  (a) two independent paths could card the same candidate — referral reserves in
  its own ledger, the bridge in another, and neither sees the other.
- The gates the estate already enforces (deny recipients/domains at
  `obe_draft_to_linear_bridge.py:74-82`, caps, suppression, style, placeholder
  scan, payload binding) apply automatically to anything entering by (b).

**The honest extension.** The two markdown globs
(`episodes/*/content/guest_release_email.md`, `launch_email.md`,
`obe_draft_to_linear_bridge.py:69-72`) are episode-scoped release/launch mail.
Guest-acquisition invites are not episode-scoped and must NOT be shoehorned into
them — an invite is not a release. The bridge's other intake is a Gmail draft
whose SUBJECT is podcast-scoped (line 821). That is the correct interface:
guest-acquisition creates the draft directly.

Changes:

- NEW `server/pipeline/guest_outreach_draft.py` — creates one Gmail draft for one
  eligible candidate. Reuses, not copies, the primitive
  `referral_touch_automation.create_gmail_draft` (line 279).
- `scripts/run_podcast_loop.sh` — invoke it for `guest-acquisition` after
  `QA: PASS`, mirroring the referral invocation's `secret_exec` containment.
- MUST NOT change: `referral_touch_automation.run_referral_touch_automation`,
  `obe_draft_to_linear_bridge.py`, the executor, any systemd unit, any deny-list,
  any cap.

## 2. THE D2 CONFLICT — dissolved, with ONE question for Ankit

There is no architecture-versus-intent conflict to adjudicate: autosend for
`podcast_outreach_qa_passed` is live and owner-signed. Wave 2 needs no new send
authority. A guest-acquisition draft entering by (b) is treated exactly like any
other podcast outreach draft: bridge QA chain, card, SKIP window, autosend if the
chain fully passed.

That said, this is the moment the class of mail flowing through the lane widens
from "podcast outreach drafted by a human-supervised routine" to "podcast
outreach drafted by an autonomous loop pursuing a quota". The hardening is the
same; the author is not. **One question for Ankit, and I will not answer it for
him:**

> Guest invites drafted by the loop will ride the existing 2026-07-22 autosend
> promotion — QA-passed, hardened, SKIP-able, no APPROVE needed. Do you want the
> loop's first drafts to sit behind your explicit `APPROVE external_send` for a
> trial period instead?

Mechanism if he says yes: the producer omits the autosend marker for its first N
drafts (a one-line difference in what the loop writes), so they land as
approve-required cards. Nothing else changes; it is reversible in one edit.

## 3. GATE ATTACHMENT

In `guest_outreach_draft.py`, before any Gmail call, in this order:

1. `source_truth_resolved_before_intake` — on the revalidation receipt per
   `DECISION-source-truth-freshness.md`, not the 46-day-old authority manifest.
2. `neutralize_preexisting_nominated_before_draft` — before drafting, per its own
   name and CMQA-001.
3. `channel_rule_cold_postcard_linkedin_warm_email_text` — the decisive one here.
   A COLD candidate must never reach a Gmail draft; only warm may. This gate is
   what makes (b) safe at the mouth of a live send lane.

`cross_model_qa_pass_before_done` attaches in `run_podcast_loop.sh`, and it is
**not redundant**. The runner today reads `head -1 "$QA_FILE"` and pattern-matches
the verdict string. It never verifies the reviewer was a DIFFERENT MODEL than the
worker — the gate's whole reason to exist. The runner has `WORKER_ENGINE`/
`WORKER_MODEL` and `QA_ENGINE`/`QA_MODEL` in scope, so it can pass both and the
gate can refuse self-graded QA.

## 4. U2b's EXIT-2 CONTRACT

`scripts/obe_draft_voice_qa.py` exits 0 on pass, 2 on fail
(`obe_draft_voice_qa.py:367`), prints `{verdict, draft_id, receipt_path}`, and its
receipt carries the `iterations` array (built at lines 247, 289, 321, 347).
Exactly TWO revise iterations are implemented (`for iteration in (1, 2)`) and U2c
says keep that number.

On exit 2 the runner must:

- read `receipt_path` from the gate's stdout JSON and lift `iterations`;
- write ONE escalation naming the gate's own failure reasons and the per-iteration
  verdicts. Privacy-safe means: the candidate alias, the iteration count, each
  iteration's mechanical failure names and judgment verdict — never the recipient
  address, never a raw contact/draft id, never the letter body;
- DELETE the failed draft rather than leave it for the bridge to card. A draft
  that failed voice QA must not become a live send candidate. This is the single
  most important line in wave 2;
- CONTINUE to the next candidate, not exit;
- account for it in `loop-drive-v1` as an **ineligible candidate** whose
  `disqualifier` is the gate violation and whose `evidence` is the QA receipt
  path. It is not a send, and it is not exhaustion-proof padding: the verdict
  computer already requires every candidate to be `eligible:false` with a real
  cited path for EXHAUSTED, so a blocked letter cannot manufacture a success
  verdict.

## 5. THE CHECK I MUST WRITE FIRST

The defect this design is most likely to invent: **a voice-QA-failed draft is
left alive in Gmail and the bridge cards it 30 minutes later — a letter the gate
rejected gets autosent.** That is the one that can put bad mail in front of a
guest.

Two executed assertions, both driving `scripts/loop_shadow_run.py`, never grep:

1. Scenario `guest-draft-gate-blocks`: the stubbed gate exits 2. Assert the
   runner (a) issued a draft-delete for that draft id in the intercept log, (b)
   delivered exactly one escalation naming the violation, (c) did NOT exit —
   the next candidate was attempted, (d) the block records the candidate as
   ineligible with the receipt path as evidence, (e) exit code and verdict are
   not a success.
2. Scenario `guest-draft-cold-candidate`: a cold candidate reaches the producer.
   Assert zero Gmail draft-create calls in the intercept log and a gate violation
   naming `channel_rule_cold_postcard_linkedin_warm_email_text`. Cold mail must
   be impossible by execution, not by prose.

Both need the harness to intercept Gmail create/delete and to serve a
configurable gate exit code — a harness extension, written before the worker runs.

## 6. RISKS AND WHAT I DID NOT VERIFY

- ~~I did NOT verify the bridge's caps~~ **MEASURED, and it is a real hole.**
  `_caps_check` (`obe_draft_to_linear_bridge.py:546-554`) enforces exactly one
  thing: the recipient has no card inside `RECIPIENT_COOLDOWN_DAYS = 7` (line 87).
  Plus `MAX_CARDS_PER_RUN = 5` (line 68) per bridge run, and the bridge runs
  every 30 minutes. The charter's **12 outbound/day, 5 new contacts/day, and 300
  touches/week are enforced NOWHERE in this lane.** The per-contact floor is the
  only ceiling present, and at 7 days the bridge is stricter than the charter's 4,
  which is safe.
  **Therefore U2a's producer MUST enforce the volume ceilings itself, before
  creating a draft, and its check must prove it by execution.** A loop with a
  quota-shaped incentive feeding a lane with no daily ceiling is precisely the
  failure Fable warned about when it rated U2a the most dangerous unit. Counting
  source: the same weekly/daily evidence the funnel sensor already reads, not a
  new ledger.
- I did NOT verify what `SEND-HOLDS` contains or how a hold is set.
- I did NOT read `open_engine_approved_send_executor.py` end to end — only its
  autosend branch and docstring.
- The claim "guest invites are warm, not cold" is a business assertion. If
  guest-acquisition sources cold prospects, gate 3 will block every one of them
  and the funnel will not move by email at all. That is correct behavior under
  the charter's cold rule (postcard/LinkedIn only), but it means U2a may deliver
  a correctly-blocked zero. Ankit should know that before it happens.
