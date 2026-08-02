# Bee notes retrieval: 2026-08-02 graph-engineering workshop, delta vs Hermes packet

Answers Wayfinder ticket `05-bee-notes-retrieval.md`.

## Retrieval status

`bee-cli` (`@beeai/cli 0.6.1`, authenticated as Ankit Patel) is working today —
the "broader Bee index timed out" issue noted in the ticket did not reproduce.
`bee conversations list --limit 20` returned conversation `9614305`
("AI Graph Engineering Workshop Participation," 2026-08-02 08:01-08:22 ET,
state COMPLETED), and `bee conversations get 9614305` returned the full
utterance transcript on the first attempt — no retries needed.

The live transcript is byte-for-byte consistent with an already-saved local
copy at
`/mnt/d_drive/repos/OB_mybcat/docs/meetings/bee/2026-08-02-jason-graph-engineering-workshop-transcript.md`
(and its raw JSON sibling), which a different research pass produced earlier
today. Both stop mid-sentence at 08:16:11 AM ("...a re complete refactoring of
this learning systems, ongoing learning systems") even though Bee's own
metadata says the conversation ran until 08:22:38. That cutoff is a
transcription/capture gap on Bee's side (confirmed by two independent
retrievals landing on the identical stopping point), not a tool failure on
this end — there is no more content to recover for this session by this
method.

**Important cross-reference found during this task:** an older-numbering
ticket, `wayfinder/tickets/003-bee-workshop-notes.md`, already closed today
("Resolved: 2026-08-02 by codex search sub-agent") against this exact same
Bee session, using the saved transcript plus a derived
`2026-08-02-jason-graphs-to-mybcat-implementation-map.md`. Its resolution
summary already captures most of what a fresh read of the transcript
surfaces: the three-graph split, "watchers can fail," the three trigger
modes, and the planner/executor split. The delta below is filtered against
**both** the Hermes packet points and ticket 003's prior resolution, so it
only lists what neither source already covers.

## Delta vs the Hermes ELI5 packet

The Hermes packet's six points (three-graph distinction; watchers need
owner/evaluator/limits/cadence; 5-layer department→factory→run→step→receipt
model; 10-step loop creation sequence; auth classes
oauth_cli/service_oauth/local_model/vault_api_key/blocked; the 15-item
missing list) are architectural/governance framing. The transcript is a
practitioner's workshop talk — it doesn't touch the 5-layer model, the
10-step sequence, or the auth-class taxonomy at all, and it doesn't add to
the 15-item missing list. What it does add is below, each item scored for
whether it's a real requirement/idea delta or just supporting color.

### 1. Watchers are known to still fail even when present — not a solved pattern
**Real delta.** The Hermes bullet states watchers need their own
owner/evaluator/limits/cadence — a structural requirement. The transcript
adds a stronger, more specific claim from the workshop's own source
material: *"for each loop you are running, you can probably have another
watch loop to look at the results... but it's still very early, and even if
you do that, it might still fail from some past experience."* This is a
caution against treating "add a watch loop" as sufficient defense-in-depth —
it explicitly failed before, for reasons the facilitator didn't resolve live.
- **Maps to:** ticket `09-evaluator-framework.md` (the evaluator ladder
  should not rely on a single watcher's self-report) and ticket
  `13-gated-auto-patch-class.md` (the auto-patch gating class should assume
  the watch loop itself can be wrong, not just the thing it's watching).
  Concretely: bake in an independent, second-order check (already the shape
  of loop-factory's `estate-deadman.timer` idea from ticket 003's resolution)
  rather than a single watcher layer.

### 2. Named orchestration pattern: captain → first-mate planner → per-repo/session workers in isolated worktrees, merged back
**Real delta.** Beyond "planner/executor split" as a label, the transcript
describes a specific mechanic: a human ("captain") talks to one dispatcher
agent ("first mate") that analyzes the request, breaks it into a plan, then
fans work out across separate agent sessions — each with its own git
worktree — which eventually merge together. The facilitator names a
specific tool for this ("Firstmate," attributed to a builder referred to as
"Quinchen" in the transcript — likely a mis-transcription; not independently
verified) and states plainly: *"to achieve something like that, you also
need a kind of state management system... it breaks down tasks, and each
task connects to one session."*
- **Maps to:** ticket `03-codebase-harness-survey.md` most directly — that
  ticket already surveys Jason's own `codebase-harness` repo (pointer
  captured the same day), which is presumably built around this exact
  pattern. Secondary relevance to ticket `07-run-record-contract.md`: the
  run-record contract should have a field that ties a task to the specific
  session/worktree executing it, so multiple in-flight delegated sessions
  are individually trackable and mergeable, not just logged as one run.

### 3. Spec artifacts live in a queryable database (MSD ↔ spec docs ↔ ADRs), not just flat files
**Real delta.** Ankit's own description of his workflow: a "Grill Me"-style
interrogation produces a Micro Spec Document (MSD), which is "connected in
the database to a very detailed spec, and actually now multiple spec
documents... multiple spec documents and ADRs." This implies a relational
store linking spec artifacts to each other and to ADRs, not the flat-file
`interview/` + `charter.yaml` + `knowledge/concept-map.md` layout loop-factory
uses today.
- **Maps to:** ticket `02-knowledge-repo-scripts-survey.md` — closest fit,
  since that ticket already surveys concept-to-process scripts
  (`atlas_to_concept.py`, `concept_to_process.py`, `evaluate_node.py`) for
  patterns transferable to loop-factory. Whether loop-factory's intent
  artifacts should move from files to a queryable store is a design question
  that ticket can absorb rather than needing a new ticket.

### 4. "Repo expert agent" as a standing, repo-bound role (distinct from the general orchestrator)
**Real delta.** The transcript describes execution handing off "to a repo
expert agent that deals with that repo's work" — a specialized, apparently
persistent agent role scoped to one repository, separate from the
general-purpose planner/orchestrator. This is a distinct concept from
"an executor session," implying standing per-repo context/expertise rather
than a one-off dispatched task.
- **Maps to:** ticket `03-codebase-harness-survey.md` again — this is very
  likely the exact thing Jason's codebase-harness repo implements, which is
  why that survey ticket already exists and cites the same 2026-08-02
  pointer capture.

### 5. Trigger taxonomy refinement: time-based / goal-based / event-based, each needing explicit boundaries and persistent state
**Minor delta, mostly confirmation.** The transcript names three trigger
classes explicitly (time: "every day, someone looks at GitHub issues";
goal: "clear boundaries about the definition of done... continues until you
finish"; event: "every time you receive a support ticket"). This sharpens
rather than adds to what ticket `07-run-record-contract.md` already lists as
a `trigger` field — worth using this three-way taxonomy as the enum values
if ticket 07 doesn't already have one, but it isn't a new requirement.
- **Maps to:** ticket `07-run-record-contract.md` (refinement only).

### 6. Terminology provenance (not actionable)
The three-graph split traces to a Peter Bee/Twitter debate; "knowledge
graph" got pulled in because of Andrew Ng's "Agentic Knowledge Graph"
DeepLearning.AI course; "graph of loops" is attributed to a Twitter user
named "Carlos," described as "the only one that's truly new." This is
citation/pedigree color with no operational content — flagging only so
nobody re-derives it as if it were new; it doesn't warrant a ticket.

### 7. Unresolved thread — do not fabricate
The transcript cuts off mid-sentence at 08:16:11 describing "a
re[-]complete refactoring of this learning systems, ongoing learning
systems." There is no available content past this point from Bee (confirmed
by two independent retrievals hitting the same wall). Whatever Ankit was
about to say about his own "ongoing learning systems" refactor is not
recoverable from this source. Flagging as a known gap rather than guessing
at its content or assigning it to a ticket.

## Bottom line

No delta here requires a new ticket. Four items (1, 2, 3, 4) are real
requirement-shaping deltas and slot into existing tickets 09, 13, 03
(primary), 07, and 02 as noted above. One item (5) is a minor refinement to
ticket 07. Two items (6, 7) are non-actionable — provenance color and an
unrecoverable cutoff, respectively — and are called out only so they aren't
mistaken for gaps that still need chasing.
