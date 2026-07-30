# social Department — F1 Intent Interview

Status: OPEN (not locked). Owner: Ankit. Interviewer: Claude Fable 5 (Claude Code session, 2026-07-28, per interview/INTERVIEW.md).
Rules: answers recorded VERBATIM; interviewer readings marked "to be confirmed, not assumed";
concept map derives ONLY from what is captured here; INTENT LOCK (owner sign-off on the
concept map, with provenance) closes F1 and is always human.

## Q1 — Core outcome owed

**Owner, verbatim (2026-07-28, via Claude Code AskUserQuestion):**
> to give you a full scope and lets revisti this.  we have mybcat.com which ahs the linked in page, facebook page adn tik tok and x and instagram, we have my personal linked in, and we have ptoemtrist buidlgn empiroes podcast on linekd in nad on youtube.  i also have amybcat youtube adn we have the icaregrow.com which is a littl bit of a seperate brand and it jsut has the website and the circle group. so we wan tto prmoote our webmiars for icaregrow.com for now community is a 2nd satege but open to chaigne ithat. we do servies through mybcat.com and acuasitions are soruced seprately adn not really super driect wit hthat yet but maybe that is worh looking at.  success would be discovery calls but not sure the best path thotgethere given the infrastucre we have and what might make sens eto keep ro change etc/

**Interviewer reading (to be confirmed, not assumed):**
Success = discovery calls booked; that is the owed outcome. The owner explicitly does NOT yet
know the best path to calls given the surface inventory, so path design is part of the
department's job (learn-and-adapt loop), not a fixed input. Surface inventory named by owner:
MyBCAT brand (LinkedIn page, Facebook page, TikTok, X, Instagram, MyBCAT YouTube), Ankit's
personal LinkedIn, Optometrists Building Empires podcast (LinkedIn + YouTube), icaregrow.com
(separate-ish brand: website + Circle group only). icaregrow lane promotes WEBINARS for now;
community is stage 2 (owner open to changing that). Services flow through mybcat.com.
Acquisitions are sourced separately today — not directly in scope, but owner flagged "maybe
that is worth looking at."

**Open items raised by this answer:**
- Owner said "let's revisit this" — Q1 final wording re-confirmed at readback (Q16).
- Whether an acquisitions-facing content lane enters scope (v1: out, flagged as future lane).
- Whether icaregrow community promotion moves from stage 2 to stage 1 later (charter change).

## Q2 — Process inventory

**Owner, verbatim (2026-07-28):**
> all langes plus egnament but if orto go mention we also use thse to prompt the podcast so there is podcast push as well and can use it for icargrow and fo rmybcat so several types of items to push in all lates plus want to see ggnamgnets etc

**Confirmed in scope (interviewer reading, to be confirmed at readback):**
1. Podcast repurposing (content derived FROM Building Empires episodes)
2. Podcast push (promoting the podcast itself — new-episode announcements, drive to the series; same push mechanic reusable for icaregrow and MyBCAT items)
3. Blog repurposing (MyBCAT blog → social)
4. Original insights ("random posts and insights")
5. Lead-magnet CTA (competitive analysis, exit valuations, phone analysis → discovery calls)
6. icaregrow webinar promo (webinars now, community stage 2)
7. Engagement lane (owner: "plus engagement" — replies/interaction; external sends, so draft-only/gated until promoted)

**Captured intent beyond the list:**
- "want to see engagements etc" — engagement metrics must be visible to the owner and feed the
  learn-and-adapt loop (sensing/analytics is a first-class function, not an afterthought).
- Several ITEM TYPES flow through the same lanes (podcast items, icaregrow items, MyBCAT items) —
  lanes are channels/processes; item type is a dimension within them.
- Future lane (named, not chartered): acquisitions-facing content.

## Q3 — V1 proving slice

**Owner, verbatim (2026-07-28):**
> the podcast postingf for intial posting is hadnled fby the podcast factory loop you are doign just reengmagement and republsinhign older ones  lets od that as v1

**Interviewer reading (to be confirmed, not assumed):**
V1 proving slice = REPUBLISHING + RE-ENGAGEMENT of OLDER podcast content. Hard boundary
captured: initial posting of new episodes belongs to the existing `podcast` department
(podcast factory loop) — this social department never does initial episode posting.
This is also a Q9 boundary rule: podcast dept owns new-episode push; social dept owns
the back-catalog resurfacing lane.

## Q9-early — Relationship to existing VPS daily-posting cron (asked out of order: evidence conflict)

**Evidence presented:** mybcat-social-content-automation runs autonomously on Lightsail —
daily 7:00 UTC ingest→draft→schedule cron (blog RSS + podcast feed), 6h health check, weekly
self-learning loop, Zernio-only posting via zernio CLI (scout report, 2026-07-28).

**Owner, verbatim (2026-07-28):**
> rebuild from day one but it can live on vps but needs to be able to be manged from one spot as a depormtn etc.

**Interviewer reading (to be confirmed, not assumed):**
Rebuild from day one: ALL posting flows through the social department; the standalone VPS cron
is retired. Execution MAY still run on the VPS (VPS = runner), but management/governance lives
in one spot: the loop-factory department. Owner accepted the posting-gap tradeoff during shadow.
NOTE: actually disabling the live VPS cron is an external/production action — chartered as a
deliberate cutover step requiring owner go at execution time, not performed during F1.
Owner also confirmed (mid-interview message, 2026-07-28): existing repos' process docs get
updated as part of this build, and the department's sensing scope includes tracking the
podcast factory loop's post performance.

## Q4 — Setpoints (operational ceiling + outcome w/ independent sensor)

**Owner, verbatim (2026-07-28, selected option):**
> Higher volume

**Confirmed (owner-selected):** Operational ceiling: max 5 posts/day/surface (X: 8),
max 100 posts/week across all surfaces combined. Outcome setpoint: TBD_MEASURE_IN_SHADOW —
discovery-call and engagement baselines measured during shadow burn-in; owner ratifies real
targets from data. Call sensor: calendar/HubSpot join, independent of department classifiers.

## Q5 — NEVER list (hard floors, every autonomy level, forever)

**Owner, verbatim (2026-07-28, multi-select + free text):**
> PHI/HIPAA floor, Truth & brand floor, Commercial floor,  also you can reserach and mek sure any reserached items are grounded and sources

**Confirmed floors (owner-selected):**
1. PHI/HIPAA: never post patient/client-identifiable info, call recordings, PHI-adjacent
   details; no client names or practice specifics without written approval on file.
2. Truth & brand: never fabricate stats/testimonials/results claims; no medical advice; no
   political/controversial takes; nothing reading as an outcome guarantee.
3. Commercial: never negotiate price/commitments in comments/DMs; never cold-DM; CTAs link
   only to official booking calendars / lead-magnet pages.
4. Grounded research (owner-added, verbatim above): any researched item used in content must
   be grounded and carry its sources — ungrounded/unsourced research claims never publish.

**Open items:**
- Owner did NOT select the "caps & suppression" bundle as a forever-floor. Frequency caps
  still exist as Q4 operational ceilings (changeable by charter, not hard floors). Whether
  "never repost deleted/retracted content" + "opt-outs instant and permanent" should be floors
  → confirm at readback (Q16); interviewer recommends yes.

## Q6 — Budget (weekly ceilings, engine policy)

**Owner, verbatim (2026-07-28):**
> you will only use ouths no api calls but keep recomended use codex and claude

**Interviewer reading (to be confirmed, not assumed):**
Engine policy: OAuth/subscription lanes ONLY, zero per-token API calls (matches the factory
hard rule). Engine allowlist: Codex OAuth + Claude subscription (owner named these two; GLM
not named — treated as not in the allowlist unless owner adds it). Ceilings: recommended
numbers kept — $0/week incremental hard-dollar spend (new dollars escalate), provisional 500
model calls/week, 20 worker-hours/week, fail-closed auto-stop at 80% of any ceiling pending
owner review, ratified after first benchmark batch.

## Q7 — Kill conditions (kill, not pause; incl. faux-work / plate-load)

**Owner, verbatim (2026-07-28, selected option):**
> Std set, coach (Recommended)

**Confirmed (owner-selected):** Kill on: (1) reputation event (platform strike/ban, client or
public blowback traceable to a post); (2) any NEVER-floor breach on a live external action;
(3) gaming its own numbers (fabricated/self-inflated metrics); (4) cost exceeds value 4+
consecutive weeks after burn-in; (5) plate-load — net-adds work to owner's plate 2 consecutive
weeks. Faux-work (on-schedule posting, zero conversions) = COACH first via process change;
kill if still flat 4 weeks after the coaching change.

## Q8 — Problem-type triage (script vs LLM per piece)

**Owner, verbatim (2026-07-28):**
> i like the confirm and when we do qa it needs to enumrate and send back to llm for edtiong to fix so it has a mini loop that self corrects typciall the best way to do this is /ringer and if it fails we need to updat hte prompt to fix in the future

| Piece | Quadrant | Implementation |
|---|---|---|
| Source ingestion (RSS/RAG/back-catalog inventory) | known/known | SCRIPT |
| Resurfacing selection (which old item, when) | known/known | SCRIPT (rank by age + prior engagement) |
| Scheduling/queueing | known/known | SCRIPT |
| Posting via Zernio | known/known | SCRIPT through kernel gateway |
| Engagement metrics collection (incl. podcast-dept posts) | known/known | SCRIPT |
| Post copywriting (repurpose/insight/CTA) | known/judgment | LLM + cross-model QA + gate, draft-only until promoted |
| Research grounding w/ sources (Q5 floor) | known/judgment | LLM + QA; unsourced claims never pass |
| Reply drafts (engagement lane) | known/judgment | LLM, draft-only, human gate (external send) |
| Image prompt generation | known/judgment | LLM + QA; render is SCRIPT |
| Learn-and-adapt (insights → changes) | unknown problem | LLM FLAGS only → proposal card → human via process-change-QA |

**Owner refinement (from verbatim above, to be confirmed):**
- QA is not pass/fail-only: QA ENUMERATES specific defects and sends them back to the LLM for
  an edit round — a bounded self-correcting mini-loop (review and fix are separate actors,
  Ringer-style). Executed via /ringer edit-test lanes.
- If a QA mini-loop fails (content can't converge), the remedy is a PROMPT UPDATE captured for
  the future — prompt evolution is a first-class, recorded process change (goes through
  process-change-QA as a lightweight class), so failures compound into better prompts.

## Q9 (EDGE) — Boundary overlaps

(Primary overlap — the VPS cron — resolved in Q9-early above: rebuild from day one, one
management spot. Podcast-dept boundary captured in Q3: podcast dept owns initial episode
posting; social dept owns back-catalog resurfacing + performance sensing.)

**Owner, verbatim (2026-07-28, selected option):**
> Count all, yield (Recommended)

**Confirmed (owner-selected):** Per-surface caps count ALL posts regardless of author — this
dept + podcast dept + owner's manual/team posts. The department checks the surface's actual
post count via Zernio before scheduling and YIELDS (skips) when a surface is at cap. Flooding
is structurally impossible.

## Q10 (EDGE) — Weird inputs

**Owner, verbatim (2026-07-28, selected option):**
> Freshness gate (Recommended)

**Confirmed (owner-selected):** Every resurfaced item passes a freshness gate before
scheduling: still-true, still-relevant, links-alive, guest/client approval on file if named,
not time-anchored. Anything ambiguous → QUARANTINE for owner review, never guess. Metrics
feed outages → pause sensing and mark data missing, never fabricate or assume zero.

## Q11 (EDGE) — Exceptions per step

**Owner, verbatim (2026-07-28, first response):**
> use rignter for this corect? lets think though this a bit

**Architecture split explained to owner (and accepted):** exception POLICY is deterministic —
a state machine in the department runtime (manager + heal ladder) decides retry/quarantine/
escalate, never a model. Ringer is the muscle INSIDE a step (drafting, QA-enumerate-edit
mini-loop, research grounding) as headless OAuth worker lanes; the harness applies the policy
to whatever Ringer returns.

**Owner, verbatim (2026-07-28, after clarification, selected option):**
> Confirm (Recommended)

**Confirmed (owner-selected):** Transient failures → retry 3× with backoff, then quarantine.
Content QA → max 2 Ringer edit rounds, then quarantine with the enumerated defect list.
Surface at cap → yield silently (normal, not an error). Missing/failed receipt → manager
summoned, heal ladder, pipeline never advances. Every escalation carries exactly ONE concrete
question: Approve / Skip / proposed-fix-yes-or-no.

## Q12 (EDGE) — The subtle failure already lived

**Owner, verbatim (2026-07-28, multi-select):**
> Silent delivery failure, AI-slop voice, Context-blind drafts

**Confirmed — three first-class QA gates (owner-selected):**
1. DELIVERY VERIFICATION: a post's completion receipt is its platform-confirmed post ID/status
   pulled back from Zernio after publish — never the scheduler's claim. (Evidence: sibling
   repos mybcat-social-delivery-fix/-repair-20260721 indicate this failure already occurred.)
2. VOICE GATE: every draft scored against Ankit's canonical style guide + Taste profile
   (anti-AI-slop) inside the cross-model QA loop; failures enter the edit mini-loop.
3. CONTEXT-COMPLETENESS: drafting nodes receive the full source item + brand/offer context
   packet; drafting from fragments is a lint failure; QA cross-checks claims against source.

## Q13 (EDGE) — Gaming / faux work sensor

**Owner, verbatim (2026-07-28, selected option):**
> Confirm (Recommended)

**Confirmed (owner-selected):** Independent sensor set: manager pulls engagement from Zernio
analytics directly (never the dept's self-report); discovery calls counted via calendar/
HubSpot join; published posts verified to exist on-platform; QA is cross-model (drafting
model never reviews itself); weekly digest to owner with links to every published post for
human sampling. These sensors feed the kill controller (Q7).

## Q14 — Escalation targets, SLAs, human gates vs full-auto

**Owner, verbatim (2026-07-28):**
> this shouedl be 100% auotmated evnatualy .  its just me and 24TTL but how logn till no more notificaiotns?

**Confirmed + interviewer reading (to be confirmed):**
- Escalation target: Ankit, sole, all classes. TTL 24h (owner override of 48h default);
  no-reply = DENY and re-file with fresh context, never auto-approve.
- Owner intent: 100% automated eventually — full-auto-with-escalation is the target state,
  reached per action class via the promotion ladder (shadow → draft_only → gated_live →
  autonomous). Draft-only phase runs as WEEKLY BATCH APPROVAL (one sitting, whole queue),
  not per-post pings.
- Answer given to owner: ~14 clean shadow days fixed burn-in, then evidence-based rungs;
  realistically 6–10 weeks to autonomous posting if streaks stay clean; failures reset
  streaks; demotion automatic on breach. Never-zero notifications: floor-breach alarms,
  kill decisions, charter/promotion sign-offs (always-human floor). Weekly digest retained
  per Q13 (removable later only by charter change).

## Q15 — Records & memory (local always; durable backends optional)

**Owner, verbatim (2026-07-28):**
> this is for the company so youc an do ob_company or maybe hub?

**Interviewer reading (to be confirmed, not assumed):**
Durable digests are COMPANY-scoped: destination OB_company preferred, MyBCAT Hub as the
alternative. Known gap, stated not skipped: as of 2026-07-26 OB_company has no connected
server on this surface, and the Hub exposes no generic capture endpoint here — what matters
is what the HEADLESS RUNTIME (VPS) can reach, so F3 investigates wiring the memory seam
(factory/memory.py) to OB_company/Hub. Until wired, sanitized digests accumulate locally and
the unwired seam is surfaced in the weekly digest — never silently dropped, never rerouted to
the personal brain without owner say-so. Content: sanitized weekly learning digests only
(posted/engaged/learned/prompt-changes). Retention floor: no secrets, no Zernio credentials,
no raw DM/comment bodies (shapes and IDs only), nothing PHI-adjacent.

## Q16 — Readback rounds

**Round 1 — full readback presented (20-node map summary + 4 open items). Owner, verbatim (selected option):**
> LOCK + add suppression (Rec.)

**Readings folded:** suppression floor added to C7 as floor 5 (never repost deleted/retracted
content; opt-outs/platform strikes honored instantly and permanently). Open items 2–4
confirmed as read back: C1 wording stands; GLM excluded (Codex OAuth + Claude subscription
only); acquisitions remains a named future lane, not chartered.

## INTENT LOCK

Status: **LOCKED**.
Lock provenance: decided_by Ankit, date 2026-07-28, surface Claude Code AskUserQuestion
(readback round 1), map version v1 + suppression floor.
