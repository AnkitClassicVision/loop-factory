# Sales Department — Concept Map (F2)

Derives ONLY from `../interview/intent-interview.md` (INTENT LOCKED by Ankit,
2026-08-06). Every concept cites its interview question. Every subgraph,
charter field, and (in F3) runtime node traces back here. Change to any
concept's MEANING reopens the interview — never patched in place.

## C1 — The owed outcome (Q1, gap fills at readback)

10 discovery calls per week actually HELD, blended across lanes. HELD means:
attended, decision-maker present, >= 20 minutes. Qualified means one of two
NAMED bars (C10). Attributed means a verified source lane on the held receipt.
Bookings are not the outcome; shows without the decision-maker are not the
outcome; unattributed holds do not count. The held receipt is a separate
artifact from the booking receipt, always.

## C2 — Lane inventory and priority (Q2, Q5, Q6)

RECEIVED (not owned): iCareGrow leads (priority 1), podcast handoff packets
via the S01 contract (2), inbound hand-raisers + website form fillers (4).
OWNED: practices-for-sale prospecting (3), re-engagement of stale/parked
contacts (5), cold services outreach (6 — deprioritized). Strategic context
(Q5, verbatim-anchored): the owner is shifting toward BUYING practices;
iCareGrow, podcast, and practices-for-sale outrank the rest. The 10/week is
blended — any mix counts, no per-lane quotas in v1.

## C3 — The stage chain and DONE (Q4, owner DONE directive)

received -> qualified -> conversation_live -> booked -> held, with
nervous_parked as a FIRST-CLASS parking stage (the owner's "close, maybe
nervous" people — previously smeared invisibly across states). Every stage
carries a binary DONE + proving receipt (machine copy in subgraphs.json
`done:` fields; rendered into each stage folder and the router by the surface
compiler). A stage label is a claim; the receipt is the proof; the SG-SENSE
staleness sensor is the auditor.

## C4 — Floors and the cascade (Q5, Q9)

`funnel:` in the charter declares the goal (held 10/wk) and prior rates;
floors.yaml is DERIVED weekly by the floor compiler (machine-written, heals
never touch). Cold-start cascade: received 196 -> qualified 89 ->
conversation_live 46 -> booked 16 -> held 10 per week. The gap between 196
received/week and the real arrival rate of the received lanes is the DRIVE
SIGNAL for the owned lanes — surfaced as cards, never silently absorbed.
Floors-vs-caps collisions latch and escalate; they never override caps.

## C5 — Context is king: the four subtle-failure gates (Q11, verbatim)

1. Cross-lane double-touch: ONE shared contact identity; suppression applies
   across processes, not per-process. 2. Context-blind follow-up: no draft
   may exist until its context packet (episodes, calls, Circle activity,
   prior threads) is assembled and verified complete. 3. Stale stage lying:
   conversation_live requires two-way evidence within 7 days — a human reply
   or live conversation; opens and clicks never count (locked at readback).
   4. Voice mismatch: every draft passes the owner voice check before it may
   reach the approval queue. Each gate is deterministic and independently
   sensed by SG-SENSE.

## C6 — Gates, escalation, cards (Q12 + card directive)

Human-gated: every outbound send (drafts only, v1 forever until promotion),
CRM writes, broker-scrape target changes, cap/floor-formula changes, the
qualification bars. Full-auto with escalation: intake, dedup, scoring,
context assembly, drafting, ledger writes, floor computation, sensing.
Escalation: owner via outbox -> Telegram + Linear, 48h SLA; unanswered cards
self-park dependents into a daily digest; silence never approves. Every card
carries a make-sense block plus the EXACT approvable actions (owner
directive, 2026-08-06, verbatim in the interview artifact).

## C7 — Budget: month-one, tighter than podcast (Q9)

450 model calls/wk, $0 (subscription-only, API spend fails closed), 840
worker-minutes/wk, 6 outbound actions/day, 2 new contacts/day, 4-day
per-contact cadence floor, 4 broker-scrape sweeps/wk (kernel read-broker
only). Review 2026-09-06. Floors screaming against these caps is information
for the owner, never permission for the machine.

## C8 — Kill and pause (Q10)

KILL (non-restartable without a new human charter decision): attribution
untrustworthy 7+ days; one fabricated/unverifiable held receipt; any
automation BREACHING a cap; cross-process poaching outside the S01 contract;
owner says stop. PAUSE (heals allowed): received lanes starving 2+ weeks;
show-rate < 40%; three consecutive red run-manifest verdicts.

## C9 — Memory (Q13)

Podcast pattern: local records always; HubSpot is the ESTATE's business
truth, never a department memory backend; never recorded: credentials, PHI,
raw message bodies, raw recordings. Sanitized digests only if a backend is
ever deliberately wired.

## C10 — Identity, attribution, dual bars (Q1, Q4, Q6, readback)

One human = one opaque ledger identity, deduplicated across ALL lanes at
intake. Attribution is set at `received` and travels to the held receipt.
Two qualification bars, never blurred: SERVICES (owner/decision-maker at an
ICP-fit optometry practice) and SELLER (owner signaling exit interest within
~24 months). The bar applied is named on every qualification record;
changing a bar is the owner's, forever.

## C11 — The proving slice (Q3)

Received-lead -> held call, shadow first, zero cold sends: intake the three
received lanes with attribution -> qualify -> book -> hold -> held receipt.
Cold outreach is authored only after measured rates exist. This slice
exercises every DONE in C3 and feeds the compiler its first measured
conversions.

## C12 — Practices-for-sale sourcing (Q8, verbatim)

WARM: HubSpot exit signups (confirmed practice-exit interest); Luma
registration answers arriving via email stating exit intent; occasionally
Fathom recordings surfacing intent — as an intent FLAG + opaque id ONLY,
recording content never enters records. COLD: broker-site listings, scraped
through the kernel read-broker under the C7 sweep cap — this scrape IS the
cold feed for the acquisition funnel.

## C13 — iCareGrow arrivals (Q7, verbatim)

HubSpot-tracked competitive-analysis signups and exit signups; iCareGrow
SERIES event registrations on Luma (run with OSS group); iCareGrow CIRCLE
group signups + engagement signals.

## C14 — The future Circle seam (Q7, verbatim)

The iCareGrow Circle content/engagement engine will be a SEPARATE department
whose output is a THIRD cross-department handoff into sales (after
podcast->sales). Out of scope v1; recorded so its interview starts from this
map, not from scratch.
