# Intent Interview — podcast department

Owner: Ankit (interview surface: Claude Code session, 2026-07-22)
Interviewer: Claude Fable 5 (loop-factory F1 protocol)
Status: **INTENT LOCKED** — owner selected "LOCK IT" at readback round 3
(Ankit, 2026-07-22, Claude Code AskUserQuestion surface), after 2 correction
rounds (round 1: PHI out of domain + sales/future-department seams; round 2:
escalation dedup + escalate-once-fix-forever priority). Concept map, charter,
and plan derive ONLY from this artifact.

Evidence base inspected before questioning (per INTERVIEW.md rule 4):
- `/mnt/d_drive/repos/podcast/docs/podcast-loop-skills-source-of-truth.md` (7 loops, 34 work units)
- `/mnt/d_drive/repos/podcast/docs/podcast-funnel-AUTOMATION-DIAG-2026-07-16.md` (5 root gaps, phased plan)
- `/mnt/d_drive/repos/podcast/docs/VPS-MASTER-PARITY-2026-07-16.md` (three code truths, do-not-redeploy rule)
- Live host state via read-only scout: all seven `podcast-loop-*` timers ENABLED and producing receipts
  (promotion happened after 7/16 — the diag doc's "all dry_run" claim is stale); prep-sweep RED silently
  (IAM denies on s3:ListBucket obe-podcast-raw-tracks + secretsmanager telegram token); referral-flywheel
  RED fail-closed (missing referral-touch tracker); pipeline at 2 of 6 guests.

---

## Q1 — Core outcome owed

Question asked: "If this department ran perfectly for 90 days, what would be visibly
different? The 7/16 diagnostic already defines a Definition of Done (recording →
published episode + social in under 1 hour of your time; zero silent failures;
pipeline never below 6 guests). Which outcome does this department OWE?"

Recommendation given: all three, as stated in the existing diag DoD.

**Owner answer (VERBATIM, selected option): "All three, as stated (Recommended)"**
— i.e. ≤1 hr of owner time per episode + zero silent failures + pipeline ≥6 guests.

Interviewer reading (to be confirmed at readback, not assumed): the department owes
three co-equal measurable outcomes over 90 days: (1) founder time per published
episode ≤1 hour, (2) zero silent failures — every loop either green or loudly red
with a delivered escalation, (3) recording pipeline never below 6 guests. These are
the outcome setpoints; sensors must be independent of the loops' own self-reports.

---

## Q2 — Process inventory + architecture

Question asked: inventory presented from evidence (7 governed loops live, 3 support
lanes, VPS live layer). Decision: what is the factory department relative to the
existing estate — wrap/govern, migrate, or hybrid?

Recommendation given: wrap the estate as its governance layer; harden in place;
nothing migrates.

**Owner answer (VERBATIM, selected option): "Govern + harden, no migration (Recommended)"**

Interviewer reading (to be confirmed at readback): the department is a MANAGER over
the existing podcast loop estate. Its runtime nodes are sensors/checkers reading
receipts, systemd timer state, logs, and VPS state read-only; it compares against
charter setpoints, escalates loudly, and drives the heal ladder. The 7 loops, the
approval bridge, and the VPS layer remain canonical where they are. Hardening fixes
(IAM, media gates, code-truth reconciliation, overlap cleanup) land in the podcast
repo/VPS via the Ringer plan, not inside factory code. Department-managed process
list: (1) the 7 governed loops' health + receipts, (2) support-lane health
(prep-sweep, sweeper, bridge/executor), (3) VPS live-layer outcomes (publish cron,
inbox review, recording worker), (4) escalation-channel liveness itself.

---

## Q3 — V1 proving slice

Question asked: which ONE capability proves the department works? Recommended the
loop watchdog / silent-failure sensor (live test case: today's prep-sweep failure).
Alternatives offered: pipeline sensor, publish-day sentinel.

**Owner answer (VERBATIM): "we need all of htes so loop watch dog shou dlhave piple in and publisday "**

Interviewer reading (to be confirmed at readback): the proving slice is the loop
watchdog, and its sensing scope INCLUDES the pipeline sensor and the publish-day
sentinel — all three sensors ship in V1 as parts of one watchdog capability:
(1) timer/receipt/log/escalation-channel liveness across the estate, (2) independent
guest-count-vs-6 pipeline sensor, (3) publish-day outcome verification of the VPS
9:55 cron. Not three separate departments' worth — one watchdog with three sensor
families.

---

## Q4 — Setpoints

Question asked: provisional setpoints proposed. OUTCOME: (a) silent-failure detection
latency ≤30 min (15-min loops) / ≤26 h (daily loops), measured from timer/log truth;
(b) pipeline ≥6 guests via independent calendar+HubSpot join; (c) publish-day
verified live by 10:30 ET; (d) founder time/episode TBD_MEASURE_IN_SHADOW.
OPERATIONAL ceiling: watchdog senses every 30 min; ≤5 individual escalation pings/day
then digest-batch. Ratify after 1 week of shadow data.

**Owner answer (VERBATIM, selected option): "Accept provisional (Recommended)"**

Interviewer reading: the numbers above enter the charter marked provisional,
ratified after one shadow week. Founder-time target derived from shadow data, never
invented.

---

## Q5 — NEVER list + automation posture

Question asked (two rounds): proposed six hard floors; owner said "need to chat about
this i dont udnersatdn this this needs to befully automated" — explained the floors
constrain only the watchdog, not the pipeline; then asked what "fully automated"
should cover (start posture vs ceiling, promotion ladder explained).

**Owner answer round 1 (VERBATIM): "need to chat about this i dont udnersatdn this this needs to befully automated"**

**Owner answer round 2 (VERBATIM): "full auto with sends now and auto-heals adn qa gates beof autosends and need to gather poitns of infomation so thing like headshot, links etc there shoudl be manifste for every podcast we need for ecah guest adn that is the goal so not only scheudlign but the info as well adn promotaional infor manfistes for each one"**

Interviewer reading (to be confirmed at readback):
1. AUTOSEND NOW: the owner explicitly promotes outreach/guest email sends in the
   podcast estate to autosend, conditional on QA gates passing before every send
   ("qa gates beof autosends"). The existing dispatch-gateway chain (fact_pack →
   deterministic gate → QA → ledger → idempotency → receipt → reconcile) becomes the
   autosend gate; the Linear human-APPROVE step is removed for QA-passed sends. This
   is an owner promotion decision recorded here with provenance (Ankit, 2026-07-22,
   Claude Code AskUserQuestion). Kill switch and send caps stay. The podcast repo's
   CLAUDE.md "agent drafts, Ankit sends" rule must be updated to reflect this.
2. AUTO-HEAL DEEPER: the watchdog may fix known failure patterns itself (recreate
   missing tracker files, re-run failed steps, restart units), every heal logged +
   reported. Unknown patterns still escalate.
3. NEW GOAL — GUEST INFO MANIFESTS: for every guest/episode the system must
   automatically gather a complete information manifest (headshot, links, bio, etc.)
   AND a promotional info manifest. "not only scheduling but the info as well."
   Missing items are chased automatically; manifest completeness becomes a tracked,
   gated artifact per episode.
4. The six proposed floors otherwise stand EXCEPT floor 1 is amended: the ESTATE may
   autosend through its QA-gated gateway; the watchdog department itself still never
   sends outreach (its outbound remains escalations/heal reports). Floors 2 (no
   live hand-patching; heals limited to known patterns + logged), 3 (no VPS redeploy
   from master until v2 lands), 4 (never suppress RED), 5 (no PHI), 6 (governance
   files human-only) unchanged.

---

## Q6 — Budget

Question asked: weekly ceilings proposed ($25/wk API, existing send caps, 4h/day
worker time, 80% auto-stop). Options: accept / raise / subscription-only.

**Owner answer (VERBATIM, selected option): "Subscription-only, $0 API"**

Interviewer reading: ALL model work runs on subscription/OAuth lanes (Codex OAuth,
Claude subscription via Ringer). Hard $0 API-spend rule: anything that cannot run on
a subscription lane ESCALATES instead of spending. Send budget: existing caps stay
(12 outbound/day, 5 new contacts/day, per-contact cadence floor). Worker wall-clock
ceiling 4 h/day, 80% auto-stop with escalation, ratify after a benchmark week.

---

## Q7 — Kill conditions

Question asked: four kill conditions proposed (reputation kill on autosend, gaming
kill on watchdog, 2-week plate-load kill, floor-breach kill).

**Owner answer (VERBATIM): "so we should never get to kill if we are proly qa and proer manifest drivers and proper context gathering also i want to make sure all data is stored inside of hubspot as cerntral soruce of truth adn that is were we can track how much of the manifste adn reuqiremtns we have and statuses etc.  and notes"**

Interviewer reading (to be confirmed at readback):
1. The four kill conditions are accepted as the BACKSTOP; the owner's emphasis is
   that proper QA, manifest-driven context gathering, and proper context should make
   them unreachable in practice. Prevention-first, kill as last resort.
2. NEW REQUIREMENT — HUBSPOT CENTRAL SOURCE OF TRUTH: all guest/episode manifest
   data, requirement completeness, statuses, and notes are tracked IN HUBSPOT.
   Manifest progress ("how much of the manifest and requirements we have") must be
   visible in HubSpot per guest. Implication: the podcast estate needs scoped
   HubSpot WRITE access for podcast_* manifest/status properties and notes —
   an owner-authorized promotion of the CRM-write class, limited to podcast-owned
   fields (sales-owned fields stay out of scope per the S-loop boundary).
   Open nuance for readback: HubSpot = business/guest/manifest truth; the VPS
   remains the media/pipeline runtime truth (2026-06-10 decision) — two truths with
   a defined boundary, not a conflict.

---

## EDGE — Incomplete manifest at publish deadline

Question asked: standing rule when a manifest is still missing items at publish day
after automated chasing. Recommended publish + suppress dependent promo.

**Owner answer (VERBATIM, selected option): "Publish with fallback assets"**

Interviewer reading: episodes ALWAYS publish on schedule when media/content QA
passes. Missing manifest assets get deterministic fallbacks (episode artwork for
missing headshot, show URL for missing practice link, template bio from transcript);
ALL promo ships on schedule with fallbacks. Chasing continues; when the real asset
lands, later promo uses it (no retro-repost of already-published pieces). Fallback
usage is recorded in HubSpot manifest status.

---

## Remaining bank items — evidence-derived readings (per INTERVIEW.md rule 4,
presented at readback for confirmation instead of asked open-ended)

- Q8 script-vs-LLM triage: sensing, gates, heals, manifest completeness checks,
  send caps, idempotency, reconcile = SCRIPTS (finite, enumerable). LLM only for:
  drafting content (emails, show notes, promo copy), QA judgment on generated
  content (always cross-model), failure clustering/improvement proposals. Matches
  the estate's existing split; no new LLM assignments invented.
- Q9 overlaps: prep stage is triple-touched (daily_booking_scan, prep-sweep,
  booking-readiness loop) — consolidation is a hardening-plan item; sales boundary
  stays (sales state OUT of podcast fields); frequency/suppression caps apply per
  contact ACROSS lanes via the shared ledger.
- Q10 weird inputs: existing rules adopted as charter rules — unresolved identity =
  suppress + review, never guess; dup contact (same name, different email) = create
  + cross-reference note, never conflate; fat-fingered emails verified from thread.
- Q11 exceptions per step: known failure pattern → auto-heal + log; unknown →
  escalate with ONE question; everything fails CLOSED (deny-by-default stays).
- Q12 subtle failure (lived): silent media corruption that survives sampling QA
  (E102 PTS collapse) and silent infra failure that kills its own alert channel
  (today's prep-sweep IAM denial). Both become first-class: executed checks as
  gates + escalation-channel liveness sensing.
- Q13 gaming: watchdog verified by independent sensors (timer/log truth vs
  receipts; HubSpot join vs loop self-reports) + negative tests (poisoned fixtures
  must FAIL gates). A gate that cannot fail is decoration.
- Q14 escalation: Telegram ping (primary) + Linear card (durable record); no-reply
  TTL 24h → re-file louder at top of next digest; NEVER auto-approve on silence.
- Q15 records: local receipts/runs/STATE always on under departments/podcast/state/;
  HubSpot = guest/manifest/business truth; VPS = media/pipeline runtime truth;
  never recorded: credentials, PHI, raw message bodies.

---

## Q16 — Readback round 1 corrections

Full readback presented (outcome, architecture, autosend-now posture, floors, kills,
publish policy, escalation, triage). Owner requested corrections before locking:

**Owner answer (VERBATIM): "coupel of corects phi not relevant this deparmnt shoudl als interact with sles and future deparntmetns as well"**

Interviewer reading of the two corrections:
1. PHI OUT OF DOMAIN: podcast guest data is business contact data, not patient
   data — PHI is not an operative concern for this department. The floor is
   restated as a scope statement ("patient/PHI data is entirely out of this
   department's domain; if any ever appears in its inputs, that is itself an
   escalation") rather than an operating constraint.
2. CROSS-DEPARTMENT SEAMS: the department must interact with a (future) SALES
   department and other future loop-factory departments. The existing sales-handoff
   loop's packets become a defined ESTATE-LEVEL SEAM: podcast emits handoff
   packets/cards consumable by sales and future departments; each department owns
   its own state (sales state stays out of podcast fields — interaction, not
   merger). Architecture must keep these seams pluggable; a new department
   consuming the seam is an estate/charter event, never self-wired.

---

## Q16 — Readback round 2 correction

**Owner answer (VERBATIM): "i need to make sure any escaltions dont escalte agin so that is priorty"**

Interviewer reading (PRIORITY requirement, two parts):
1. NO DUPLICATE ESCALATIONS: escalations are deduplicated by incident fingerprint —
   one incident = one escalation thread. The 15-min prep-sweep failure must never
   ping 96 times; it is one incident until resolved (the louder-refile-after-24h
   policy operates on the same thread, not new pings).
2. NO REPEAT ESCALATIONS (root-cause priority): every escalation spawns a
   root-cause improvement item (proof-improvement loop input). The SAME fingerprint
   escalating again after being resolved is a first-class defect of the department.
   Recurrence rate becomes an outcome setpoint with target ZERO repeat escalations —
   "escalate once, fix forever."

---

## Post-lock owner addendum — writing rules (2026-07-22, mid-implementation)

**Owner instruction (VERBATIM): "wit hte wrirign rules we've defined it in ob_mybcat so lets use that as well for how the words fit etc."**

Interviewer reading (owner charter addendum, human-made, provenance as above):
all estate-drafted content (guest outreach emails, manifest chase emails, show
notes, promo/social copy) is governed by the writing rules stored in OB_mybcat:
1. MyBCAT Writing Style Graph v1 (canonical registry, captured 2026-06-02):
   style modes (Ankit Direct internal / MyBCAT Sales & Marketing external),
   artifact shapes, banned patterns, approved claims, rewrite rules, validators,
   capture loop.
2. WRITING_GUIDE_CANONICAL (5-section XML, revision 2, sha256 946a19b4...):
   banned-phrase categories with regexes, no-em-dash + punctuation rules, hook
   framework, bridge framework, lift-and-swap test, signature block
   (Ankit / 615.779.3629 / BCC 23344341@bcc.hubspot.com), length-by-warmth.
3. tropes.fyi anti-AI-writing standing decision (captured 2026-07-15): applies
   to all prose from every AI surface.
Because sends are autosend NOW, this becomes a GATE, not guidance: the
deterministic pre-send gate gains a style validator (em-dash count 0, banned
phrase scan, signature present, one CTA, length by warmth) and the cross-model
QA judge is instructed from the Style Graph nodes (hook framework, lift-and-swap,
coffee-shop test). A style-violating poisoned draft joins the negative-test
suite and must BLOCK. At draft time, agents query OB_mybcat for the newest
versions of these artifacts (newer captures supersede).

---

## Post-lock owner addendum — receipt-gated steps + headless/subscription-only (2026-07-23)

**Owner instruction (VERBATIM): "can we updat ehte loop-factory process.  every step needs verificaion with an artifact, output, or receipt of somekind no matter what to verify its done before moving forward.  if not then the manger n eeds to come and fix it.  this processes are run headless mode either claud eo rcodex or ringer etc if ringer then only use ouath plans"**

Interviewer reading (owner charter addendum, human-made, provenance: Ankit,
2026-07-23, Claude Code):
1. RECEIPT-GATED STEPS: every step of every factory/department process proves
   completion with an executed artifact, output, or receipt before the next
   step may run — universally, "no matter what". A step without its receipt is
   not done regardless of any claim.
2. MANAGER REMEDIATION: a missing or failed step receipt summons the MANAGER —
   the department manager / estate watchdog detects the gap and fixes it via
   the heal ladder or escalates to the human-in-the-loop outbox; the process
   never advances past the gap.
3. HEADLESS EXECUTION: processes run headless under Claude, Codex, or Ringer
   workers — never dependent on an interactive session.
4. RINGER = OAUTH PLANS ONLY: Ringer lanes may use OAuth/subscription-plan
   engines exclusively (Codex OAuth, GLM coding plan, Claude subscription);
   metered API lanes are forbidden factory-wide — escalate instead of spend.
Encoded in: runbooks/factory-pipeline.md hard rules, CLAUDE.md/AGENTS.md hard
rules 8-9 (twins synchronized), charter execution block, and the scaffold
charter template so every future department inherits it.
