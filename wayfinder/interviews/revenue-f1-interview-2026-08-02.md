# Revenue / Lead Follow-Up Department — F1 Owner Interview Record

Conducted: 2026-08-02, in-conversation (polly orchestrator session; tool outage forced in-chat capture, transcribed here on recovery)
Owner: Ankit · Interviewer: polly · Protocol: one question at a time, recommended answer first, verbatim capture, dependency-first, edge grill, readback rounds.

## INTENT LOCK

Signed by owner, verbatim, twice:
1. `intent locked` (2026-08-02)
2. `INTENT LOCKED` (2026-08-02, canonical form)

Lock covers the readback round-1 table plus the round-2 engine-routing amendment, both reproduced below.

## Verbatim owner answers (as typed)

- Q1 (outcome): "accoet and add it has a next etrp and closure if needed" → accept + add next step and closure if needed
- Q2 (trigger/source of truth): "accept and every lead needs next dispotion ao sometirms actuon aoemtiems other based on context and we somt know what we somt know wyt" → accept; every lead needs a next disposition — sometimes action, sometimes other, based on context; we don't know what we don't know yet
- Q3 (read boundary v1): "yes its hibspot as source of truth" → HubSpot is source of truth
- Q4/boundary revision: "yes on 1 no phi restrictions read all and linked in calendar gmail but hubspot has iy all fatjom does have more in direct access but notes in hibspot" → yes on reviewer; sales lane so no patient-PHI restrictions apply to reads; read all incl. LinkedIn, Calendar, Gmail; HubSpot has it all; Fathom has more via direct access but notes land in HubSpot
- Q5→approval posture: "approval us automaric iy shoulf follow wreiting riles snd cintext rukes snd wa prior to send but full auto" → approval is automatic; must follow writing rules and context rules and QA prior to send; but full auto
- Q6/reviewer correction: "i sm named reviewer but no human ideally accept" → Ankit is named reviewer; ideally no human; tripwires accepted
- Q7 (suppression): "accept"
- Q8 (engines/budget) + lead taxonomy: "so jus tme erigh ntow and ther are several type sof leads, mybcat clients, icare grow , podcsat, adn purchase for cvc really teh main difrenc i sparctes to buy and the others all feed each other and icare and pod cas and my bcat coudl feed the cvc but that is not wthe quesion you asked . use these rules"
- Readback amendment: "yup you can also add in gemini auoth as well and kimi autoh or as back up you can use glm api or grok api in opernrouter we want to always proirotize ouths but the api secdond. the last api we use are claude or codex tscine they are hte msot expinse usually"

## Locked charter (normalized)

| # | Element | Content |
|---|---|---|
| 1 | Outcome | Every approved opportunity: named owner, current status, next step with due date, documented outcome, explicit closure/disposition at end. None action-less > 1 business day. |
| 2 | Trigger | Event: opportunity reaches approved stage in HubSpot; daily time-based sweep as backstop. |
| 3 | Source of truth | HubSpot for opportunity existence/stage (aggregates Gmail/Calendar/LinkedIn/Fathom notes). Department records = truth for what the loop did. |
| 4 | Reads | Sales lane — business leads, not patients. Charter permits HubSpot + Gmail + Calendar + LinkedIn + Fathom-via-HubSpot. v1 implementation reads HubSpot only. Patient-side data (charts, recordings, insurance) never in scope by construction. |
| 5 | Lead types | mybcat-client, icare-grow, podcast, cvc-acquisition. First three cross-feed and can feed cvc-acquisition. Early classify-lead-type node; per-type writing rules; disposition `route-to-other-pipeline` with receipt. CVC-acquisition type keeps a longer human gate on the autonomy ladder (owner-accepted rec). |
| 6 | Dispositions | Open vocabulary: take-action / wait-until / nurture / close / route-to-other-pipeline / unknown→human. Unknown branch is mandatory ("we don't know what we don't know yet"); every unknown teaches a candidate new disposition type. |
| 7 | Human owner | Ankit, solo: reviewer on gated rungs; exception-handler + auditor at full auto. |
| 8 | Autonomy | Target = full-auto sends behind deterministic gates (writing/voice rules, context rules, QA prior to send). Ladder, each rung owner-signed: shadow → human-gated live → sampled auto → full auto + tripwires + kill switch. v1 = shadow (external_actions_taken=0). |
| 9 | Tripwires (auto-pause sends, keep drafting, escalate) | any spam complaint or ≥2 opt-outs/24h; bounce >5% trailing 50; QA-fail >20%/day; unknown-disposition >30%; 25 sends/day cap (owner-accepted default); repeat-contact freeze (>N touches/7d). |
| 10 | Suppression (deterministic pre-send gate) | Skip contacts: in any active HubSpot sending sequence/workflow; personally emailed by Ankit recently (Gmail BCC log); in another department's queue; opted out anywhere. Inventory built by agent from HubSpot data (discovery task), not from memory. |
| 11 | Engines | Tier 1 (always first): claude_subscription, codex_oauth, gemini_oauth, kimi_oauth, glm_oauth. Tier 2 backup: GLM / Grok API via OpenRouter. Tier 3 last resort: Claude/Codex API (most expensive). Fallback never silent: auth route stamped in every receipt (loopfactory.auth.route), board-visible, daily API-spend ceiling, repeated Tier-2/3 use escalates. Drafter ≠ QA engine (cross-model QA). |
| 12 | Budget | 50 runs/day, 200 model calls/day, token budget enforced via ledger reserve→commit. Breach = pause + escalate. No latency floor (daily-cadence loop). |
| 13 | KPIs | Freshness (0 stale >1 biz day); QA-pass + audit-pass rates; median speed-to-disposition; 100% closure integrity. Targets baselined after 2 weeks of telemetry. |
| 14 | Never | External sends before promotion; CRM writes before promotion; patient data; silent API fallback; retries past caps; second writable source of truth outside HubSpot + department records. |

## Governance follow-ups created by this interview (owner actions)

1. Hard rule 9 text in repo CLAUDE.md/AGENTS.md predates this decision ("per-token API lanes forbidden") — owner to amend per the Tier-1/2/3 policy above. Charter carries the new policy as owner-captured decision dated 2026-08-02 until then.
2. Readback flags accepted by silence and covered by the lock: "approved opportunity" unit, 1-business-day freshness, 25/day cap, CVC longer gate, KPI baselining.

## Feeds

- F2 charter.yaml authoring: blocked until ticket 009 (factory versioning) resolves so the charter is born v2-shaped.
- Ticket 012 (Revenue stand-up plan): now unblocked (008 + 013 resolved).
- Discovery task queued: HubSpot active-sender inventory for the suppression list.
