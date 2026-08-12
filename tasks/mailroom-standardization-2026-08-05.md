# One Mailroom — loop-factory exception → Linear card + Telegram decision

Owner: Ankit. Drafted 2026-08-05. **Status: awaiting owner approval, nothing built.**

## Intent (owner's words, 2026-08-05)

> "every loop factory creation should use the same shape of mailroom as the podcast
> one. lets address that and fix mailroom b so that any exception uses the
> linear/telegram flow and allows me to respond in telegram. also it maybe a while
> before i can actually get to the responses so keep that in mind for a timeout
> perspective, i'm assuming i can come to it whenever? maybe also add in a check in
> if you don't hear back from me. on urgent items that have a deadline more often
> but for less urgent it can be in 2 days recheck and say this is a re-escalation
> so i know."

Follow-up decisions, same session:

- Remove "Acknowledge". Two buttons only: **✅ Approve** (approved as-is) and
  **✏️ Respond** (modify, ignore, or anything else).
- Urgent cadence: scale to the deadline.
- Backoff: back off but never stop.
- **Switch both lanes now.** Podcast escalations move to Approve/Respond in the
  same pass, not after a proving period. The email send-approval lane
  (`--kind card`) is explicitly NOT touched: it keeps Approve-send / Fix / Skip.
- **Close the 13 stale cards** (ANK-290..303) and start clean.
- **Codex desktop on the Mac mini must use this same channel**, via its own
  skill (working name `/human-in-loop-oe`).
- **The skill is a MASTER skill in the open-skills repo**, propagated to any
  surface. Platforms may adapt and pull it in; the skill body stays portable.
  Ankit wants a copy-paste setup instruction he can hand to Codex on the Mac
  mini, covering **all** actions that need human approval, combining Open Engine
  cards and Telegram.

**Respond branch owner — owner decision, Ankit 2026-08-05: Ankit owns it, and the
AI must be able to pick it back up.**

On a `fix` decision the listener must do both halves:

1. **Ownership is human and visible.** The card moves to `Agent Needs Input` with
   Ankit as owner. It does not close, and the re-escalation keeps running against
   it, so an item he responded to but nobody finished cannot go quiet.
2. **The work is resumable by any agent.** The decision row carries everything a
   fresh agent needs with no conversation history: `packet_id`, the original
   packet verbatim, `card_identifier`, `notes` (Ankit's words), `first_raised`,
   and `resume_hint` naming the department and node that raised it. An agent
   picking this up reads one row and knows the ask, the answer, and where it came
   from.

Rejected alternative: silently re-queueing to the originating department. It hides
the item from Ankit's board and makes "who owns this" unanswerable.

## The one shape

Every exception a department cannot self-heal becomes exactly this:

1. One Linear card carrying the full text. Durable record. Never expires.
2. One Telegram prompt with **✅ Approve** and **✏️ Respond**, plus an
   "Open card in Linear" link.
3. Owner taps. Approve posts a human-grammar `APPROVE <action_class>` comment on
   the card. Respond arms a force-reply; the typed words land as `FIX-NOTES:`.
4. `outbox_listen.py` reads the comment, records the decision, acks, closes.
5. No decision yet → the re-escalation node re-arms the buttons and re-pings,
   headed `RE-ESCALATION #n, first raised <date>`.

## Why the buttons need the re-escalation

`tg_approval.py` sets `NONCE_TTL_HOURS = 72`. Telegram buttons die after three
days. The card never dies, so the owner can always reply in Linear. But "come to
it whenever" only holds for the buttons if something re-arms them. Normal cadence
of 48h sits inside the 72h window, so the buttons stay live by construction. This
is the load-bearing reason the re-escalation node is not optional.

## Verified facts this design rests on

| Claim | Evidence |
|---|---|
| Two-way Telegram exists and works | `/opt/podcast-webhook/scripts/tg_approval.py` on the VPS, reached over ssh by `podcast/scripts/obe_tg_notify.py:58-80` |
| Buttons carry a single-use nonce | `tg_approval.py:221-232` `parse_callback`, `v1\|a\|ANK-123\|<nonce>` |
| Owner reply becomes a card comment | `tg_approval.py:208-219` `build_fix_notes_comment`, first line `FIX-NOTES:` |
| `outbox_listen` already parses that grammar | `factory/outbox_listen.py:47` `^(APPROVE\|SKIP\|FIX)\w*\b`; `FIX-NOTES:` matches, agent markers are skipped at `:296` |
| An Approve tap cannot send email | `open_engine_approved_send_executor.py:448` hard-filters `[send-approval]` in title; `:369-370` refuses without `gmail_draft_id`. Loop-factory cards have neither. |
| Ledger is split-brain today | push → `~/.config/loop-factory/card_ledger.jsonl` (36 rows); listen → `departments/social/state/card_ledger.jsonl` (2 rows) |
| Pusher watches a file that does not exist | `outbox_push.yaml` watches only `departments/social/state/approval_queue.jsonl`; absent |
| 36 ledger rows are only 13 real cards | ANK-293 ×13, ANK-296 ×10, ANK-292 ×3; all 13 confirmed live in Linear |
| No human decision has been lost | 11 of 13 cards have zero comments; ANK-292 and ANK-297 carry only agent-marked comments |

## Work

### L1 — the decision buttons (owner: switch both lanes now)

Edit `/mnt/d_drive/repos/podcast/scripts/tg_approval.py`. Verified 2026-08-05:
local and VPS copies are byte-identical (md5 `163c130533af90b18d25cf91aff0137e`,
627 lines), so the repo copy is the source of truth.

- `--kind escalation` buttons change from 👍 Ack / ✏️ Respond to
  **✅ Approve / ✏️ Respond**. This is the shape both podcast and loop-factory use.
- `--kind card` is **untouched**. That is the live email send-approval lane and it
  genuinely needs Approve-send / Fix / Skip. Do not merge the two.
- Approve on an escalation posts `APPROVE <action_class>`, where action class comes
  from a new `--action-class` flag defaulting to `proposal`. **Never**
  `external_send` unless the caller explicitly passes it. That keeps the send
  executor out by construction, not merely by its title filter.
- New `--reescalation N` and `--first-raised <iso>` render the re-escalation header.
- Back up the file before editing; `--dry-run` first.

**Deploy gate.** `scripts/deploy-podcast-vps.sh` ships committed code only
(`git archive HEAD`) and stops/starts VPS workers. The consumer
`podcast-tg-approval.service` is active and must be restarted to pick up the
change. So the VPS half needs, in order: owner-approved commit in the podcast
repo → owner-approved deploy → consumer restart → live tap test. This is a
separate approval from the loop-factory work and must not be bundled with it.

### L2 — factory code (department-agnostic, per repo rule)

- `factory/human_in_the_loop.py` — `escalate()` accepts `urgency`
  (`normal`|`urgent`) and `due` (ISO). Default `normal`. Carried in the packet.
- `factory/outbox_push.py`
  - After card creation, arm the Telegram buttons via the new notify path.
  - Keep the plain one-way ping as fallback when the button path fails, mirroring
    `run_podcast_loop.sh:186-189`. An alert is never lost to a button outage.
  - Ledger row gains `urgency`, `due`, `first_raised`, `reescalation_count`.
  - **Dedup by `card_identifier`**: when `linear_control_ticket.py` returns a card
    already open in the ledger, attach to that row instead of appending a new one.
    This is what stops one reply becoming 13 decisions.
- `factory/reescalate.py` — NEW. Scans the one ledger for `open` rows, computes
  next-ping, re-arms with a fresh nonce, increments the counter.
  - normal: 48h, 48h, 48h, then ×2 each time, capped at 336h (2 weeks). Never stops.
  - urgent with `due`: half the remaining time, floor 2h. Past due: every 2h.
  - One ping per card per tick, always.
- `factory/outbox_listen.py` — any recorded decision moves the row out of `open`,
  which is what stops the re-escalation. No separate stop flag.

### L3 — kill the split brain

- `~/.config/loop-factory/outbox_push.yaml` becomes the single canonical config.
- Delete `departments/social/state/outbox_config.yaml`.
- Repoint `social-decisions.service` at the canonical config.
- Watches: add `departments/podcast/state/decisions_outbox.jsonl` and
  `departments/outreach/state/decisions_outbox.jsonl`; drop the absent
  `approval_queue.jsonl`.
- New `loop-factory-reescalate.timer`, hourly (the 2h floor makes hourly enough).
- Optional: rename the two units from `social-*` to `loop-factory-mailroom-*`,
  since the lane is estate-wide now, not social.

### L4 — every future department gets this for free

- `factory/scaffold.py` registers `departments/<name>/state/decisions_outbox.jsonl`
  in the canonical config at F0 scaffold time.
- `runbooks/factory-pipeline.md`: mailroom wiring becomes a named F3 step and a
  Definition-of-done item.
- `templates/`: the department template ships the `escalate()` call.

### L5 — `human-in-loop-oe`, the master portable skill

Owner decision 2026-08-05: this is a **master skill living in the open-skills
repo**, propagated to every surface. Platforms may render or adapt it; they never
fork it.

**Canonical source (source of truth):**
`~/repos/Ankit-open-skills/skill-library/skills/base/human-in-loop-oe/SKILL.md`
(GitHub `AnkitClassicVision/Ankit-open-skills`). `base/` is the correct family:
it already holds `open-engine-card-format` and `open-engine-loop-design`, the two
skills this one composes with.

**Propagation, existing machinery, nothing new invented:**

| Step | Mechanism |
|---|---|
| Surface registry | `deployment-harness/surfaces/surfaces.yaml` — hermes_default, hermes worker profiles, `~/.claude/skills`, `~/.agents/skills`, `~/.gemini/skills`, project-local |
| Per-platform adaptation | `deployment-harness/surfaces/surface-adapters.yaml` — the codex adapter already mandates `codex_adapter_no_claude_only_tooling`, so the skill body must carry **no Claude-only allowed-tools assumptions** |
| Push to local surfaces | `skill-library/scripts/sync-skills-to-surfaces.sh` — **broken for this purpose, must be fixed first, see below** |
| Reach a second machine | that machine `git pull`s the private repo and installs with its own paths |

**Propagation defect found 2026-08-05, blocks L5.** `sync-skills-to-surfaces.sh`
rsyncs `skill-library/skills/` wholesale into `~/.claude/skills/` and
`~/.agents/skills/`. Because the repo groups skills by family, that installs to
`~/.claude/skills/base/<skill>/SKILL.md`. Evidence that this is wrong:

- neither `~/.claude/skills/base` nor `~/.agents/skills/base` exists;
- every installed skill sits flat at `~/.claude/skills/<skill>/`;
- `open-engine-card-format`, cited as canonical in the global `AGENTS.md`, is
  present in the repo's `base/` family and installed on **no surface at all**.

So the propagation lane Ankit is asking to standardize on has never actually
carried a skill. Fix: flatten the family level during sync, and make the script
fail loudly when a destination already holds a same-named skill from a different
family. Until that lands, install the single skill by direct rsync of the skill
directory, not the tree.

**Portability contract the skill body must satisfy:**

- Plain shell and Python only. No Claude-only tool names, no `allowed-tools`
  assumptions, no MCP dependency.
- Every path comes from one config block at the top, so a platform overrides
  paths without touching logic.
- Degrades honestly: if the transport is unavailable, the skill says so and
  refuses to claim an approval. It never fabricates a decision.

**Design rule: a remote surface never holds a credential.** No Linear token, no
Telegram bot token, no VPS ssh key on the Mac mini. It writes a packet; the
mailroom on this box does the card and the buzz. Same rule loop-factory already
enforces on departments (`kernel/capabilities.py`), extended across machines.

Skill contract, four steps:

1. **Raise** — append one JSON line to the local outbox
   (`packet_id`, `eli5`, `urgency`, `due`, `action_class`, `source_surface`).
2. **Wait** — the mailroom does card, Telegram buttons, re-escalation.
3. **Read back** — poll `decisions.jsonl` for the row matching `packet_id`:
   `approve` / `skip` / `fix`, plus the owner's notes.
4. **Never** claim the owner approved anything without that row.

**Transport, resolved 2026-08-05.** `clawd/scripts/sync_to_macmini.sh` is
push-only (this box → `100.79.154.17`, key auth, BatchMode). There is no return
leg. Resolution: add a **pull leg on this box** that rsyncs the Mac mini's outbox
directory down on the mailroom tick. This reuses the SSH trust that already
exists in that direction and puts no new credential on the Mac mini.

Deliverable: `references/codex-macmini-setup.md` inside the skill — the
copy-paste instruction set Ankit hands to Codex on the Mac mini.

### L6 — retire the 13 stale cards

ANK-290 through ANK-303, raised Aug 2-3, no human comments, content now stale.
Post one agent-marked comment on each explaining the mailroom was rewired, then
close. Agent-marked so the listener ignores it and no decision is fabricated.
Owner's phone stays quiet; the new lane starts with a clean board.

## Verification (executed, reported with real output)

1. `python3 loopfactory.py check` — compileall + full pytest.
2. New unit tests: cadence math, backoff ladder, urgency scaling past due,
   ledger dedup by `card_identifier`, re-escalation counter, TTL-vs-cadence
   invariant (normal cadence must stay under `NONCE_TTL_HOURS`).
3. `outbox_push.py --dry-run` and `outbox_listen.py --dry-run` against the real
   ledger; output shown to the owner before anything live.
4. One live end-to-end on a single throwaway card: owner taps Approve, we show
   the comment landing in Linear and the decision recorded in `decisions.jsonl`.
5. `PLACEHOLDER_MODE=1` and `OBE_TG_NOTIFY_DISABLE` honoured so pytest never
   buzzes the owner's phone (`obe_tg_notify.py:46-55` precedent).

## Risks

| Risk | Mitigation |
|---|---|
| Changing `--kind escalation` changes the live podcast escalation buttons in the same pass. Owner chose this knowingly (2026-08-05) over a staged rollout. | `--kind card` (email send) untouched and asserted byte-identical in review; file backed up; `--dry-run` first; live tap test on one throwaway card before the next podcast loop fires |
| The 13 stale cards | Closed with an agent-marked comment, not re-armed. Zero buzzes. |
| Re-escalation becomes spam | Backoff ladder, one ping per card per tick, 2-week cap |
| `APPROVE external_send` leaking onto a non-send card | New default action class is `proposal`; `external_send` only when explicitly passed |
| Silent-success returns | Add the stall check: a tick where zero watch files exist is an error, not a 0 |

## Receipts

### Rung 1 — COMPLETE 2026-08-05, local artifact only (not committed)

| What | Proof |
|---|---|
| Card-level grouping replaces row-level polling | `factory/outbox_listen.py` `_open_groups`; one reply now settles every ledger row sharing a card |
| FIX is owned by Ankit and resumable | `_resume_context` puts `owner`, `notes`, `resume_hint`, `packet_text`, `first_raised`, `card_url` on the decision row; card parks in `Agent Needs Input`, does not close |
| Push records what a resumer needs | `factory/outbox_push.py` ledger row gains `packet_text` and `first_raised` |
| TEST passes | `python3 -m pytest tests/test_outbox_listen.py tests/test_outbox_push.py tests/test_factory_human_loop.py -q` → 29 passed |
| Full repo green | `python3 loopfactory.py check` → 671 passed, CHECK PASS |
| **RED watched failing** | Both new tests run against the pre-change file via `git stash`: `test_one_reply_settles_every_ledger_row_sharing_a_card` and `test_fix_decision_row_carries_everything_an_agent_needs_to_resume` both FAILED, then passed after restore |
| 13 stale cards retired | 13 cards, 36 ledger rows, 0 failures. Agent-marked comment plus `Agent Done`. Spot-check ANK-293: state `Agent Done`, comment present, agent-marked so the listener skips it |
| Ledger has no open rows | `Counter({'retired': 36})` |
| Split brain killed | `social-decisions.service` repointed to `~/.config/loop-factory/outbox_push.yaml`; `departments/social/state/outbox_config.yaml` deleted; live run exit 0 |

`retired` is deliberately not `decided:*`. Nobody decided those 13; the channel was
broken. Backups: ledger and unit file in the session scratchpad.

**Deferred out of rung 1 on purpose.** The podcast and outreach watch paths are NOT
yet added to the canonical config. Turning them on before the buttons work would
create a pile of cards Ankit cannot act on from his phone. They go in with rung 2,
and their cursors must be seeded at current line count so historical rows do not
flood.

## Owner gates still required

1. **Commit to the podcast repo** — the VPS deploy ships committed code only.
2. **Deploy to the VPS** — `deploy-podcast-vps.sh` restarts live workers.
3. **Restart `podcast-tg-approval.service`** — the button consumer.

Everything in L2, L3, L4, L5 and L6 is local to this box and needs no such gate.

## Not in scope

No department is promoted out of shadow by this work. No external send, CRM,
finance or publish path is opened. Governance files stay human-only.
