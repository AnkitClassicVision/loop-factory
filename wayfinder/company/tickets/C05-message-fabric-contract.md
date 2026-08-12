# C05 — Typed message fabric: schema, adapters, human endpoints

Status: open · Type: grilling · Claimed: — · Blocked by: —

## Question

The message schema (hire request, escalation, status, goal change) as events
on the Layer 0 log; the adapter interface that lets Linear/Telegram/email/
Buzz deliver without becoming truth; human endpoints with priority classes,
SLAs, refile-louder, and a defined saturation behavior (safe-hold, not
louder). What do the existing seams become: decisions_outbox.jsonl,
factory/human_in_the_loop.py, escalate_outbox.py, the charter escalation
block (telegram_ping, linear_card, no_reply_ttl 24h)? Unify or wrap? Also:
what does department A's "hire" message contain so department B can accept
with a traced goal (feeds proof condition 6 arbitration and Layer 3 economy).
