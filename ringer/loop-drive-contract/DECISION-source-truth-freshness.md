# Source-truth staleness resolution (pre-U2a, 2026-08-10)

CANARY: blue paperclip

Fable's blocker finding, verified 2026-08-07: `process/proofs/source_room_authority_manifest.json`
is stamped 2026-06-25, nothing in production refreshes it, so a
`source_truth_resolved_before_intake` gate keyed on 7-day manifest staleness would
block guest acquisition permanently.

## Decision (coordinator, within owner-signed wave-1 scope)

Split **authority** from **freshness-of-revalidation**:

- The manifest's authority ranks and room_state stay HUMAN-certified (H1 source-room
  process). No script re-certifies judgment.
- A small deterministic **revalidator** re-verifies the observables: each certified
  source's `location_ref` still exists and its content hash matches what was
  recorded; any drift becomes a `blocking_gaps` entry. It writes
  `process/proofs/source_truth_revalidation.json` with a fresh `generated_at`,
  `room_state` copied from the manifest, and the drift findings.
- The U7 gate `source_truth_resolved_before_intake` is INPUT-AGNOSTIC (it checks
  whatever packet path it is handed for parseability + generated_at freshness).
  Wave-2 wiring hands it the REVALIDATION receipt, not the raw manifest. Intake
  therefore runs while the certified sources are stable, and blocks exactly when
  reality drifted or the revalidator stopped running — both true alarms.

## Consequences

- No change to the r7 U7 module contract (already input-agnostic).
- The revalidator is a small deterministic script: build as a one-task Ringer
  manifest alongside wave 2 (U2a wiring names its receipt path).
- Owner-visible, not blocking: the manifest itself is 46 days old and references
  June-era specs; an H1 source-room re-review is owed on its own merits. The
  hashes the revalidator records will be against the sources as certified then.
