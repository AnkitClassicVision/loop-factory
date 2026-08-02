# Ticket 14 draft — OAuth-expiry + engine-outage policy

CANARY: blue paperclip

Status: **ACCEPTED — locked policy** (Ankit, 2026-08-02, all forks as
recommended; see ticket 14 Resolution). Grounded in hard rule 9
(subscription-only, never fall back to metered), ticket 04 (wrappers verify
OAuth internally but record nothing), and the locked ticket-07 contract
(auth_class + status fields). Changes now follow process-change QA.

## What this is (plain English)

When a subscription login (Claude, Codex) expires or an engine is down
mid-run, this policy says exactly what happens instead of a loop silently
failing — or worse, silently switching to a pay-per-token route.

## The policy

1. **Detect at the wrapper.** The engine wrapper's existing auth check
   (e.g. claude-oauth.sh's auth_status probe) runs before every model call.
   On failure it emits the result instead of swallowing it.
2. **Record, don't improvise.** The step writes a run record with
   `status: blocked`, `auth_class: blocked`, `errors: [{code: AUTH_EXPIRED |
   ENGINE_DOWN, detail}]`. NEVER a fallback to a metered/API lane — hard rule
   9; a metered route appearing in any record is a violation flag by contract.
3. **Auth-block ≠ node failure.** The manager treats AUTH_EXPIRED as an
   ENVIRONMENT gate, not a node defect: it does NOT count heal-ladder strikes
   (healing can't fix a login), and it does NOT demote the node.
4. **Escalate with the fix in hand.** One human-in-the-loop outbox item per
   department per lane (not per blocked step — no spam): which lane, since
   when, how many steps queued, and the exact single-line re-auth command to
   run. Engine outage (non-auth): normal retry policy first, heal ladder only
   after retries exhaust.
5. **Resume, don't redo.** Manager re-probes auth each cycle; when restored,
   queued steps resume under their normal idempotency keys (ticket 08) — no
   duplicate work from the blocked period.

## Forks (3)

1. **Blast radius**: does an auth-block pause the whole department or only
   model-calling steps? Recommend: **only model-calling steps** — sensors,
   deterministic nodes, and the manager keep running (they're model-free by
   design), so the board still sees truth while drafting is paused.
2. **Escalation surface**: outbox only, or outbox + Linear card? Recommend
   **both** — the outbox is the contract, the card reuses the existing
   create_review_card path so it shows on the board's approval lane.
3. **GLM lane**: ticket 04 found NO subscription-plan GLM wiring anywhere —
   only a forbidden OpenRouter-metered route. Recommend: **remove GLM from the
   engine roster now** (park in fog: re-add if/when real plan wiring exists).
   Keeping a lane that can only bill per-token is a standing rule-9 hazard.
