# social — Operating Runbook (v1)

Goal: run the governed Zernio social engine daily without owner attention —
back-catalog republishing in shadow now, all lanes at earned autonomy later —
so discovery calls get booked while the owner's plate gets lighter.
Owner: Ankit. Department: social.
Traces to: concept-map C1–C20 (LOCKED 2026-07-28), subgraphs SG-REPUBLISH /
SG-SENSE / SG-LEARN, charter v1.0.

## Preconditions

- Charter v1.0 present, loader-valid, `autonomy_state` correct for the phase.
- `python3 loopfactory.py validate --name social` → OVERALL true (missing =
  park, never improvise).
- A pinned release for the running code (F4 onward); drift check clean.
- For live posting classes ONLY (post-promotion): `zernio` CLI authenticated on
  the runner via its own config — credentials never live in this repo.

## The daily cycle (headless; systemd timer, installed DISABLED until F5)

1. `departments/social/runtime/social_daily.sh` drives SG-REPUBLISH in charter
   order: markers → inventory → S1 resolve → select → S2 eligibility →
   assemble_context → S3 privacy → draft/QA loop (≤2 rounds, cross-model) →
   kernel frequency+budget → dispatch (simulate in shadow) → delivery_verify →
   record. Proof: every step writes its receipt before the next runs; the runs
   row chain in `state/runs.jsonl` is the executed evidence.
2. SG-SENSE pulls Zernio analytics for ALL posts (this dept + podcast dept +
   manual), joins discovery calls from the calendar/HubSpot export, compares
   against charter setpoints. Proof: observation rows appended; missing feeds
   recorded as `status:missing`, never zeros.
3. Weekly: SG-LEARN reads observation rows only, proposes insight/prompt-update
   cards to `state/approval_queue.jsonl` (ONE approve/skip/fix question each,
   TTL 24h, deny-and-refile on expiry). Proposals without row-level evidence are
   dropped as `ungrounded_proposal`.
4. Weekly digest to owner: every platform-verified post with links, engagement
   summary, quarantine count, unwired-seam notice.

## QA gate

- Executed checks: full `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest departments/social/tests/ -q -p no:cacheprovider`
  plus `python3 loopfactory.py validate --name social` and (post-pin)
  `python3 loopfactory.py qa --name social`.
- Cross-model QA: YES for all generated content — qa_post refuses to review its
  own drafting engine; enumerate-then-edit, max 2 rounds, quarantine with
  defect list on non-convergence.
- Prompt updates arising from failed QA loops are recorded process changes
  (runbooks/process-change-qa.md) — never silent edits.

## Exceptions & escalation (charter `exceptions`, deterministic)

- Transient failure → retry 3× with backoff, then quarantine.
- Content QA non-convergence → quarantine with enumerated defects.
- Surface at cap (counting ALL authors) → yield silently; not an error.
- Missing receipt → manager + heal ladder; the chain NEVER advances past it.
- Metrics outage → mark missing; never fabricate, never assume zero.
- Escalation target: Ankit, sole, all classes; TTL 24h; no-reply = deny +
  re-file with fresh context; floor breach pings immediately.

## Promotion path (per runbooks/promotion-ladder.md; never self-served)

shadow (now) → draft_only (weekly BATCH approval, one sitting) → gated_live →
autonomous. 14 clean shadow days minimum before the first promotion counts.
Demotion is automatic on any breach. The always-human floor never promotes:
charter changes, promotions, kill decisions, this runbook.

## Legacy cutover (chartered human gate `vps_cron_cutover`)

The old standalone daily cron in mybcat-social-content-automation on the
Lightsail VPS is retired ONLY at a deliberate cutover: (1) verify ON the VPS
which branch/worktree the live crontab executes; (2) owner gives the explicit
go; (3) disable the three cron entries; (4) confirm next-day silence from the
old lane and receipts from this department. Until then the old cron keeps
running and this department stays in shadow. Repo branch state warning
(scout, 2026-07-28): ~7 weeks of work unmerged there; canonical-branch
decision belongs to the owner before its docs are updated.

## Kill switch

`systemctl --user disable --now social-loop.timer` (registry entry), or write
`departments/social/state/KILLED` — guards block the whole chain on that
marker. Kill conditions in charter `kill_if` are kill, not pause: restart
requires a new human charter decision.

## Manifest

- Inputs: podcast/back-catalog feeds (RSS or item lists), blog RSS (phased
  lane), brand/offer packet (`knowledge/brand-context.yaml`, owner-approved),
  suppression list, approvals-on-file list, engines allowlist
  (codex_oauth, claude_subscription — OAuth only, $0 API).
- Outputs/receipts: `state/runs.jsonl`, `STATE.json`, `heartbeats.jsonl`,
  dispatch receipts + delivery verifications, quarantine files, approval queue
  cards, weekly digest.
- This runbook is a release artifact: changing it means re-lint + re-pin
  (runbooks/process-change-qa.md). Governance file — humans only, forever.
