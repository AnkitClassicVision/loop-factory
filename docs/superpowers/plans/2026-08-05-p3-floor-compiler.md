# P3 (machine slice): Cohort Ledger + Floor Compiler, Alarm-Only — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Departments get a PII-free cohort transition ledger and a deterministic floor compiler that derives flow/stock floors from charter goals + matured measured rates, writing an append-only floors history and raising alarm-only observations — no actuation, no dispatch, no charter writes.

**Architecture:** Phase P3 machine slice of `docs/superpowers/specs/2026-08-05-loop-brain-reconcile-design.md` (C3). Two factory modules (`events_ledger.py`, `floor_compiler.py`), an OPTIONAL `funnel:` charter section parsed by `charter_loader.py` (absent = compiler reports `unconfigured`, changes nothing), one podcast runtime node on the daily chain (weekly-due gating inside the node), roster/shell wiring, map row, re-pin. The HUMAN slice of P3 — the sales-department interview, intent lock, and any charter amendment adding `funnel:` goals — is Ankit's and is NOT in this plan; hard rule 3 (charters humans-only) is why the compiler must handle `unconfigured` gracefully forever.

**Tech Stack:** Python 3 stdlib, pytest, bash. Charter parsing reuses `factory/charter_loader.py` conventions.

## Global Constraints

- Hard rule 3: NOTHING in this plan writes charter.yaml. The compiler READS an optional section and derives floors; floors.yaml is a machine-written derived snapshot that heals never touch.
- Alarm-only: the node emits observations and records; it never enqueues work, never touches caps, never sends.
- Deny-by-default: unreadable events, unparseable charter funnel section, or failed reconciliation preconditions → FREEZE (no floor increases) + `unknown`/`alarm` observation, never a guessed number. Unmeasured is unknown, never zero.
- No PII: the events ledger rejects rows whose string values match email/phone patterns or that carry disallowed keys. Opaque subject ids only.
- Watch every new test fail RED first. Release-tree files change (runtime node, shell, roster) → re-pin at integration.

## Frozen interfaces

`factory/events_ledger.py`:

- `append_event(state_dir, *, subject_id, from_stage, to_stage, ts=None, meta=None) -> Path` — appends one JSON line to `<state>/events.jsonl`. ALLOWED row keys exactly: `subject_id, from_stage, to_stage, ts, meta`; `meta` value keys must be in `{"source", "cohort", "note"}`. Reject (raise `LedgerError`) on: non-string/empty subject_id or stages, any string value matching `[^@\s]+@[^@\s]+\.[^@\s]+` (email) or 7+ consecutive digits (phone-like), duplicate `(subject_id, from_stage, to_stage)` within the same UTC day (double-count guard).
- `read_transitions(state_dir, *, from_stage, to_stage, since, until) -> list[dict]` — parsed rows in the window; malformed lines are counted, not skipped silently: returns via second element `(rows, malformed_count)` — actually return a `TransitionWindow` dataclass with `.rows` and `.malformed`.

`factory/floor_compiler.py`:

- `compile_floors(dept_dir, state_dir, *, now=None) -> dict` — the verdict-shaped result:
  `{"status": "ok"|"frozen"|"unconfigured", "reason": str, "floors": {stage: {"flow_per_week": int, "stock_min": int, "rate_used": float, "rate_source": "prior"|"measured"|"blended"}}, "changes": [{stage, field, old, new}], "computed_at": iso}`.
- Math (spec C3, exact): `flow_i = ceil(flow_downstream / rate_i * (1 + buffer_i))`; `stock_i = ceil(flow_i * lead_days_i / 7 * (1 + stock_buffer_i))`. Rates: measured 4-week matured cohorts only (entrant matured when `entry_ts + maturity_days <= now`), minimums 30 entrants / 10 conversions, blend `0.75*old + 0.25*measured`; below minimums → prior, `rate_source: "prior"`.
- Change discipline: vs the previous floors snapshot — deadband: no change while attainment data is absent (this phase never measures attainment; deadband applies to RATE-driven floor moves): a floor may move at most ±20% per compile AND at most once per 7 days (the node enforces weekly cadence; the compiler enforces the cap). FREEZE (status frozen, floors = previous, no changes) when: `state_drift != 0` or `unledgered_inbound != 0` in `<state>/objectives_observed.json` (read the keys the funnel_floor_sensor already writes — READ that file's real shape first and match it), OR newest run-manifest verdict is red, OR events ledger malformed_count > 0.
- Persistence: write `<dept>/floors.yaml` (whole-file machine snapshot with a `# MACHINE-WRITTEN — derived; humans set goals in charter.yaml` header) and append one line to `<state>/floors-history.jsonl` per compile (the full result dict). Never edit history.
- Charter source: `charter_loader.load_charter()` result gains OPTIONAL `funnel` via a new `funnel_config(charter) -> dict | None` helper: section shape in charter.yaml (READ-ONLY here; podcast does not have it yet):

```yaml
funnel:
  end_goal: {stage: <terminal stage>, per_week: <int>}
  transitions:            # ordered upstream ← downstream
    - {from: <stage>, to: <stage>, prior_rate: 0.6, buffer: 0.15, lead_days: 5, maturity_days: 14, stock_buffer: 0.2}
```

  `funnel_config` returns None when the section is absent; raises `CharterError` on a malformed section (bad rates outside (0,1], negative days, unknown keys).

Podcast node `departments/podcast/runtime/floor_compiler_run.py`:

- CLI `--state-dir --dept-dir --shadow`; exits 0 always unless crashed (1). Weekly gate INSIDE: if newest floors-history line is younger than 7 days → emit run record with status ok and a `skipped_not_due` artifact note, exit 0, NO observation. Otherwise call `compile_floors`; observation per result: `unconfigured` → sensor `floors`, status `unknown`; `frozen` → status `alarm`; `ok` with changes → status `alarm` (owner visibility of every floor move: full-auto WITH alarm-after); `ok` no changes → status `ok`. Always `runrecord.emit_record` (fatal on failure, P0 discipline).
- compare_charter transitions: `("floors","alarm") -> ("floors_moved_or_frozen","med")`, `("floors","unknown") -> ("floors_unconfigured","low"... )` — compare_charter has no low; use "med" for alarm and "med" for unknown? Match existing severity vocabulary: use ("floors","alarm") -> ("floors_attention","med") and ("floors","unknown") -> ("floors_unconfigured","med"), MEANINGS in owner language: attention = "The funnel floor compiler either moved a floor (working as designed, shown for your awareness) or froze because its input data cannot be trusted — the detail says which."; unconfigured = "No funnel goals are declared in the charter, so no floors are being derived; the hand-set charter floors still stand." QUESTIONS["floors"]: "Did a floor move for a data-backed reason, or is the compiler frozen/unconfigured and why?"

## Tasks

### Task 1 (lane A): events ledger + floor compiler + podcast node + tests

**Files:**
- Create: `factory/events_ledger.py`, `factory/floor_compiler.py`, `departments/podcast/runtime/floor_compiler_run.py`
- Modify: `factory/charter_loader.py` (add `funnel_config` helper only)
- Test: `tests/test_events_ledger.py`, `tests/test_floor_compiler.py`, `departments/podcast/tests/test_floor_compiler_run.py` (all create)

- [ ] Failing tests first, then implement, per module:
  - ledger: append happy path; email/phone/PII rejection; disallowed keys rejection; same-day duplicate rejection; window read + malformed count.
  - compiler: `unconfigured` when charter has no funnel section; golden cascade (fixture charter funnel with 3 transitions, priors only → exact ceil numbers asserted); measured-rate blend kicks in at 30/10 with a synthetic matured ledger; immature cohorts (29 entrants) stay on priors; ±20% cap (measured rate would triple a floor → capped move + change recorded); FREEZE on drift key, on red verdict fixture, on malformed ledger line; floors.yaml written with the machine header; history appended once per compile.
  - node: not-due skip (fresh history line → no observation); due + unconfigured → `floors` unknown observation; emit_record always present; crash path exits 1.
  - charter_loader: `funnel_config` None on absent; parses the documented shape; raises on rate 1.5, negative lead_days, unknown key.
- [ ] Green gate: `PYTHONPATH="$PWD" python3 -m pytest tests/test_events_ledger.py tests/test_floor_compiler.py tests/test_charter_loader*.py departments/podcast/tests/test_floor_compiler_run.py -v` (run whatever charter_loader test file already exists too; do not modify it).

### Task 2 (lane B): wiring — shell, roster, compare transitions, static pins

**Files:**
- Modify: `departments/podcast/runtime/podcast_daily.sh` (insert the node invocation after `funnel_floor_sensor` line, before `expectation_reconcile`: `python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/floor_compiler_run.py" --shadow --state-dir "${STATE_DIR}" --dept-dir "${REPO}/departments/${DEPARTMENT}"`)
- Modify: `departments/podcast/runtime/run-roster.json` (insert node `floor_compiler_run` with the right ordinal — renumber subsequent ordinals consistently; required true)
- Modify: `departments/podcast/runtime/compare_charter.py` (the two `floors` transitions + MEANINGS + QUESTIONS from the frozen section)
- Test: extend `departments/podcast/tests/test_compare_charter.py` (two mapping tests, existing style) and `departments/podcast/tests/test_daily_failclosed.py` (static pin: floor_compiler_run invoked between funnel_floor_sensor and expectation_reconcile) — `test_run_roster.py` must pass UNCHANGED (it derives from the shell + roster, which is the point).

### Task 3 (coordinator): integrate, subgraph node, map, shadow, re-pin

- [ ] Apply patches; full `loopfactory.py check` + podcast suite.
- [ ] Add `floor_compiler_run` node to subgraphs.json SG-WATCHDOG (id N13, impl `runtime/floor_compiler_run.py`) + regenerate the surface (`python3 -m factory.surface_compiler generate --dept-dir departments/podcast`) so drift CI stays green; procedural-graph.md N13 row (alarm-only contract, weekly gate, unconfigured until the owner adds `funnel:` goals).
- [ ] Validate, shadow run (expect: `floors` unknown observation on first due run — unconfigured is the honest state), re-pin, qa, commit, ledger checkpoint.
- [ ] HANDOFF TO ANKIT (the human slice): sales-department F1 interview + intent lock; charter `funnel:` amendment for podcast (goals + priors) — drafted for review, never self-signed.

## Self-Review Notes

- Spec C3 minus actuation and attainment-based control policy (both need dispatch/conductor, P4/P5) — floors derive and alarm only. Bottleneck attribution needs attainment measurement, deferred with it.
- The freeze precondition reads `objectives_observed.json` — the shape must be read from the real file before coding (worker instruction included).
- compare severities stay in the existing vocabulary (med) — advisory phase.
