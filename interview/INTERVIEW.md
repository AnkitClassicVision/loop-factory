# F1 Intent Interview Protocol (grill-me style)

The interview is the factory's front end. Its job is to extract the OWNER's
judgment — goals, edges, exceptions, escalations, floors — precisely enough
that everything downstream (concept map, procedural graph, charter, nodes) is
mechanical. A department whose interview was shallow will be wrong in ways no
QA gate can fix, because the QA gates check conformance to the interview.

## Hard rules

1. **One question at a time.** Never a questionnaire dump.
2. **Recommended answer first.** State the tension or unknown, give your
   recommended answer and why, then offer 2-4 concrete options only when the
   choice is real. A/B options beat open-ended questions.
3. **Verbatim capture.** The owner's answers are recorded word-for-word in the
   interview artifact, followed by an "Interviewer reading (to be confirmed,
   not assumed)" paragraph. The concept map derives ONLY from what is captured.
4. **Evidence before questions.** If existing data, code, CRM records, or docs
   can answer a question, inspect them instead of asking. Say what you found
   and where. If evidence conflicts with an answer, stop and ask about the
   conflict.
5. **Dependency-first order.** Outcome owed → process inventory → proving
   slice → setpoints → floors (NEVER list) → budget → kill conditions →
   problem-type triage → EDGE GRILL → escalation & human gates → memory →
   readback. Do not skip ahead to implementation details.
6. **The interview continues until every charter field has an owner-confirmed
   answer in the owner's own words.** Unresolved items are recorded as open
   questions, never fake definitions.
7. **Readback rounds.** After the first pass, present the draft concept map
   and read the whole thing back. Owner corrections are captured verbatim and
   folded. Repeat until the owner has nothing to correct.
8. **INTENT LOCK is always human.** The interview closes only when the owner
   signs off on the concept map, recorded with provenance (who, when, via what
   surface). A machine cannot self-certify intent — at any autonomy level,
   ever. An L4 process heal that would change WHAT the department is for
   (not just HOW it works) reopens this interview.

## The Edge Grill (the part most interviews skip)

After the happy path is captured, switch to adversarial mode. For every
process/funnel the owner described, grill at minimum:

- **Boundary overlaps:** who else touches these people/records/systems? What
  happens when a contact/entity lives in two processes at once? (Frequency and
  suppression must apply across processes, not per-process.)
- **Weird inputs:** the ambiguous record, the unresolvable identity, the entity
  with no CRM stage, the out-of-the-blue arrival that skips the normal intake.
- **Exception handling:** for each step, what does the owner want when it
  fails — retry, skip, quarantine, or wake a human? What's the ONE question
  the escalation should ask?
- **The subtle failure the owner has already lived.** Ask directly: "what has
  gone wrong before that was subtle, not obvious?" (Precedent: drafting without
  full context was named a 'massive failure that's more subtle' in the first
  department's interview — it became a first-class QA gate.)
- **Gaming and faux work:** how would this department look busy while
  producing nothing? What independent sensor would catch it?
- **Who gets woken up:** escalation targets, in order, with response SLAs.
- **Human gate vs full auto:** for every action class, does the owner want a
  human decision (gate) or full automation with escalation on breach? DEFAULT
  IS FULLY AUTOMATED WITH ESCALATION — but external sends, CRM/finance writes,
  publishes, charter changes, and promotions are ALWAYS human-gated until the
  action class is promoted through the earned-autonomy ladder, and governance
  files are human-only forever.
- **Kill vs pause:** what condition means kill (non-restartable without a new
  human charter decision), not just pause?

Use `interview/QUESTION_BANK.md` for the full question set with recommended
framings. Skipping a bank section requires writing a one-line rationale in the
artifact — the same not_applicable discipline the graphs use.

## Outputs (all committed with the department)

1. `departments/<name>/interview/intent-interview.md` — the verbatim artifact.
2. Concept map (`knowledge/concept-map.md`) — WHY, derived only from the
   artifact, locked by the owner.
3. Then F2 begins: charter + procedural graph + subgraphs.json, every node
   tracing back to this interview. Anything that traces to nothing is a lint
   failure.
