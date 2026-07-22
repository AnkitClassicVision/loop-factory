# Known Limits (v0.1) — honest ledger of what is NOT yet enforced

Two independent read-only reviews (Codex, 20 findings; Gemini via Antigravity,
9 findings) ran against v0.1; both returned FIX-FIRST. The cheap, real fixes
were applied (see git history). This file records what remains open BY DESIGN
at this stage, so nobody mistakes v0.1's shadow posture for production
enforcement. Every item here is a **go-live blocker**: no department may leave
shadow-simulate while it stands.

## 1. The kernel is in-process (Codex P0 #3)

`LockService` is an in-process object. Department code that holds the object
could reach its signer and ledgers. The REAL boundary is operational, mirroring
the source design's B-KERNEL/OPS gates: run the kernel in a separate trusted
process (local RPC), launch departments through `factory/launch.py` (no
`OE_KERNEL_SIGNING_KEY`, no credentials), systemd confinement from
`templates/systemd/`, and swap `LocalSigner` → `KMSSigner`. Until those are
done, in-process shadow use is acceptable ONLY because shadow has zero real
effects (fake sinks, simulate tokens).

## 2. Sanitization is attested, not independently proven (Codex P0 #2, partial fix)

`request_model(..., sanitized=True)` now refuses un-attested prompts, but the
attestation comes from the calling pipeline (the S3 privacy-preflight node),
not from a separate trusted sanitizer service that signs what it sanitized.
Full separation = a sanitizer that issues its own receipt over the sanitized
payload hash. Track for the first gated-live promotion.

## 3. Signing keys: LocalSigner is shadow-tier (Codex #4, partial fix)

Empty keys are now rejected, but LocalSigner accepts weak keys and is meant
for tests/shadow. Production classes require the KMS seam wired and the key in
a secret store, never env-of-record.

## 4. Ledgers assume a single kernel process (Codex #10)

Nonce/budget/frequency ledgers are fsync'd JSONL with in-memory snapshots —
durable across restarts (tested) but not safe under CONCURRENT kernel
processes. The deployment contract is one kernel per host (single-writer, same
discipline as manager epoch fencing). Multi-process needs a transactional
store with unique constraints.

## 5. Release eligibility is charter+maps, not full F1-F4 proof (Codex #6, partial fix)

`release pin` now requires a valid charter and clean map QA and stamps
`intent_locked` on its output, but it does not yet demand INTENT LOCK
provenance, owner review evidence, executed node-QA receipts, or a
zero-delivery shadow report. Those checks belong to the promotion gate
(runbooks/promotion-ladder.md) and remain human-verified in v0.1.

## 6. Charter placeholder strictness (Codex #7, partial fix)

The loader now rejects a charter whose `department` disagrees with its
directory, but F0 scaffold placeholders (TODO_F1 mission/setpoints) still load
— deliberately, so the manager can watch a not-yet-interviewed department.
Rejecting placeholders is the F2 owner-review step, a human gate.

## 7. Manager telemetry contract is outreach-shaped (Codex #12, partial fix)

`sense()` implements the standard approval-queue/touch telemetry. Departments
with a different worker shape pass `sense_fn=` to `run_manager_cycle` — the
Sense→Compare→Decide→Record discipline is the invariant, not the field names.

## 8. Approval packets and shadow sinks hold draft content locally (Codex #16)

The human-in-the-loop packet contains the draft BY DESIGN (the owner approves
off exactly what would go out), and the shadow sink records what would have
been sent (that is the shadow proof). Both live under gitignored local `state/`
dirs. The data-plane rule stands: nothing under state/ ships to any remote
backend un-redacted (`factory/memory.py` seams are fail-closed).

## 9. Heal ladder time and QA proof are caller-supplied (Codex #15)

Deterministic by design (no wall clock in scripts); the supervisor supplies
time, and `record_success` trusts the caller's executed check. A signed
QA-success receipt is the upgrade path.

## 10. Estate v1 escalates; it does not park (Codex #14, partial fix)

The estate CLI now inventories on-disk departments (unregistered = drift
breach), but the `park` verb, schedule-aware staleness, and heartbeat-content
checks are gated-live estate work. v1 is a shadow watchdog: detect + escalate.

## Rejected review suggestions (do NOT "fix" these)

- **Adding `OE_KERNEL_SIGNING_KEY` to the department env allowlist** (Gemini
  #4): the allowlist EXCLUDES the signing key on purpose — a department that
  can read the key can mint its own receipts. The kernel runs in the trusted
  context; departments fail closed on the missing key. Working as designed.
