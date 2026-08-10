# Finish line: the podcast automation works, and cannot fail silently

Owner directive, Ankit, 2026-08-10 evening: "finish this whole thing so the
podcast automation works without silent failure tomorrow."

This spec defines DONE precisely, so tomorrow is execution and not rediscovery.
Everything here is measured, not assumed; each unit names the executed proof that
closes it.

## What "works" means, stated as a test

A guest-acquisition loop run, on the real timer, ends with either:

- a REAL Gmail draft that exists at the provider, created by the loop, that
  passes the bridge's QA chain and enters the existing send lane; or
- a receipt naming every candidate considered and the specific gate or ceiling
  that disqualified each one.

Anything else is a failure, and the run says so loudly.

## What "cannot fail silently" means, stated as a test

Three properties, each independently proven:

1. **No untested seam.** Every caller/callee boundary in the chain has one
   executed check that runs the caller invoking the callee for real. Today only
   the runner/producer seam has one (`guest-producer-seam`, landed f71df2c).
2. **No signal collision.** No error path shares an observable with a benign
   path. The receipt is the authority: if the expected artifact is absent, the
   run fails, whatever the exit status said.
3. **Absence alarms.** If the loop produces zero drafts for N consecutive days
   while eligible candidates exist, something pages Ankit without anyone asking.

## State at the start of tomorrow

Landed and proven (podcast `f71df2c`, loop-factory `baced47`):
re-entry with metering and a thrash detector; four executable gates; the producer
module; the runner wiring; and the seam test proving the whole chain reaches
`verdict DROVE, drafts_created 1` in PLACEHOLDER_MODE.

Not done, and each one is a way the automation still does nothing:

| # | Gap | Consequence today |
|---|---|---|
| G1 | The source-truth revalidator does not exist | The runner's default `--source-truth` path points at a file nothing writes. The freshness gate blocks every candidate, forever, and that block looks legitimate. |
| G2 | Nothing writes the candidates file | The producer is handed an empty list and honestly reports `no_candidate`. Indistinguishable from a real drought. |
| G3 | Ceiling counts default to 0 | The charter's 12/day, 5 new contacts/day, 300/week cannot bite. The lane downstream enforces only a 7-day per-recipient cooldown. |
| G4 | The live Gmail path has never executed | `_live_gmail_service` and the real voice gate are unproven code. |
| G5 | No absence alarm | A zero-output week looks exactly like a quiet week. |

G1 and G2 are the reason the funnel would still show zero tomorrow even with
today's fix. They are the whole job.

## Units, in build order

Each unit: what it does, the executed proof, and the seam it must test.

### U8 — source-truth revalidator
Deterministic script that re-verifies the observables of the human-certified
authority manifest (each `location_ref` still exists, content hash matches) and
writes `process/proofs/source_truth_revalidation.json` with a fresh
`generated_at` plus any drift as `blocking_gaps`. Human judgment stays human: it
re-verifies, it never re-certifies.
**Proof:** running it writes a receipt the U7 freshness gate PASSES; corrupting
one source makes the gate BLOCK naming the drift. Both by execution.
**Seam:** the revalidator writes the exact path the runner passes.

### U9 — candidate feeder
Selects eligible candidates from `episodes/FOCUS-LIST.json` (17 targets, DEFICIT
mode today) and writes the candidates file the runner passes, in the producer's
declared shape, with aliases and no raw contact data beyond what the draft needs.
**Proof:** the seam scenario, with the feeder in the chain, still reaches
`drafts_created 1`; an empty focus list produces `no_candidate` with the reason
named, not a silent zero.
**Seam:** feeder output is consumed by the producer without transformation.

### U10 — real ceiling counts
Derive `sent_today`, `new_contacts_today`, `touches_this_week` from the same
FUNNEL-LEDGER evidence the funnel sensor already reads, and pass them.
**Proof:** with the ledger seeded past a ceiling, the producer returns `capped`
naming it and creates nothing. Already proven at module level; this proves the
FEED.
**Seam:** the runner's computed counts reach the producer's parameters.

### U11 — live-path proof
One run against real Gmail, creating one real draft. This is the first external
effect of the entire job.
**Proof:** the draft exists at the provider, carries the HubSpot BCC, and the
receipt records it. **GATED ON ANKIT** (see the open question).
**Seam:** `_live_gmail_service` and the real voice gate, neither ever executed.

### U12 — absence alarm (dead man's switch)
A department sensor that alarms when drafts-created stays 0 for N days while the
focus list is non-empty. This is the unit that makes the whole thing
silent-failure-proof rather than merely currently-working.
**Proof:** stop the producer, watch the alarm fire. A check nobody has watched
fail is not a check.
**Open sizing question, to answer before speccing:** does the estate watchdog
already track per-lane OUTPUT counts, or only per-lane liveness? If output, U12
is a threshold. If only heartbeats, it is a new sensor.

## Hard rules that do not relax tomorrow

- Every check EXECUTES; grep proves presence, only execution proves reachability.
- Every check is watched failing on the real defect before it is trusted green.
- Review before land, and never the same worker for both.
- One `run_name` (`loop-drive-contract`), Ringside on screen, lint before run.
- Charters, autonomy states and promotion thresholds stay human-only.
- No second unit until the first has produced a real artifact.

## ANSWERED: the first-draft gate (Ankit, 2026-08-10)

**Hold the first THREE drafts for his eyes, then revert to the autosend lane.**

Implementation, and it must be counted by evidence rather than by a flag someone
can forget to flip: the producer omits the autosend marker while the count of
drafts it has ever created is under 3, so those land as approve-required cards.
From the 4th onward it behaves exactly like every other podcast outreach draft.

The count comes from the producer's own ledger of created drafts, not from a
config toggle and not from a date. U11's proof must show draft 1 landing as
approve-required and a simulated 4th landing with the autosend marker; watch both.

Rationale, his: three letters is enough to see a pattern in the copy, one is
luck.
