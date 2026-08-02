# Survey: JayZeeDesign/codebase-harness

Source: https://github.com/JayZeeDesign/codebase-harness, commit `8edc6af`
(single commit, "feat: add setup-codebase-harness master orchestrator skill").
Cloned read-only into scratchpad for full-text reading; nothing committed
anywhere. Repo is small: a README plus four Claude Code `SKILL.md` files
(538 lines total, no application code, no schemas, no scripts beyond one bash
template). This survey reads all of it — figures below are exhaustive, not a
sample.

## What it actually is

Not a runtime harness with telemetry/evaluator code — it's a **skill pack for
Claude Code** that a human invokes by name ("set up the harness for this
repo") to onboard an *unfamiliar* repo for agent-driven development. Everything
fires inside a chat session; there is no daemon, cron, or background watcher
anywhere in the repo. It organizes around three pillars — legible, executable,
verifiable — orchestrated by `skills/setup-codebase-harness/SKILL.md`:

- `skills/dev-local-setup/SKILL.md` (94 lines) + `assets/dev-local.template.sh`
  (138 lines) — investigate a repo's services/ports/infra, generate one
  `scripts/dev-local.sh` (tmux-based, `up/down/status/logs/restart/attach`,
  idempotent, discovery-driven not convention-guessed).
- `skills/e2e-setup/SKILL.md` (64 lines) — stand up a trustworthy e2e gate:
  real flows (never hardcode OTP/bypass), one session helper so only the
  auth spec re-pays the login cost, layered client→server→product
  assertions, video+trace evidence, triage red as real-bug/stale-test/flaky
  *before* touching anything.
- `skills/pr/SKILL.md` (92 lines) — the verify-before-ship loop: a fresh,
  read-only verifier sub-agent drives the real app to judge the feature
  ("subjective" check, delegated because independence + context isolation
  both pay off), then the orchestrating agent runs codified checks itself
  ("objective" check — delegating buys nothing, you need the raw error to
  fix it), capped at ~3 fix/re-verify rounds before escalating to a human.
  Never opens a PR on red; ships a reviewable proof link, not a claim.

## Mapped against loop-factory's three target tickets

### (a) Run-record / telemetry contract — ticket `07-run-record-contract.md`

Weak match. The repo has **no** telemetry schema, no run_id/engine/token
fields, nothing resembling ticket 07's field list — nothing to port there.
The one transferable shred: `skills/pr/SKILL.md` lines 50-56 define a strict,
minimal verdict envelope the verifier sub-agent must return and nothing else:

```
FEATURE: works | broken
  expected: <criteria>
  observed: <what actually happened>
  evidence: <screenshot/video paths>
```

That `expected / observed / evidence / verdict` shape is a reasonable
sub-record for ticket 07's `evaluator results` field — cite it as a minimal
envelope pattern, not as a source for the run-record schema itself, which
this repo doesn't attempt.

### (b) Evaluator framework — ticket `09-evaluator-framework.md`

Strongest match. `skills/pr/SKILL.md` lines 13-24 draw exactly the line
ticket 09 is deciding: deterministic, codified checks (type-check, lint,
unit, existing e2e) are run by the orchestrator itself, after, as a
regression sweep — "delegating buys nothing but a round-trip, and you need
the error to fix it." Model/agentic judgment is reserved for the one thing
with no spec — does the feature do what was intended — and *must* run on an
independent, fresh sub-agent that didn't write the code (lines 13-19), which
is the same independence principle behind loop-factory's cross-model
requirement for promotion-counted runs. Two more transferable pieces:

- `skills/e2e-setup/SKILL.md` lines 50-58 — a red check is triaged
  (real-bug / stale-test / flaky-env) *before* any fix, and "never weaken or
  delete an assertion just to go green" unless the contract itself changed,
  confirmed from the diff. Direct precedent for deny-by-default gate
  receipts: a failing evaluator is information, not something to route
  around.
- `skills/e2e-setup/SKILL.md` lines 18-21 — unit/integration tests live
  inside each owning package; system e2e is a separate top-level suite
  because it spans all of them and belongs to none. Weak but real precedent
  for splitting node-scoped deterministic checks from cross-department
  evaluator suites in the eval registry ticket 09 wants.

### (c) Watch loops — ticket `13-gated-auto-patch-class.md`

Good shape, wrong altitude. The repo has zero daemon/cron/background-watcher
code, so it offers nothing for ticket 13's actual hardest question —
who watches the watcher, review cadence, per-department promotion ladder.
Flag that gap honestly; loop-factory has to design that fresh. What *does*
transfer is the fix→reverify loop discipline in `skills/pr/SKILL.md`:

- Lines 58-60: cap fix/re-verify at ~3 rounds, then escalate to the human
  with the verdict — this is the same shape as ticket 13's 3-strike
  demotion back to propose-only, just phrased per-incident instead of
  per-node-history.
- Lines 62-65: if a regression-sweep fix changes feature behavior, it
  invalidates the prior verification and forces a fresh re-verify pass —
  i.e., a fix in one lane can't reuse stale proof from another lane. Direct
  precedent for "passing executed QA + full re-shadow + re-pin" as the
  required receipt before an auto-patch counts as proven.
- Lines 86-89 ("the feature is the verdict," never ship on red, proof not
  claims) is the same posture as loop-factory's deny-by-default + hard rule
  8 (receipt-gated steps) — convergent validation, not new information.
- `skills/e2e-setup/SKILL.md` lines 60-64 — external-service tests must
  "refuse to run if it detects a live key/credential" rather than degrading
  silently. Worth lifting verbatim as a pattern for the watcher's own
  guardrail: fail closed and loud on an out-of-scope target, don't warn and
  continue.

## Other things noticed, lower priority

- `skills/setup-codebase-harness/SKILL.md` lines 40-46 pushes root
  `AGENTS.md`/`CLAUDE.md` down to a ~100-line table of contents with depth
  moved into a `docs/` system-of-record — loop-factory's
  `charter.yaml` + `concept-map.md` + `procedural-graph.md` split already is
  this pattern. Confirms the existing design rather than suggesting a change.
- Lines 47-51 (same file): custom lints whose error message *is* the fix
  ("X isn't allowed here — do Y") is a good general habit for
  `ringer/one-true-master/checks/*` but isn't scoped to tickets 07/09/13, so
  it's not carried into the recommendation above.

## Bottom line for tickets 07/09/13

Nothing here to port wholesale — it's a small, single-commit, chat-invoked
skill pack, not a telemetry/evaluator/supervisor system. The reusable
substance is entirely in `skills/pr/SKILL.md`: deterministic-checks-run-by-you
vs subjective-judgment-by-an-independent-fresh-agent as the evaluator split
(09), the expected/observed/evidence verdict envelope as a record sub-shape
(07), and the capped-retry-then-escalate + no-stale-proof-across-fixes loop
discipline (13). The watcher-of-watchers question in 13 has no prior art here
at all.
