# Podcast Department — Build + Estate Hardening Plan (2026-07-22)

Derives from: `departments/podcast/interview/intent-interview.md` (INTENT LOCKED
by Ankit 2026-07-22), `departments/podcast/knowledge/concept-map.md` (LOCKED),
podcast repo evidence (`docs/podcast-funnel-AUTOMATION-DIAG-2026-07-16.md`,
`docs/VPS-MASTER-PARITY-2026-07-16.md`, live scout sweep 2026-07-22).

Two workstreams. A = build the watchdog department (loop-factory repo).
B = harden the podcast estate (podcast repo + VPS). Ringer implements; the
coordinator reviews and lands. All engines run subscription/OAuth lanes only
(charter: $0 API).

## Current verified state

- F0 scaffold done; F1 interview LOCKED; F2 charter + concept map + procedural
  graph + subgraphs.json authored. `loopfactory.py validate --name podcast`:
  charter OK, guard-matrix lint [], traceability fails ONLY on the 11
  not-yet-written runtime impls (expected pre-F3).
- Estate live state: 7 loops enabled + receipting; prep-sweep RED silently
  (IAM); referral-flywheel RED fail-closed (missing tracker); pipeline 2/6.

## Workstream A — F3/F4: build the watchdog (loop-factory repo)

| # | Task | Executed check (Ringer check command) |
|---|---|---|
| A1 | Author 11 runtime nodes per procedural-graph.md: sense_estate, compare_charter, fingerprint_dedup, escalate_outbox, record, pipeline_sensor, publish_verifier, manifest_sensor, heal_select, heal_apply, heal_verify (+ kernel_bridge). Each: declared inputs → output contract → executed QA check → receipt to runs. Script-only, no model calls (C14). | per-node pytest + `python3 loopfactory.py validate --name podcast` exits 0 |
| A2 | Negative tests: poisoned fixtures per C16 — a unit missing from sense output FAILS, a forged/stale receipt BLOCKS, a resolved fingerprint recurring flags department_defect, heal on non-allowlisted playbook REFUSES | pytest negative suite exits 0 (i.e. every poison correctly rejected) |
| A3 | Shadow run against the estate's REAL current incidents (prep-sweep IAM, referral tracker): watchdog must detect both, dedup to exactly 2 threads, propose (not apply) heals, delivered_count==0 | shadow receipts exist + assertions in `loopfactory.py qa` |
| A4 | Release pin + registry; systemd timer installed DISABLED (F5 enable is a separate owner step) | `release pin --flip` OK; drift check clean |

## Workstream B — estate hardening (podcast repo + VPS)

| # | Task | Gate | Executed check |
|---|---|---|---|
| B0 | **URGENT — IAM fix**: `oe-sales-reader` lacks `s3:ListBucket` on `obe-podcast-raw-tracks` + `secretsmanager:GetSecretValue` on the Telegram bot token secret. Prepare minimal least-privilege policy diff for Ankit review; Ankit applies/approves. | **HUMAN (Ankit)** — AWS access change | prep-sweep next run exits 0; Telegram ping test delivers |
| B1 | Referral-flywheel unblock: recreate the `referral-touch-<date>.json` tracker per its contract; add "missing tracker" to the heal playbook allowlist | none (local state file) | loop's next run exits 0 with a selected candidate |
| B2 | **Autosend flip** (owner-authorized in interview): remove Linear human-APPROVE for QA-PASSED outreach sends; full gateway chain + send caps + kill switch stay. ORDER: (1) negative tests first — poisoned drafts (hallucinated fact, unverified link, wrong recipient, placeholder token, STYLE-VIOLATING draft: em dash + banned phrase + two CTAs) must BLOCK; (2) then flip; (3) first 5 autosends get post-send verification receipts reviewed. NEW (owner addendum 2026-07-22): the deterministic gate gains a style validator built from the OB_mybcat writing rules (Writing Style Graph v1 + WRITING_GUIDE_CANONICAL rev 2 + tropes.fyi decision): em-dash count 0, banned-phrase scan (incl. regex categories), signature block (Ankit / 615.779.3629 / HubSpot BCC), single CTA, length-by-warmth; the cross-model QA judge prompt is generated from the Style Graph nodes (hook framework, lift-and-swap, coffee-shop test). | negative tests green BEFORE flip; flip itself owner-confirmed at plan approval | poisoned-draft suite blocks 5/5 (incl. style poison); first real send produces gateway receipt chain |
| B3 | **Manifest system**: derive the full required-field list (headshot, links, bio, promo assets, …) from the existing asset-request lane; create scoped HubSpot `podcast_*` manifest/status properties; wire completeness tracking + fallback-asset policy into publish path (C6/C7/C10). Field list presented to Ankit before HubSpot property creation. | HubSpot property creation = CRM change, Ankit reviews field list | manifest sensor reads completeness for every active guest; fallback path renders in test |
| B4 | **Media gates**: E102 probes (PTS-gap, cross-track adjacency, transfer/AV-sync, keyframe/GOP, bumper parity) become executed P1/P2 gates with poisoned-fixture negative tests | none (gates only block) | each poisoned fixture FAILS its gate |
| B5 | **Escalation dedup** estate-wide: incident fingerprint + one-thread policy in the estate's Telegram/Linear escalation paths (C12) | none | replayed incident produces 0 new threads |
| B6 | **Code-truth reconciliation** (diag Phase 0.2): land v2 on master (cherry-pick the 7 media fixes first if full merge stalls), re-run md5 parity, THEN redeploy VPS from master. HARD RULE: no VPS redeploy before v2 lands. | **HUMAN (Ankit)** — deploy | parity report VPS==master for server/ |
| B7 | Cleanup: remove dead `open-engine-cron-podcast-*` units; consolidate prep-stage triple-touch (booking scan vs prep-sweep vs B-loop) into one owner per sub-step; retire ProspectingFeeder endpoint into A4 | none | `systemctl --user list-timers` shows no dead units; one owner per prep sub-step documented |

## Sequencing

1. B0 (IAM, needs Ankit) + B1 immediately — both RED loops green again.
2. A1→A4 (Ringer build swarm) in parallel with B4 + B5 (Ringer fix lanes).
3. B2 autosend: negative tests → owner-confirmed flip → monitored first sends.
4. B3 manifest system (field list → Ankit review → implement).
5. B6 code-truth (big, owner-gated deploy). B7 cleanup last.

## Ringer discipline (applies to every run)

HUD on screen before specs; engine pitch from `./ringer.py models` with real
numbers (subscription lanes only per charter); `./ringer.py lint` every
manifest; one `run_name` per job; every check executes and prints WHY it
failed; review-swarm before fix-swarm; read-only lanes on worktrees; no worker
gets send/publish tools; coordinator spot-checks ≥1 passing artifact per run.

## Out of scope (explicitly)

- Enabling the department's systemd timer (F5) — owner step after shadow burn-in.
- Any new send class beyond the owner-authorized outreach autosend.
- VPS redeploy before v2 lands on master.
