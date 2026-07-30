# social Department — Procedural Graph (F2 output, the WHAT)

Status: DRAFT (F2). Hand-authored from the locked concept map. The authoritative
machine form is `subgraphs.json` (validated by `factory/graphs.py`); these
tables are the human-readable companion. If the two disagree, the JSON is
wrong OR this file is wrong — fix both, re-lint, re-pin. A process change that
does not update BOTH plus the release is a drift alarm.

Traces to: `knowledge/concept-map.md` (LOCKED 2026-07-28) + `charter.yaml` v1.0.
Every node carries the uniform wrapper: declared inputs → output contract →
executed QA check → receipt to runs. Scripts get NO QA exemption.

V1 stands three subgraphs (charter `funnels.entries`): SG-REPUBLISH (proving
slice, C4), SG-SENSE (C16/C20), SG-LEARN (C10/C11). The six phased lanes
(podcast push, blog, insights, lead-magnet CTA, icaregrow webinars, engagement
replies) reuse SG-REPUBLISH's node shape with different source adapters; each
activates ONLY via charter change + its own subgraph entry + re-lint + re-pin.

## Shared Safety Layer (factory standard; immutable per charter)

| ID | Node | impl | What it enforces for THIS department |
|---|---|---|---|
| S1 | resolve_identity | SCRIPT | canonical content-item + target-surface identity across systems (tracker DBs, Zernio); unresolved = suppress+review (C13) |
| S2 | eligibility_gate | SCRIPT | freshness gate (C13): still-true, links-alive, approval-on-file, not-time-anchored; suppression floor (C7.5): deleted/retracted content NEVER re-eligible |
| S3 | privacy_preflight | SCRIPT | deterministic PHI/client-identifiable scan + redact BEFORE any model call (C7.1) |
| S4 | send_authorization | SCRIPT (gate) | pre-dispatch atomic proof → one-time token; no token = no post |
| S5 | frequency_reserve | SCRIPT | ONE shared ledger; cap slot per surface counting ALL authors via Zernio count (C12); full = YIELD |
| S6 | kill_controller | SCRIPT (independent) | charter kill_if + plate-load; outside self-scoring (C9) |
| S7 | circuit_breaker | SCRIPT | auto-freeze posting class on platform strike / complaint / delivery-failure streak (C9, C15) |
| S8 | budget_reserve | SCRIPT | reserve worst-case BEFORE the first model call; OAuth lanes only; fail closed (C8) |

Delivery rule: while the posting class is shadow/draft_only, dispatch runs
`delivery_mode=simulate` to a synthetic sink and asserts `delivered_count==0`.

## SG-REPUBLISH — back-catalog republish + re-engagement (V1 proving slice)

```
[T] daily trigger (headless)
  → N1 inventory_backcatalog → S1 → N2 select_candidate → S2(freshness)
  → N3 assemble_context (FULL source + brand/offer packet, versioned manifest)
  → S3 → S8 → N4 draft_post (LLM, Ringer lane)
  → N5 qa_post (cross-model; ENUMERATES defects → ≤2 edit rounds → quarantine)
  → S5(cap check, all authors) → S4 → N6 dispatch (kernel→Zernio; simulate in shadow)
  → N7 delivery_verify (platform-confirmed post ID — never scheduler's claim)
  → N9 record
  [S6 + S7 run continuously across the lane]
```

| # | Node | type | impl | action_class / autonomy | QA check (executed) | model | Traces |
|---|---|---|---|---|---|---|---|
| N1 | inventory_backcatalog | Read | SCRIPT | internal_read / shadow | index row count > 0 AND schema-valid; stale source = HALT | none | C1, C4 (Q3) |
| N2 | select_candidate | Transform | SCRIPT | internal_read / shadow | deterministic rank (age + prior engagement); replays with same inputs give same pick | none | C10 (Q8) |
| N3 | assemble_context | Transform | SCRIPT+read | internal_read / shadow | versioned context manifest COMPLETE (full source item + brand/offer packet); fragment = lint fail, HALT+escalate | none | C15.3 (Q12) |
| N4 | draft_post | Generate | LLM (Ringer lane, allowlisted engines) | content_draft / shadow | consumes ONLY the S3-sanitized bundle; output matches per-surface format contract | per node contract | C10, C11 (Q8) |
| N5 | qa_post | Gate | HYBRID (cross-model) | internal_check / shadow | enumerated-defect report; voice gate vs style guide + Taste profile; grounded-sources check (C7.4); claim→source coverage; ≤2 edit rounds then quarantine | ≠ N4 family | C11, C15.2 (Q8, Q12) |
| N6 | dispatch | Act | SCRIPT→kernel gateway | external_publish / shadow=simulate | delivered_count==0 in shadow; consumes S4 token live | none | C5 (Q9-early) |
| N7 | delivery_verify | Score | SCRIPT | internal_read / shadow | post ID + status pulled back from Zernio; missing/failed = receipt FAIL → manager | none | C15.1 (Q12) |
| N9 | record | Record | SCRIPT | internal_write / shadow | runs row appended; receipt chain complete | none | factory law |

## SG-SENSE — independent engagement + outcome sensing (read-only)

```
[T] daily trigger → S1(post identity join) → N1 pull_zernio_analytics (ALL posts,
  incl. podcast dept) → N2 pull_call_joins (calendar/HubSpot) → N3 compare_charter
  (setpoints, caps, faux-work, gaming signals) → N4 assemble_weekly_digest
  (links to every published post; unwired-seam notice) → N9 record
```

| # | Node | type | impl | action_class / autonomy | QA check (executed) | model | Traces |
|---|---|---|---|---|---|---|---|
| N1 | pull_zernio_analytics | Read | SCRIPT | internal_read / shadow | feed outage = mark_missing, NEVER fabricate/zero | none | C16, C20 (Q13) |
| N2 | pull_call_joins | Read | SCRIPT | internal_read / shadow | join independent of dept classifiers | none | C6 (Q4) |
| N3 | compare_charter | Score | SCRIPT | internal_read / shadow | thresholds from charter, not code constants | none | C6, C9 |
| N4 | assemble_weekly_digest | Transform | SCRIPT | internal_write / shadow | digest lists EVERY platform-verified post; sanitized (no bodies of DMs/comments) | none | C16, C18 (Q13, Q15) |
| N9 | record | Record | SCRIPT | internal_write / shadow | runs row appended | none | factory law |

## SG-LEARN — learn-and-adapt proposal lane (flags only, never self-modifies)

```
[T] weekly trigger → N1 read_metrics_records → S3 → S8
  → N2 propose_insights (LLM: what worked, what to change, prompt updates)
  → N3 proposal_card_to_outbox (human decision; process-change-qa on approval)
  → N9 record
```

| # | Node | type | impl | action_class / autonomy | QA check (executed) | model | Traces |
|---|---|---|---|---|---|---|---|
| N1 | read_metrics_records | Read | SCRIPT | internal_read / shadow | reads SG-SENSE records only (no self-graded data) | none | C10 |
| N2 | propose_insights | Generate | LLM (Ringer lane) | internal_draft / shadow | proposals cite metric evidence rows; ungrounded proposal = QA fail | ≠ drafting family | C10, C11 (Q8) |
| N3 | proposal_card_to_outbox | Act | SCRIPT | internal_write / shadow | card carries ONE approve/skip/fix question; prompt changes route to process-change-qa | none | C11, C14, C17 (Q11, Q14) |
| N9 | record | Record | SCRIPT | internal_write / shadow | runs row appended | none | factory law |

## Autonomy rollout

All publish classes start at shadow (simulate). Draft-only runs as WEEKLY BATCH
approval (C17). Promotion is per action class per runbooks/promotion-ladder.md;
no lane auto-promotes; demotion automatic on breach. The legacy VPS cron
cutover (C5) is its own human gate (`vps_cron_cutover`), executed only after
this department's posting class reaches gated_live and the owner gives the go.

## Intent traceability

Every node above lists the concept-map node (C#) and interview question (Q#) it
closes. Does-less / does-more check: no capability beyond the locked interview —
no initial podcast episode posting (C4 boundary), no replies lane in v1, no
acquisitions lane, no remote memory writes until the seam is wired (C18).
