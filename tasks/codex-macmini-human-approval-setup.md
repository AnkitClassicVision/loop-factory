# Codex on the Mac mini — human approval through Open Engine + Telegram

**Hand this to Codex on the Mac mini.** It is the setup and operating instruction
for routing every action that needs Ankit's approval through one channel: an Open
Engine Linear card plus Telegram Approve/Respond buttons.

> **STATUS 2026-08-05: not yet executable.** The mailroom this depends on is mid-
> repair (see `tasks/mailroom-standardization-2026-08-05.md`, rungs 1-3). Running
> these steps today will install the skill correctly but the packet will not
> produce a card until the mailroom lands. Destination for this file is
> `skill-library/skills/base/human-in-loop-oe/references/codex-macmini-setup.md`
> in `AnkitClassicVision/Ankit-open-skills`.

## What you are being asked to do

Any time you are about to take an action that needs Ankit's approval, you do not
ask in chat and you do not act. You raise a packet. A card appears in Linear with
the full context, Ankit's phone buzzes with two buttons, and you wait for a
recorded decision.

Actions that must go through this channel, with no exceptions:

- external sends of any kind: email, SMS, LinkedIn, DM, social post
- CRM, EHR, or finance writes
- publishes, deploys, merges, production writes
- spend above any standing ceiling
- deleting or overwriting anything you did not create in this session
- any change to a governance file, charter, autonomy state, or approval threshold
- anything where you are not sure, which is itself the signal to raise a packet

## Hard rules

1. **You never hold a credential.** No Linear API key, no Telegram bot token, no
   VPS ssh key lives on the Mac mini. You write a packet; another machine does the
   card and the buzz. If a step here seems to need a secret, stop: you are doing
   it wrong.
2. **A decision exists only as a row in `decisions.jsonl`.** Not a chat message,
   not an inference, not silence, not a timeout. No row means no approval.
3. **Never fabricate or assume a decision.** If the transport is down, say the
   transport is down and stop. Reporting "approved" without the row is the single
   worst failure mode of this system.
4. **Timeouts do not approve.** There is no expiry that converts waiting into
   consent. Ankit may answer in ten minutes or ten days; both are fine. The
   channel re-pings him on its own.
5. **No PHI, secrets, tokens, raw patient or contact IDs, or raw message bodies**
   in a packet. Describe the shape of the data, never the data.

## One-time setup

### 1. Get the canonical skill

The skill is a master skill. The open-skills repo is the only source of truth;
never edit the copy on this machine.

```bash
git -C ~/repos/Ankit-open-skills pull --ff-only origin main || git clone git@github.com:AnkitClassicVision/Ankit-open-skills.git ~/repos/Ankit-open-skills
```

### 2. Propagate it to this machine's surfaces

Surfaces scan **flat**: `~/.agents/skills/<skill>/SKILL.md`. The repo stores
skills by family, `skill-library/skills/base/<skill>/`. The family level must be
flattened on the way in or the surface will not see the skill.

> Verified on the Linux box 2026-08-05: neither `~/.claude/skills/base` nor
> `~/.agents/skills/base` exists, and every installed skill sits flat. Do not
> copy the family directory.

```bash
rsync -a ~/repos/Ankit-open-skills/skill-library/skills/base/human-in-loop-oe ~/.agents/skills/
```

Confirm the skill landed on the Codex surface:

```bash
test -f ~/.agents/skills/human-in-loop-oe/SKILL.md && echo INSTALLED || echo MISSING
```

If your Codex build reads the legacy path, mirror it:

```bash
mkdir -p ~/.codex/skills && ln -sfn ~/.agents/skills/human-in-loop-oe ~/.codex/skills/human-in-loop-oe
```

Do not run `skill-library/scripts/sync-skills-to-surfaces.sh` until it is fixed.
As written it rsyncs the whole `skills/` tree and installs to
`~/.agents/skills/base/<skill>/`, one level deeper than any surface scans. That
fix is tracked in the loop-factory mailroom plan, layer L5.

### 3. Create the outbox this machine writes into

```bash
mkdir -p ~/openclaw/outbox && touch ~/openclaw/outbox/decisions_outbox.jsonl
```

### 4. Confirm the return path exists

The Linux box pulls this directory down on its mailroom tick and pushes decisions
back. You do not configure that side. Verify only that the file the decisions land
in is readable from here:

```bash
test -f ~/openclaw/outbox/decisions.jsonl && echo READBACK-READY || echo "READBACK-MISSING (expected until the mailroom repair lands)"
```

### 5. Adapt, do not fork

If Codex on this machine needs different paths or phrasing, that belongs in
`deployment-harness/surfaces/surface-adapters.yaml` in the open-skills repo, under
the `codex` surface, committed and pushed. Editing
`~/.agents/skills/base/human-in-loop-oe/SKILL.md` in place creates drift and the
next sync will silently disagree with you.

## Raising an approval

Append exactly one JSON line to `~/openclaw/outbox/decisions_outbox.jsonl`.

| Field | Required | Meaning |
|---|---|---|
| `packet_id` | yes | Stable unique id you generate. You poll on this. |
| `kind` | yes | `approval` for a yes/no gate, `escalation` for a heads-up |
| `eli5` | yes | One line, plain English, what you are asking. This becomes the card title and the Telegram headline. |
| `department` | yes | `codex-macmini` unless you are acting for a named department |
| `action_class` | yes | What class of thing you want to do: `external_send`, `crm_write`, `publish`, `deploy`, `spend`, `proposal`. Be honest; this is the audit trail. |
| `urgency` | yes | `normal` or `urgent`. `urgent` requires `due`. |
| `due` | when urgent | ISO 8601 deadline. Drives how hard the channel nags. |
| `context` | yes | Object with the evidence Ankit needs to decide, sanitized |
| `source_surface` | yes | `codex-macmini` |

Example, a single line in the file:

```json
{"packet_id":"cdx-2026-08-05-a1","kind":"approval","eli5":"Send the Q3 recap email to the 4 partners on the reviewed list?","department":"codex-macmini","action_class":"external_send","urgency":"normal","context":{"recipients":4,"draft_location":"local draft id 88f2","voice_qa":"passed"},"source_surface":"codex-macmini"}
```

Cadence, so you know what to expect: a `normal` packet re-pings Ankit every 48
hours, backing off after the third to a maximum of two weeks, and never stopping.
An `urgent` packet re-pings at half the time remaining until `due`, with a two
hour floor, then every two hours once past due.

## Reading the decision

Poll `~/openclaw/outbox/decisions.jsonl` for a row whose `packet_id` matches.

```bash
grep -h '"packet_id":"cdx-2026-08-05-a1"' ~/openclaw/outbox/decisions.jsonl 2>/dev/null | tail -1
```

| `decision` | What Ankit did | What you do |
|---|---|---|
| `approve` | Tapped ✅ Approve, meaning approved as-is | Take exactly the action described in the packet. Nothing adjacent, nothing extra. |
| `fix` | Tapped ✏️ Respond and typed | Read `notes`. Revise, then raise a NEW packet with a new `packet_id`. Never reuse the old approval. |
| `skip` | Dismissed | Do not act. Record that it was declined. |

No matching row means no decision. Keep waiting or stop and report that you are
waiting. Both are correct; claiming approval is not.

## Verify your setup before relying on it

Run this once, end to end, on something harmless.

1. Raise a packet with `action_class: proposal` and an `eli5` that says it is a
   setup test.
2. Within 15 minutes, confirm a Linear card exists and Ankit's phone buzzed.
   Ankit confirms the buzz; you cannot verify it from here.
3. Ankit taps ✅ Approve.
4. Confirm a row with your `packet_id` and `"decision":"approve"` appears in
   `decisions.jsonl`.
5. Deliberately break it: poll for a `packet_id` that was never raised and confirm
   you get nothing back and correctly report "no decision" rather than assuming.

Step 5 is not optional. A check nobody has watched fail is not a check.

## What not to do

- Do not ask Ankit for approval in chat and treat his reply as the decision. The
  row is the decision.
- Do not install a Linear or Telegram credential on this machine to "make it
  faster".
- Do not batch several unrelated approvals into one packet. One packet, one
  decision, one action.
- Do not re-raise the same packet id after a `fix`. New id, every time.
- Do not edit the skill in place on this machine.
