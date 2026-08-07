# Sales Department — Procedural Graph (F2)

Machine copy: `subgraphs.json` (v1 lint; stage-shaped subgraphs with `done:` +
`stage:` consumed by the surface compiler). This document narrates; the JSON
decides. Nodes have NO impls yet — F3 authors runtime per the triage below,
and every impl lands in the release tree with an executed QA check.

## Shape: folders ARE the loop

Owner directive (2026-08-06): each workspace folder is one part of the loop,
carrying its routing, its DONE, and its floor. The six funnel stages are the
six primary subgraphs; SG-SENSE is the cross-cutting watchdog. The surface
compiler renders `NN_<stage>/CONTEXT.md` (+ DONE + floor pointer) and
`ROUTER.md` (with the DONE column) from `subgraphs.json` — humans write prose,
never edges.

```
[intake feeds] → 01_received → 02_qualified → 03_conversation_live → 05_booked → 06_held
                     ↑                              ↕
                (dedup, C10)                 04_nervous_parked (park/revive/kill)
   SG-SENSE (daily): 4 context-is-king gates + floors attainment + kill/pause watch
```

## Stage table (DONE column mirrors subgraphs.json — single machine source)

| # | Subgraph | Stage | DONE means (summary) | Receipt | F3 triage |
|---|---|---|---|---|---|
| 1 | SG-RECEIVED | received | one attributed, deduplicated ledger row per arrival | events.jsonl row | SCRIPT: HubSpot/Luma/email/scrape parsers (C13/C12); no models |
| 2 | SG-QUALIFIED | qualified | named-bar decision (services OR seller) with evidence | qualification record | SCRIPT v1 against locked bars; model-scored variant re-opens S3 |
| 3 | SG-CONVERSATION-LIVE | conversation_live | two-way within 7d; drafts only from complete context packets, voice-checked | thread record + packet manifest + voice receipt | LLM (drafting) behind S1/S3/S8 guards; packet assembly SCRIPT |
| 4 | SG-NERVOUS-PARKED | nervous_parked | exit only via executed revive touch OR explicit kill reason | touch receipt / kill record | SCRIPT: park scheduler + revive queue |
| 5 | SG-BOOKED | booked | calendar receipt with time + attendee, linked to identity + attribution | calendar event id | SCRIPT: calendar join |
| 6 | SG-HELD | held | attended, decision-maker present, >=20 min; bar + attribution on receipt; same-day ledger append | held-call receipt (independent of booking) | SCRIPT: calendar/notes join + owner held-confirm card (cards-v2: approve attests decision-maker + >=20 min, 48h SLA, silence never confirms); Fathom intent FLAG only |
| 7 | SG-SENSE | — | all 4 gates ran; floors compared; kill/pause evaluated | daily observations + run record | SCRIPT: deterministic sensors, state-machine classification |

## Guards

Every subgraph is read-only in v1 (drafts land in the approval queue; the
estate dispatch gateway owns sends — Q12): S4/S5/S6/S7 not_applicable with
recorded rationales. S1 (identity) is a live guard in SG-CONVERSATION-LIVE
(the drafting lane joins person entities) and is the intake contract itself in
SG-RECEIVED. S3 + S8 guard the one model-capable, cost-incurring node (the
drafter). Scheduling or choosing a next step inside this department is the
conductor's job at the estate layer — a split-brain defect here.

## Loops (each closes through its receipt, never through a label)

- INTAKE loop: arrivals → dedup → attributed row (daily; C13/C12 feeds).
- QUALIFY loop: new received rows → bar decision → qualified/parked-out.
- CONVERSATION loop: context packet → voice-checked draft → approval queue →
  (estate sends) → reply harvest → staleness truth (comms-loop invariant: the
  return path is the reply harvest; return_sla per escalation contract).
- PARK/REVIVE loop: nervous_parked rows → revive due → draft via conversation
  loop OR kill with reason.
- BOOK/HOLD loop: calendar joins → booking receipt → attended calendar pass →
  held-confirm card (owner attests decision-maker + >=20 min via the outbox
  queue; the confirmation row is the attestation artifact) → held receipt →
  ledger.
- SENSE loop (daily): 4 context-is-king gates, floors attainment vs
  floors.yaml, kill/pause watch → observations → budget telemetry (factory
  producer derives budget_used.json from runs-v2.jsonl; absent file = breach,
  never zero) → compare → cards (make-sense + exact approvable actions).
- DRIVE loop (weekly, via floor compiler): measured rates replace priors →
  floors move (alarm-after) → gaps between floors and received-lane reality
  become drive cards for the owned lanes.

## F3 authoring order (from the proving slice, C11)

1. SG-RECEIVED parsers (iCareGrow HubSpot forms, Luma regs, website forms,
   podcast handoff packets) + ledger append via factory/events_ledger.
2. SG-QUALIFIED bar scorer. 3. SG-BOOKED + SG-HELD calendar joins + receipts.
4. SG-SENSE four gates + floors attainment. 5. SG-CONVERSATION-LIVE packet
   assembler + drafter (last — it is the only model lane and needs the gates
   live first). 6. PFS scrape feed (kernel read-broker) after the warm slice
   proves. Every node: declared inputs → output contract → EXECUTED QA check →
   receipt to runs. Wire effects through kernel/ only.
