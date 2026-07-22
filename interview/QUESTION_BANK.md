# F1 Question Bank

One question per turn. Recommended answer first. Verbatim capture. See
`INTERVIEW.md` for the protocol. Sections marked (EDGE) are the grill — they
are not optional; skipping one requires a recorded rationale.

## Q1 — Core outcome owed

"What does this department OWE the business? Finish the sentence: if this ran
perfectly for 90 days, the thing that would be visibly different is ___."
Probe for the measurable artifact (meetings set, tickets closed, posts
published, invoices reconciled), not activity.

## Q2 — Process inventory

"List every distinct process/funnel/lane this department must manage. Which
exist today, which are aspirational?" Probe: mini-processes hiding inside big
ones; adjacent lanes the owner runs manually today; future lanes to keep the
architecture pluggable for (a new lane later = charter change, never
self-created by the department).

## Q3 — V1 proving slice

"Which ONE process proves the department works? Everything else phases in
behind it." Recommend the highest-pain, lowest-blast-radius lane.

## Q4 — Setpoints

Two numbers per the factory contract:
- Operational setpoint (ceiling, not quota): the pace/volume guardrail.
- Outcome setpoint: measured by a sensor INDEPENDENT of the department's own
  classifiers (calendar joins, payment records, third-party feeds). If the
  target is unknown, record TBD_MEASURE_IN_SHADOW and derive from data —
  never invent a number.
Probe: "what data exists today that should set this number?"

## Q5 — NEVER list (hard floors)

"What must this department NEVER do, at any autonomy level, forever?"
Offer candidates: no external send without approval until promoted; never
exceed frequency caps; suppression/opt-out honored instantly and permanently;
never negotiate price/commitments outside a recorded whitelist; never touch
PHI/patient-adjacent data. Capture modifications verbatim (e.g. "allowed under
recorded items" becomes a whitelist in the charter, changes always-human).

## Q6 — Budget

Weekly ceilings: model calls, dollars, worker minutes. Engine policy
(subscription/OAuth lanes vs per-token API). Fail-closed rule: auto-stop at
80% of any ceiling pending owner review. Recommend conservative provisional
numbers ratified after a benchmark batch.

## Q7 — Kill conditions (kill, not pause)

Offer: reputation damage; cost exceeds value 4+ consecutive weeks after
burn-in; gaming its own numbers; any floor breach on external actions. Then
ask for the owner's own additions. Probe the faux-work condition explicitly:
"activity happens but nothing converts — kill or coach?" and the plate-load
rule: the department must remove items from the owner's plate, never add more
than it removes (net-added for 2 weeks = kill signal).

## Q8 — Problem-type triage (script vs LLM, per piece)

For each piece of each process, classify:
- Known problem / known solution → SCRIPT, no LLM ever.
- Known problem / judgment needed → LLM + cross-model QA + gate, draft-only
  until promoted.
- Known problem / unknown solution → data exploration first, then govern.
- Unknown problem → department FLAGS only → proposal card → human.
If a choice is finite and enumerable, it is a state machine, not an LLM —
make the owner confirm each LLM assignment is real judgment.

## Q9 (EDGE) — Boundary overlaps

"Who or what else touches the same people/records/systems? Can an entity be in
two processes at once? Which lane owns it then?" Capture explicit boundary
rules (e.g. "sales scope starts AFTER the third podcast call") and cross-lane
caps (frequency/suppression apply PER ENTITY ACROSS ALL lanes).

## Q10 (EDGE) — Weird inputs

Walk each intake: "what's the weirdest thing that ever arrived here?"
Ambiguous identity, missing stage/fields, duplicates across systems,
out-of-the-blue arrivals that skip the normal funnel, stale records,
timezone/locale surprises. Rule: unresolved identity = suppress + review,
never guess.

## Q11 (EDGE) — Exceptions per step

For each step: on failure — retry (how many times), skip, quarantine, or wake
a human? What is the ONE question the escalation asks? Anything the owner
can't answer becomes an open question in the artifact, and the step fails
closed until answered.

## Q12 (EDGE) — The subtle failure

"What has gone wrong before that was subtle rather than obvious?" Fold the
answer in as a first-class QA gate, not a footnote.

## Q13 (EDGE) — Gaming / faux work

"How would this department look productive while producing nothing? What
independent signal would expose it?" Wire that signal into the kill
controller inputs.

## Q14 — Escalation & human gates

- Escalation targets in order, with response SLAs and the no-reply rule
  (default: deny and re-file after TTL — NEVER auto-approve).
- For every action class: human gate or full-auto-with-escalation? Default is
  fully automated with escalation, EXCEPT the always-human floor: external
  sends, CRM/EHR/finance writes, publishes, spend over ceiling, charter
  changes, promotions. Those stay gated until the class earns promotion
  through the ladder (shadow → draft_only → gated_live → autonomous), and
  governance files stay human-only forever.

## Q15 — Records & memory

- Local records are always on (runs, heartbeats, STATE, approval queue,
  briefs) under `departments/<name>/state/`.
- "Do you want a durable copy anywhere else?" Offer the seams: S3 telemetry
  ship, Open Brain / memory-surface digests (sanitized only), or none.
  Anything remote requires deliberate wiring — the seam blocks until wired.
- Retention: what must never be recorded at all (secrets, PHI, raw message
  bodies)?

## Q16 — Readback

Present the concept map. Read the whole understanding back. Capture
corrections verbatim; fold; repeat until clean; then request INTENT LOCK
(owner sign-off, recorded with provenance). The lock is always human.
