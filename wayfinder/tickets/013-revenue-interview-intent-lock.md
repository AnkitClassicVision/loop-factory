# 013 — Revenue department interview + intent lock

Status: CLOSED · Type: grilling (HITL) · Resolved: 2026-08-02 — INTENT LOCKED by Ankit (in-conversation, signature captured twice verbatim)

## Question

Run the factory front door for the new Revenue / Lead Follow-Up department: F0 scaffold, then the F1 owner interview per `interview/INTERVIEW.md` + `interview/QUESTION_BANK.md` — one question at a time, recommended answer first, verbatim capture, dependency-first, full EDGE GRILL (overlaps, weird inputs, exceptions, subtle failure, gaming, escalation targets, human-gate vs full-auto, kill vs pause) — ending in readback rounds and **INTENT LOCK signed by Ankit** (never self-certified).

## Boundary posture (pre-loaded from Hermes ELI5 + MyBCAT rules, to confirm in interview)

- May read: approved CRM/interaction evidence only.
- May create: internal review packets and follow-up recommendations.
- May never: send email/SMS/social, change CRM status, write patient records. `external_actions_taken = 0` until owner promotion.
- PHI adjacency → data minimization; no raw patient data, IDs, or message bodies in department code, records, cards, or memory.
- Outcome statement seed (from Hermes notes): "Every approved opportunity has an owner, current status, next action, due date, and documented outcome."

## Notes

Interview can run before ticket 008 resolves; F2 charter/graph authoring waits for 008 (graph semantics) and 009 (versioning) so the charter is born in the v2 shape.

## Resolution

Interview completed in-conversation on 2026-08-02 (tool outage forced in-chat capture; transcribed on recovery). Full record, verbatim owner answers, and the locked 14-element charter: [revenue-f1-interview-2026-08-02.md](../interviews/revenue-f1-interview-2026-08-02.md).

Headlines: outcome incl. next-step + closure; HubSpot source of truth (v1 reads HubSpot only); 4 lead types (mybcat-client / icare-grow / podcast / cvc-acquisition) with cross-feed disposition and a longer human gate for cvc-acquisition; open disposition vocabulary with mandatory unknown→human branch; Ankit sole owner; FULL-AUTO destination behind 4-rung owner-signed ladder with 6 tripwires + suppression gate; engine routing Tier1 OAuth (claude/codex/gemini/kimi/glm) → Tier2 cheap API (GLM/Grok via OpenRouter) → Tier3 Claude/Codex API, fallback never silent; 50 runs / 200 calls/day. Governance follow-up: owner amends hard rule 9 text (API lanes now permitted as loud, capped fallback). Unblocks ticket 012 together with 008. New discovery task queued: HubSpot active-sender inventory for suppression.
