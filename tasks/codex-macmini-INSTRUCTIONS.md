# Codex on the Mac mini — human approval setup

**Hand this whole file to Codex on the Mac mini.**

Every path and fact below was verified on the live system on 2026-08-05. The
companion reference `human-in-loop-oe/references/codex-macmini-setup.md` covers
the principles; this file gives you the concrete commands for THIS machine.

---

## What you are being set up to do

You are on a Mac mini. When you are about to take an action that needs Ankit's
approval, you do not ask in chat and you do not act. You append one JSON line to
a local outbox file, then wait.

A separate Linux machine pulls that file, turns each new line into a Linear card,
and buzzes Ankit's phone with two Telegram buttons: **✅ Approve** and
**✏️ Respond**. His tap posts a comment on the card, and a listener records it as
a decision you can read back.

You hold **no credential**. No Linear token, no Telegram token, no ssh key. If a
step here looks like it needs a secret, you are doing it wrong: stop and say so.

---

## Actions that must go through this channel

- external sends of any kind: email, SMS, LinkedIn, DM, social post
- CRM, EHR, or finance writes
- publishes, deploys, merges, production writes
- spend above any standing ceiling
- deleting or overwriting anything you did not create in this session
- changes to a governance file, charter, autonomy state, or approval threshold
- anything where you are unsure, which is itself the signal to raise a packet

---

## Step 1 — get the skill

The canonical source is the private open-skills repo. Never edit the copy on this
machine; edits belong upstream or they vanish on the next sync.

```bash
git -C ~/repos/Ankit-open-skills pull --ff-only origin main || git clone git@github.com:AnkitClassicVision/Ankit-open-skills.git ~/repos/Ankit-open-skills
```

## Step 2 — install it flat

Surfaces scan flat: `~/.agents/skills/<skill>/SKILL.md`. The repo groups skills
by family, so the family level must be stripped. Copy the one skill directly:

```bash
mkdir -p ~/.agents/skills && rsync -a ~/repos/Ankit-open-skills/skill-library/skills/base/human-in-loop-oe ~/.agents/skills/
```

Confirm it landed:

```bash
test -f ~/.agents/skills/human-in-loop-oe/SKILL.md && echo INSTALLED || echo MISSING
```

If your Codex build reads the legacy path, mirror it:

```bash
mkdir -p ~/.codex/skills && ln -sfn ~/.agents/skills/human-in-loop-oe ~/.codex/skills/human-in-loop-oe
```

## Step 3 — create the two files

The Linux box reaches this machine at `ankitpatel@100.79.154.17` and syncs
`/Users/ankitpatel/clawd`, so the mailroom paths live under that root.

```bash
mkdir -p /Users/ankitpatel/clawd/outbox && touch /Users/ankitpatel/clawd/outbox/decisions_outbox.jsonl /Users/ankitpatel/clawd/outbox/decisions.jsonl
```

## Step 4 — point the skill at them

```bash
export HITL_OE_OUTBOX_FILE=/Users/ankitpatel/clawd/outbox/decisions_outbox.jsonl && export HITL_OE_DECISIONS_FILE=/Users/ankitpatel/clawd/outbox/decisions.jsonl
```

Persist those two exports in your shell profile so they survive a restart.

---

## Raising an approval

Append exactly one JSON line to `HITL_OE_OUTBOX_FILE`.

| Field | Required | Meaning |
|---|---|---|
| `packet_id` | yes | Unique id you generate. You poll on this. |
| `kind` | yes | `approval` for a yes/no gate, `escalation` for a heads-up |
| `eli5` | yes | One plain line. Becomes the card title and the phone headline. |
| `department` | yes | `codex-macmini` unless acting for a named department |
| `action_class` | yes | `external_send`, `crm_write`, `publish`, `deploy`, `spend`, or `proposal`. Be honest; it is the audit trail. |
| `urgency` | yes | `normal` or `urgent`. `urgent` requires `due`. |
| `due` | when urgent | ISO 8601 deadline. Drives how hard the channel nags. |
| `context` | yes | Object with the sanitized evidence needed to decide |
| `source_surface` | yes | `codex-macmini` |

Example, one line in the file:

```bash
python3 -c 'import json,os;row={"packet_id":"cdx-2026-08-05-a1","kind":"approval","eli5":"Send the Q3 recap email to the 4 reviewed partners?","department":"codex-macmini","action_class":"external_send","urgency":"normal","context":{"recipients":4,"draft_id":"local-88f2","voice_qa":"passed"},"source_surface":"codex-macmini"};open(os.environ["HITL_OE_OUTBOX_FILE"],"a").write(json.dumps(row)+"\n")'
```

**Cadence, so you know what to expect.** A `normal` packet re-pings Ankit every
48 hours, backing off after the third ping toward a two-week maximum, and never
stopping. An `urgent` packet re-pings at half the time remaining until `due`,
floored at two hours, then every two hours once past due. Cadence never
authorizes anything.

---

## Reading the decision

```bash
python3 -c 'import json,os,sys;pid=sys.argv[1];f=os.environ["HITL_OE_DECISIONS_FILE"];rows=[json.loads(l) for l in open(f,encoding="utf-8") if l.strip()] if os.path.exists(f) else [];m=[r for r in rows if r.get("packet_id")==pid];print(json.dumps(m[-1]) if m else "no decision")' cdx-2026-08-05-a1
```

| `decision` | What Ankit did | What you do |
|---|---|---|
| `approve` | Tapped ✅ Approve, approved as-is | Do exactly the action in the packet. Nothing adjacent. |
| `fix` | Tapped ✏️ Respond and typed | Read `notes`. Revise, then raise a NEW packet with a NEW `packet_id`. Never reuse the old approval. |
| `skip` | Declined | Do not act. |
| `no decision` | He has not answered | Keep waiting, or stop and report that you are waiting. Both are correct. |

---

## Hard rules

1. **A decision exists only as a row in `decisions.jsonl`.** Not a chat message,
   not an inference, not silence.
2. **Waiting never becomes consent.** There is no timeout that turns a
   non-answer into an approval. He may answer in ten minutes or ten days.
3. **Never fabricate a decision.** If the file is missing or the transport is
   down, say so and stop. Reporting "approved" without the row is the single
   worst failure this system can have.
4. **No PHI, secrets, tokens, raw patient or contact IDs, or raw message bodies**
   in a packet. Describe the shape of the data, never the data.
5. **One packet, one decision, one action.** Never batch unrelated approvals.
6. **Never edit the skill in place.** Fix it upstream in the open-skills repo.

---

## Verify before you rely on it

Run all five. Step 5 is not optional.

1. Raise a packet with `action_class: proposal` and an `eli5` saying it is a
   setup test.
2. Confirm exactly one new line landed in your outbox.
3. Within ~15 minutes of the pull leg being live, Ankit confirms a Linear card
   exists and his phone buzzed. You cannot verify that from here.
4. Ankit taps ✅ Approve. Confirm a row with your `packet_id` and
   `"decision":"approve"` appears in your decisions file.
5. **Deliberate failure:** poll a `packet_id` that was never raised and confirm
   you get `no decision` and correctly refuse to act. A check nobody has watched
   fail is not a check.

---

## Known state as of 2026-08-05

The Linux side of this channel is live and proven end to end: an escalation
became Linear card ANK-312, Ankit's tap posted `APPROVE proposal`, and the
listener recorded `decision: approve`.

**The one piece still landing is the pull leg** that carries this machine's
outbox to the Linux box. Until Ankit confirms it is running, steps 1 and 2 of
Verify will work and step 3 will not. Do not treat that as a broken contract:
it means your packet is queued, not lost, and you must keep waiting rather than
assume approval.
