# social Department — Concept Map (F1 output, the WHY)

Status: **LOCKED** — derives ONLY from `interview/intent-interview.md`.
INTENT LOCK provenance: decided_by Ankit, 2026-07-28, Claude Code AskUserQuestion
surface, readback round 1 ("LOCK + add suppression (Rec.)"), map v1 + suppression floor.

Rules:
- Every node cites the interview question it derives from (Q1..Q16 + mid-interview messages).
- Nothing appears here that the owner did not say or confirm at readback.
- The procedural graph (F2) must trace every node back to THIS map; anything
  that traces to nothing is a lint failure.

## Spine (one sentence)

For Ankit, the social department turns the company's existing content estate
(Building Empires back catalog, MyBCAT blog, original insights, lead magnets,
icaregrow webinars) into ONE governed, always-on Zernio posting engine across
all surfaces — back-catalog republish/re-engagement first — measuring
engagement with sensors independent of itself (including the podcast
department's posts), learning through governed prompt/process changes, so
discovery calls get booked while removing work from Ankit's plate, never
adding to it.

## Nodes

| ID | Concept | From | Notes (owner's words where possible) |
|---|---|---|---|
| C1 | Outcome owed: DISCOVERY CALLS booked (services flow through mybcat.com). Path to calls is not fixed — designing the path is part of the department's job. Engagement + icaregrow webinar registrations = leading indicators, not the owed artifact | Q1 | "success would be discovery calls but not sure the best path" — Q1 wording re-confirmed at readback |
| C2 | Surface inventory: MyBCAT brand (LinkedIn page, Facebook, TikTok, X, Instagram, MyBCAT YouTube); Ankit personal LinkedIn; Building Empires podcast (LinkedIn + YouTube); icaregrow.com (separate-ish brand: website + Circle only). icaregrow lane promotes WEBINARS now; community is stage 2 | Q1 | "icaregrow… promote our webinars for now community is a 2nd stage" |
| C3 | Chartered lanes (7): podcast repurpose; podcast push; blog repurpose; original insights; lead-magnet CTA (competitive analysis, exit valuations, phone analysis); icaregrow webinar promo; engagement lane (draft-only/gated). Item type (podcast/icaregrow/MyBCAT) is a dimension within lanes. Future named lane: acquisitions content | Q2 | "all lanes plus engagement… several types of items to push in all lanes" |
| C4 | V1 proving slice: REPUBLISH + RE-ENGAGEMENT of older podcast content. Hard boundary: initial posting of new episodes belongs to the podcast department, forever out of this department's hands | Q3 | "initial posting is handled by the podcast factory loop… you are doing just reengagement and republishing older ones" |
| C5 | Architecture: REBUILD FROM DAY ONE. All posting flows through this department; the standalone VPS daily cron is retired at a gated cutover (owner go at execution time). Execution may run ON the VPS (VPS = runner); governance/management lives in ONE spot: this department | Q9-early | "rebuild from day one but it can live on vps but needs to be able to be managed from one spot" |
| C6 | Setpoints: operational ceiling 5 posts/day/surface (X: 8), 100 posts/week total, ceilings not quotas. Outcome setpoints TBD_MEASURE_IN_SHADOW — baselines from shadow, owner ratifies from data. Call sensor: calendar/HubSpot join, independent of dept classifiers | Q4 | owner selected "Higher volume" |
| C7 | Hard floors (NEVER, forever): (1) PHI/HIPAA — no patient/client-identifiable content, no client names/practice specifics without written approval on file; (2) Truth & brand — no fabricated stats/testimonials/claims, no medical advice, no political takes, no outcome guarantees; (3) Commercial — no price/commitment negotiation in comments/DMs, no cold DMs, CTAs only to official booking/lead-magnet pages; (4) Grounded research — researched items must be grounded and carry sources or they never publish; (5) Suppression — never repost deleted/retracted content; opt-outs and platform strikes honored instantly and permanently (added at readback, owner-approved) | Q5, Q16 | "any researched items are grounded and sources"; lock option "LOCK + add suppression" |
| C8 | Budget: OAuth/subscription lanes ONLY, zero per-token API. Engine allowlist: Codex OAuth + Claude subscription. $0/week incremental hard-dollar spend (new dollars escalate); provisional 500 model calls/wk, 20 worker-hrs/wk; fail-closed auto-stop at 80% of any ceiling; ratify after benchmark batch | Q6 | "only use oauths no api calls but keep recommended use codex and claude" |
| C9 | Kill set (kill, not pause): reputation event; floor breach on live external action; gaming its own numbers; cost>value 4+ consecutive weeks post burn-in; plate-load net-add 2 consecutive weeks. Faux-work (posts but no conversions) = COACH via process change first, kill if flat 4 weeks after | Q7 | owner selected "Std set, coach" |
| C10 | Script-vs-LLM triage: SCRIPTS = ingest, resurfacing selection (rank by age + prior engagement), scheduling, posting via kernel gateway, metrics collection. LLM+cross-model QA, gated = copywriting, research grounding, reply drafts, image prompts. Learn-and-adapt = LLM FLAGS only → proposal card → human | Q8 | "i like the confirm" |
| C11 | QA shape: QA ENUMERATES defects and returns them to the LLM for an edit round — bounded self-correcting mini-loop (max 2 rounds), review and fix are separate actors, run as headless Ringer lanes. A non-converging loop's remedy is a recorded PROMPT UPDATE (lightweight process-change class) so failures compound into better prompts | Q8, Q11 | "qa… enumerate and send back to llm for editing… if it fails we need to update the prompt to fix in the future" |
| C12 | Cap scope: per-surface caps count ALL posts regardless of author (this dept + podcast dept + manual/team). Dept checks actual surface counts via Zernio before scheduling and YIELDS when full | Q9 | owner selected "Count all, yield" |
| C13 | Freshness gate: resurfaced items must pass still-true, still-relevant, links-alive, guest/client-approval-on-file, not-time-anchored. Ambiguous → QUARANTINE for owner review, never guess. Metrics outages → data marked missing, never fabricated, never assumed zero | Q10 | owner selected "Freshness gate" |
| C14 | Exception policy (deterministic, scripts decide — Ringer only executes inside steps): transient → retry 3× w/ backoff then quarantine; content QA → 2 edit rounds then quarantine w/ defect list; cap → yield; missing receipt → manager + heal ladder, never advance; escalations carry exactly ONE approve/skip/fix question | Q11 | "use ringer for this correct? let's think through this a bit" → confirmed after split explained |
| C15 | Lived-failure QA gates (first-class): (1) DELIVERY VERIFICATION — receipt = platform-confirmed post ID pulled from Zernio post-publish, never scheduler's claim; (2) VOICE GATE — drafts scored against Ankit's style guide + Taste profile (anti-AI-slop) in cross-model QA; (3) CONTEXT-COMPLETENESS — drafting gets full source + brand/offer packet, no drafting from fragments | Q12 | owner selected all three lived failures |
| C16 | Anti-gaming sensors (all independent of self-report): manager pulls Zernio analytics directly; calls via calendar/HubSpot join; on-platform post-existence verification; cross-model QA; weekly digest to owner with links to every published post | Q13 | owner selected "Confirm" |
| C17 | Escalation: Ankit sole target, all classes; TTL 24h; no-reply = DENY + re-file, never auto-approve; floor breaches ping immediately. Draft-only phase = weekly BATCH approval. Target state: 100% automated with escalation, earned per action class via promotion ladder; always-human floor never automates (charter, promotions, kill) | Q14 | "this should be 100% automated eventually. its just me and 24TTL" |
| C18 | Records: local always on. Durable digests are COMPANY-scoped: OB_company preferred, MyBCAT Hub alternative — wired via the factory memory seam by whatever the headless runtime can reach; until wired, digests accumulate locally and the unwired seam is surfaced weekly, never silently dropped. No secrets, no Zernio creds, no raw DM/comment bodies (shapes + IDs only), nothing PHI-adjacent | Q15 | "this is for the company so you can do ob_company or maybe hub?" |
| C19 | Existing-repo duty: mybcat-social-content-automation becomes the execution toolbox; its process docs (cutover runbook, cron setup, concept package) are updated to point at this department as the single management spot | Q9-early, mid-interview msg | "lets create the process in existing repos and update the process there" |
| C20 | Sensing scope includes the podcast factory loop's post performance — this dept measures engagement on podcast-dept posts (it still never does their initial posting) | mid-interview msg | "i'm open to also tracking the podcast factory loops performance" |

## Open items — ALL RESOLVED at readback (2026-07-28, lock round 1)

1. Suppression floor: ADDED as floor 5 in C7 (owner selected "LOCK + add suppression").
2. Q1 revisit: C1 wording CONFIRMED (discovery calls owed, path design part of the job).
3. GLM engine: exclusion CONFIRMED — allowlist is Codex OAuth + Claude subscription.
4. Acquisitions lane: CONFIRMED out of v1 charter; named future lane only.
