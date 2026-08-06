# P0: Close Silent Skips + Wire Budget Telemetry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the podcast daily chain unable to skip silently: expectation failures become first-class findings instead of `|| true` noise, and missing budget telemetry becomes a breach instead of reading as zero spend.

**Architecture:** Phase P0 of the approved Conductor v2 spec (`docs/superpowers/specs/2026-08-05-loop-brain-reconcile-design.md`). Three thin, reversible changes: (1) `expectation_reconcile.py` splits its exit codes (0 ok / 2 findings / 1 crash) and `podcast_daily.sh` handles 2 as an alarm verdict exactly like `dag_supervisor`; (2) `compare_charter.py` gains the missing `expectation` sensor transitions so findings escalate instead of raising; (3) `factory/manager.py` gains a `--budget` path with fail-closed missing-file semantics, wired into the podcast daily invocation.

**Tech Stack:** Python 3 stdlib only, bash, pytest. No new dependencies.

## Global Constraints

- Shadow-only: no change may create an external effect; kernel gateways untouched.
- Deny-by-default: never add an allow-on-failure path; every failure is a record + finding.
- Records always: node runs keep appending runs/runs-v2; no emit becomes optional.
- No PHI, secrets, or guest identities in tests, fixtures, or findings text.
- Every new check is watched failing RED before its implementation lands (TDD).
- Department runtime files (`departments/podcast/runtime/*`, `compare_charter.py`) are inside the pinned release tree: after the code lands, process-change QA + re-pin (Task 8) is MANDATORY or the manager will alarm release drift. That alarm firing before Task 8 completes is expected and correct — do not suppress it.
- Expected new standing card after P0 ships: `budget_telemetry_missing` (spend is genuinely unverifiable until the P1 producer lands). This is intended; do not "fix" it by softening the finding.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `departments/podcast/runtime/expectation_reconcile.py` | Modify (exit codes) | Sensor: declared expectations vs reality |
| `departments/podcast/runtime/compare_charter.py` | Modify (transition table) | Finite observation→finding compiler |
| `departments/podcast/runtime/podcast_daily.sh` | Modify (lines 127, 152) | Daily chain orchestration |
| `factory/manager.py` | Modify (sense/compare/CLI) | Manager Sense→Compare→Decide→Record |
| `templates/department_daily.sh.template` | Modify (line 25) | Future departments inherit the wiring |
| `departments/podcast/tests/test_expectation_exitcodes.py` | Create | Exit-code contract |
| `departments/podcast/tests/test_compare_charter.py` | Extend | Expectation transitions |
| `departments/podcast/tests/test_daily_failclosed.py` | Extend | Shell regression pins |
| `departments/podcast/tests/test_records_integrity.py` | Extend | Emit-propagation regression |
| `tests/test_manager_budget_failclosed.py` | Create | Budget fail-closed semantics |

---

### Task 1: expectation_reconcile exit-code split (0 ok / 2 findings / 1 crash)

**Files:**
- Modify: `departments/podcast/runtime/expectation_reconcile.py:143` (the `return` in `main()`)
- Test: `departments/podcast/tests/test_expectation_exitcodes.py` (create)

**Interfaces:**
- Produces: process exit contract consumed by Task 3's shell handling — `0` all ok, `2` findings recorded (fail-closed observations appended, valid verdict), `1` crash/emit failure (node failure).

- [ ] **Step 1: Write the failing test**

```python
"""Exit-code contract for expectation_reconcile: 0 ok / 2 findings / 1 crash.

The daily shell (podcast_daily.sh) treats 2 as a valid alarm verdict whose
observations the compare/dedup chain must process, exactly like
dag_supervisor. Any other nonzero is a node failure that stops the chain.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "runtime" / "expectation_reconcile.py"
REPO = Path(__file__).parents[3]


def _invoke(state_dir, sources, manifests):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--shadow",
         "--state-dir", str(state_dir),
         "--sources", str(sources),
         "--manifests", str(manifests)],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
    )


def test_no_manifests_is_findings_verdict_exit_2(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    sources = tmp_path / "sources"; sources.mkdir()
    manifests = tmp_path / "manifests"; manifests.mkdir()  # empty: fail closed
    result = _invoke(state, sources, manifests)
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload["status"] == "fail"
    rows = [json.loads(line) for line in
            (state / "observations.jsonl").read_text().splitlines()]
    assert rows[-1]["sensor"] == "expectation"
    assert rows[-1]["status"] == "unknown"


def test_crash_is_node_failure_exit_1(tmp_path):
    # state-dir path occupied by a FILE: _append raises OSError -> crash lane.
    state = tmp_path / "state"; state.write_text("not a directory")
    sources = tmp_path / "sources"; sources.mkdir()
    manifests = tmp_path / "manifests"; manifests.mkdir()
    result = _invoke(state, sources, manifests)
    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload["errors"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest departments/podcast/tests/test_expectation_exitcodes.py -v`
Expected: `test_no_manifests_is_findings_verdict_exit_2` FAILS (returncode is 1, not 2). `test_crash_is_node_failure_exit_1` may already pass; that is fine — the split is what is new.

- [ ] **Step 3: Implement the split**

In `departments/podcast/runtime/expectation_reconcile.py`, replace the final return of `main()`:

```python
    # OLD:
    # return 0 if worst == "ok" and not errors else 1
    # NEW — exit contract consumed by podcast_daily.sh:
    #   0 = all expectations met; 2 = findings recorded (valid verdict, the
    #   compare/dedup chain must process them); 1 = crash (node failure).
    if errors:
        return 1
    return 0 if worst == "ok" else 2
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest departments/podcast/tests/test_expectation_exitcodes.py -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add departments/podcast/runtime/expectation_reconcile.py departments/podcast/tests/test_expectation_exitcodes.py
git commit -m "fix(podcast): expectation_reconcile exit contract — 0 ok / 2 findings / 1 crash"
```

---

### Task 2: compare_charter learns the expectation sensor

**Files:**
- Modify: `departments/podcast/runtime/compare_charter.py:27` (FAILURE_CLASSES), `:72` (MEANINGS), `:195` (QUESTIONS)
- Test: `departments/podcast/tests/test_compare_charter.py` (extend — never delete existing tests)

**Interfaces:**
- Consumes: observation rows `{"sensor": "expectation", "status": "unknown"|"alarm", ...}` as written by `expectation_reconcile._observation`.
- Produces: candidates with `failure_class` `"expectation_blind"` (status unknown, severity high) and `"expectation_delta"` (status alarm, severity high) for the fingerprint/escalate chain.

- [ ] **Step 1: Write the failing test** (append to `test_compare_charter.py`, matching its existing fixture style for charter/observations — reuse the module's existing helper for building a charter if one exists, otherwise the minimal dict the other tests use)

```python
def test_expectation_unknown_maps_to_expectation_blind(minimal_charter):
    rows = [{
        "ts": "2026-08-05T12:00:00+00:00", "sensor": "expectation",
        "subject": "expectation-none", "status": "unknown",
        "evidence": "/tmp/manifests",
        "detail": "no expectation manifests found (fail closed)", "metrics": {},
    }]
    candidates = compare_charter.compare_observations(rows, minimal_charter)
    assert [c["failure_class"] for c in candidates] == ["expectation_blind"]
    assert candidates[0]["severity"] == "high"
    assert candidates[0]["what_it_means"]


def test_expectation_alarm_maps_to_expectation_delta(minimal_charter):
    rows = [{
        "ts": "2026-08-05T12:00:00+00:00", "sensor": "expectation",
        "subject": "expectation-daily", "status": "alarm",
        "evidence": "/tmp/receipt.json",
        "detail": "2 expectation delta(s)",
        "metrics": {"counts": {"ok": 1}, "deltas": []},
    }]
    candidates = compare_charter.compare_observations(rows, minimal_charter)
    assert [c["failure_class"] for c in candidates] == ["expectation_delta"]
    assert candidates[0]["severity"] == "high"
```

(If `test_compare_charter.py` has no `minimal_charter` fixture, build the charter argument exactly the way its existing tests do — copy their construction verbatim.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest departments/podcast/tests/test_compare_charter.py -v -k expectation`
Expected: FAIL with `ValueError: no charter comparison transition for sensor='expectation', status='unknown'`

- [ ] **Step 3: Add the transitions**

In `FAILURE_CLASSES` (after the `("manifest", "unknown")` entry):

```python
    # expectation_reconcile fails closed: no manifests, unreadable snapshot,
    # or reconcile error is blindness; recorded deltas are contract gaps.
    # Added in P0 when the || true bypass was removed — before this entry the
    # generic comparison raised on any expectation observation.
    ("expectation", "unknown"): ("expectation_blind", "high"),
    ("expectation", "alarm"): ("expectation_delta", "high"),
```

In `MEANINGS`:

```python
    "expectation_blind": {
        "what_it_means": "The checker that compares declared step expectations against reality has nothing to check — its manifests or its ground-truth snapshot are missing or unreadable.",
        "what_it_needs": "Ops must restore the expectation manifests or the snapshot feed; nothing needed from you unless the expectation contract itself should change.",
    },
    "expectation_delta": {
        "what_it_means": "A step that declared an expected artifact has not produced it in time — work the process promised is missing.",
        "what_it_needs": "Ops must run the declared heal for each delta or repair the producing step; you only decide if a delta was intentional.",
    },
```

In `QUESTIONS`:

```python
    "expectation": "Which declared expectation has no matching artifact, and does its heal run or does the manifest need correcting?",
```

- [ ] **Step 4: Run the full compare test file**

Run: `python3 -m pytest departments/podcast/tests/test_compare_charter.py -v`
Expected: all PASS (old and new).

- [ ] **Step 5: Commit**

```bash
git add departments/podcast/runtime/compare_charter.py departments/podcast/tests/test_compare_charter.py
git commit -m "feat(podcast): compare_charter maps expectation observations — blind/delta, both high"
```

---

### Task 3: podcast_daily.sh — replace `|| true` with the alarm-verdict pattern

**Files:**
- Modify: `departments/podcast/runtime/podcast_daily.sh:127`
- Test: `departments/podcast/tests/test_daily_failclosed.py` (extend)

**Interfaces:**
- Consumes: Task 1's exit contract (0/2/1).

- [ ] **Step 1: Write the failing regression test** (append to `test_daily_failclosed.py`)

```python
def test_expectation_line_has_no_silent_bypass():
    text = SCRIPT.read_text(encoding="utf-8")
    expectation_lines = [l for l in text.splitlines() if "expectation_reconcile.py" in l]
    assert expectation_lines, "expectation_reconcile invocation missing from daily chain"
    assert not any("|| true" in l for l in expectation_lines), (
        "expectation_reconcile must not be silenced with || true; "
        "exit 2 is a findings verdict handled like dag_supervisor's alarm")
    assert "exp_rc" in text, "expected the rc-capture alarm-verdict pattern"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest departments/podcast/tests/test_daily_failclosed.py -v -k bypass`
Expected: FAIL on the `|| true` assertion.

- [ ] **Step 3: Edit the shell**

Replace line 127 (`... expectation_reconcile.py --shadow --sources "${SOURCES}" || true`) with:

```
# Expectation reconcile is receipt-gated like the DAG supervisor: exit 2 is a
# VALID findings verdict (observations recorded; compare/dedup below must
# process them). Any other nonzero exit is a node failure and stops the chain.
exp_rc=0
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/expectation_reconcile.py" --shadow --sources "${SOURCES}" || exp_rc=$?
if [ "${exp_rc}" -ne 0 ] && [ "${exp_rc}" -ne 2 ]; then
    echo "expectation_reconcile failed with rc=${exp_rc} (not a findings verdict)" >&2
    exit "${exp_rc}"
fi
```

- [ ] **Step 4: Verify — tests plus a bash syntax check**

Run: `bash -n departments/podcast/runtime/podcast_daily.sh && python3 -m pytest departments/podcast/tests/test_daily_failclosed.py -v`
Expected: `bash -n` silent, all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add departments/podcast/runtime/podcast_daily.sh departments/podcast/tests/test_daily_failclosed.py
git commit -m "fix(podcast): remove || true — expectation findings now flow to compare, crashes stop the chain"
```

---

### Task 4: manager sense() — missing budget telemetry is a first-class fact

**Files:**
- Modify: `factory/manager.py:189-213` (the budget block in `sense()`)
- Test: `tests/test_manager_budget_failclosed.py` (create)

**Interfaces:**
- Produces: sensed keys `budget_telemetry_missing: bool` (path given, file absent) and `budget_telemetry_unconfigured: bool` (no path given). Consumed by Task 5's compare and asserted by later phases.

- [ ] **Step 1: Write the failing test**

```python
"""Budget telemetry fail-closed: missing must never read as zero spend.

Red-team operator catch (loop-brain-reconcile): the 900-call ceiling loaded
with no usage feed and missing data was treated as an empty usage map.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from factory.manager import sense, compare, DEFAULT_THRESHOLDS  # noqa: E402


def test_missing_budget_file_is_flagged(tmp_path):
    sensed = sense(tmp_path, budget_path=tmp_path / "nope.json")
    assert sensed["budget_telemetry_missing"] is True
    assert sensed["budget_telemetry_unconfigured"] is False
    assert sensed["budget_used"] == {}


def test_no_budget_path_is_unconfigured_not_missing(tmp_path):
    sensed = sense(tmp_path)
    assert sensed["budget_telemetry_missing"] is False
    assert sensed["budget_telemetry_unconfigured"] is True


def test_present_budget_file_loads(tmp_path):
    p = tmp_path / "budget_used.json"
    p.write_text(json.dumps({"model_calls": 12}), encoding="utf-8")
    sensed = sense(tmp_path, budget_path=p)
    assert sensed["budget_used"] == {"model_calls": 12}
    assert sensed["budget_telemetry_missing"] is False
    assert sensed["budget_telemetry_unconfigured"] is False


def test_corrupt_budget_file_still_unreadable(tmp_path):
    p = tmp_path / "budget_used.json"
    p.write_text("{not json", encoding="utf-8")
    sensed = sense(tmp_path, budget_path=p)
    assert sensed["budget_unreadable"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_manager_budget_failclosed.py -v`
Expected: first two FAIL with `KeyError: 'budget_telemetry_missing'`; the other two PASS (existing behavior).

- [ ] **Step 3: Implement in `sense()`** — replace the budget block:

```python
    budget_used: dict[str, Any] = {}
    budget_unreadable = False
    # Red-team operator catch: a configured ceiling with no usage feed must
    # never read as healthy zero spend. Distinguish the three honest states:
    # unconfigured (no path wired), missing (path wired, file absent), and
    # unreadable (file present, corrupt).
    budget_unconfigured = budget_path is None
    budget_missing = False
    if budget_path and Path(budget_path).exists():
        try:
            budget_used = json.loads(Path(budget_path).read_text(encoding="utf-8"))
        except (ValueError, OSError):
            # Codex review #13: missing/corrupt cost telemetry must surface as a
            # breach, never silently read as a healthy zero-spend baseline.
            budget_used = {}
            budget_unreadable = True
    elif budget_path:
        budget_missing = True
```

And add to the returned snapshot dict, next to the existing budget keys:

```python
        "budget_telemetry_missing": budget_missing,
        "budget_telemetry_unconfigured": budget_unconfigured,
```

- [ ] **Step 4: Verify**

Run: `python3 -m pytest tests/test_manager_budget_failclosed.py tests/test_manager_hardening.py -v`
Expected: all PASS (new file green, no regression in hardening).

- [ ] **Step 5: Commit**

```bash
git add factory/manager.py tests/test_manager_budget_failclosed.py
git commit -m "feat(manager): sense distinguishes budget telemetry missing vs unconfigured vs unreadable"
```

---

### Task 5: manager compare() — missing telemetry breaches, unconfigured warns

**Files:**
- Modify: `factory/manager.py:454-458` (after the `budget_unreadable` finding)
- Test: `tests/test_manager_budget_failclosed.py` (extend)

**Interfaces:**
- Consumes: Task 4's sensed keys and `t["budget_ceilings"]`.
- Produces: findings `budget_telemetry_missing` (severity `breach`) and `budget_telemetry_unconfigured` (severity `warn`); decide() already escalates every breach.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_missing_telemetry_with_ceilings_is_breach(tmp_path):
    sensed = sense(tmp_path, budget_path=tmp_path / "nope.json")
    codes = {f["code"]: f["severity"] for f in compare(sensed, DEFAULT_THRESHOLDS)}
    assert codes.get("budget_telemetry_missing") == "breach"


def test_unconfigured_telemetry_with_ceilings_is_warn(tmp_path):
    sensed = sense(tmp_path)
    codes = {f["code"]: f["severity"] for f in compare(sensed, DEFAULT_THRESHOLDS)}
    assert codes.get("budget_telemetry_unconfigured") == "warn"


def test_no_ceilings_no_budget_findings(tmp_path):
    t = dict(DEFAULT_THRESHOLDS)
    t["budget_ceilings"] = {}
    sensed = sense(tmp_path, budget_path=tmp_path / "nope.json")
    codes = [f["code"] for f in compare(sensed, t)]
    assert "budget_telemetry_missing" not in codes
    assert "budget_telemetry_unconfigured" not in codes
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_manager_budget_failclosed.py -v -k telemetry`
Expected: first two FAIL (finding absent).

- [ ] **Step 3: Implement in `compare()`** — directly after the `budget_unreadable` finding block:

```python
    # breach: a ceiling exists but its usage feed is wired and absent —
    # spend is unverifiable, which is a guard failure, not a zero.
    if t["budget_ceilings"] and sensed.get("budget_telemetry_missing"):
        findings.append(
            _finding("budget_telemetry_missing", "breach",
                     "budget ceilings are set but the usage telemetry file is "
                     "absent — spend is unverifiable (wire the producer or fix "
                     "the path)",
                     observed=None, setpoint=None)
        )
    # warn: ceilings exist and no telemetry path is wired at all — visible
    # pressure without an estate-wide alarm storm for departments that have
    # not adopted the budget feed yet.
    if t["budget_ceilings"] and sensed.get("budget_telemetry_unconfigured"):
        findings.append(
            _finding("budget_telemetry_unconfigured", "warn",
                     "budget ceilings are set but no usage telemetry path is "
                     "configured — pass --budget to the manager invocation",
                     observed=None, setpoint=None)
        )
```

- [ ] **Step 4: Verify**

Run: `python3 -m pytest tests/test_manager_budget_failclosed.py tests/test_manager_hardening.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add factory/manager.py tests/test_manager_budget_failclosed.py
git commit -m "feat(manager): missing budget telemetry breaches; unconfigured warns — never zero"
```

---

### Task 6: CLI `--budget` + podcast/template wiring

**Files:**
- Modify: `factory/manager.py:978` (argparse block in `main()`), `factory/manager.py:1022` (`run_manager_cycle` call)
- Modify: `departments/podcast/runtime/podcast_daily.sh:152`
- Modify: `templates/department_daily.sh.template:25`
- Test: `tests/test_manager_budget_failclosed.py` (extend)

**Interfaces:**
- Consumes: `run_manager_cycle(..., **telemetry_paths)` forwards unknown kwargs to `sense()` — `budget_path` rides that path; no signature change needed.

- [ ] **Step 1: Write the failing test** (append)

```python
def test_run_manager_cycle_forwards_budget_path(tmp_path):
    from factory.manager import run_manager_cycle
    report = run_manager_cycle(tmp_path, budget_path=tmp_path / "nope.json")
    codes = [f["code"] for f in report["findings"]]
    assert "budget_telemetry_missing" in codes
```

- [ ] **Step 2: Run to verify it fails or passes**

Run: `python3 -m pytest tests/test_manager_budget_failclosed.py -v -k forwards`
Expected: PASS already if `**telemetry_paths` forwarding works (it should — Task 4/5 did the semantics). If it FAILS, the forwarding assumption broke: stop and read `run_manager_cycle` before continuing.

- [ ] **Step 3: Add the CLI argument** — in `main()` after the `--outbox` argument:

```python
    parser.add_argument("--budget", default=None,
                        help="usage telemetry JSON ({kind: used}) compared "
                             "against the charter's weekly ceilings; a wired "
                             "path whose file is absent is a breach")
```

And extend the `run_manager_cycle` call:

```python
    report = run_manager_cycle(
        state_dir, autonomy_state=autonomy, thresholds=thresholds,
        escalate_fn=escalate_fn, department=args.department,
        dept_dir=root / "departments" / args.department,
        budget_path=args.budget,
    )
```

- [ ] **Step 4: Wire the invocations**

`departments/podcast/runtime/podcast_daily.sh:152` becomes:

```
python3 "${REPO}/factory/manager.py" --department "${DEPARTMENT}" --root "${REPO}" --outbox "${OUTBOX}" --budget "${STATE_DIR}/budget_used.json"
```

`templates/department_daily.sh.template:25` becomes:

```
python3 "${REPO}/factory/manager.py" --department "${DEPARTMENT}" --root "${REPO}" --outbox "${OUTBOX}" --budget "${STATE_DIR}/budget_used.json"
```

- [ ] **Step 5: Verify end to end, without touching real state**

Run: `bash -n departments/podcast/runtime/podcast_daily.sh && python3 -m pytest tests/test_manager_budget_failclosed.py -v`
Then a scratch-department CLI proof (no outbox → no card side effect):

Run: `mkdir -p /tmp/p0check/departments/scratch/state && python3 factory/manager.py --department scratch --root /tmp/p0check --budget /tmp/p0check/absent.json | python3 -c "import json,sys; f=json.load(sys.stdin)['findings']; assert 'budget_telemetry_missing' in f, f; print('breach surfaced:', f)"`
Expected: `breach surfaced: [...]` including `budget_telemetry_missing`.

- [ ] **Step 6: Commit**

```bash
git add factory/manager.py departments/podcast/runtime/podcast_daily.sh templates/department_daily.sh.template tests/test_manager_budget_failclosed.py
git commit -m "feat(manager+podcast): wire --budget telemetry path; absent file now breaches"
```

---

### Task 7: regression pin — runs-v2 emit failures already propagate

**Files:**
- Test: `departments/podcast/tests/test_records_integrity.py` (extend; no implementation change expected)

**Interfaces:**
- Consumes: `manifest_sensor.run(state_dir, sources)` and `factory.runrecord.emit_record`.

The receipts audit claimed sensors swallow emit failures; re-reading the code shows `_emit_run_record` re-raises and the success-path call is outside any try. This task pins that as an executable contract so it cannot regress silently.

- [ ] **Step 1: Write the test** (append to `test_records_integrity.py`, using its existing imports/style)

```python
def test_manifest_sensor_emit_failure_propagates(tmp_path, monkeypatch):
    """A node whose runs-v2 append fails must FAIL the node (runbook rule:
    a step without its receipt cannot advance). Pinned during P0."""
    from departments.podcast.runtime import manifest_sensor
    import pytest

    monkeypatch.setattr(manifest_sensor, "_run", lambda *a, **k: [])

    def boom(*args, **kwargs):
        raise OSError("simulated runs-v2 append failure")
    monkeypatch.setattr(manifest_sensor.runrecord, "emit_record", boom)

    with pytest.raises(OSError, match="simulated runs-v2 append failure"):
        manifest_sensor.run(tmp_path, tmp_path)
```

(If `departments.podcast.runtime` is not importable as a package in this test file's existing style, import it the way the neighboring tests in the same file do — copy their import mechanism verbatim.)

- [ ] **Step 2: Run it — expect immediate PASS**

Run: `python3 -m pytest departments/podcast/tests/test_records_integrity.py -v -k propagates`
Expected: PASS on first run. If it FAILS, the audit was right after all and the fix is to make the relevant `_emit_run_record` path re-raise — stop and investigate before changing anything.

- [ ] **Step 3: Commit**

```bash
git add departments/podcast/tests/test_records_integrity.py
git commit -m "test(podcast): pin emit-failure propagation — a node without its record is a failed node"
```

---

### Task 8: full verification + process-change QA + re-pin

**Files:**
- No new code. Runs `runbooks/process-change-qa.md` for the podcast department (its runtime files changed in Tasks 1-3, 6).

- [ ] **Step 1: Full factory check**

Run: `python3 loopfactory.py check`
Expected: compileall clean, full pytest PASS. Report the actual counts.

- [ ] **Step 2: Department validation**

Run: `python3 loopfactory.py validate --name podcast`
Expected: PASS.

- [ ] **Step 3: Process-change QA per the runbook**

Follow `runbooks/process-change-qa.md` end to end for the podcast department: update the procedural map's expectation-node description (exit contract 0/2/1 and the daily chain's alarm-verdict handling; `departments/podcast/procedural-graph.md`), re-lint, re-shadow (one shadow run of the daily chain against fixtures or with sinks simulated — `delivered_count==0`), then re-pin:

Run: `python3 loopfactory.py release pin --name podcast --source-ref $(git rev-parse HEAD) --flip`
Expected: new release hash printed; `departments/podcast/releases/current` points at it; drift alarm clears on the next manager tick.

- [ ] **Step 4: Confirm the expected new standing card**

The next real daily run will raise `budget_telemetry_missing` (breach) — this is the designed honest state until the P1 producer lands. Confirm exactly one card appears (fingerprint dedup), then leave it standing.

- [ ] **Step 5: Commit the map + release artifacts**

```bash
git add departments/podcast/procedural-graph.md departments/podcast/releases/
git commit -m "chore(podcast): process-change QA + re-pin for P0 silent-skip closure"
```

---

## Self-Review Notes

- Spec coverage: P0 spec line items are (a) remove `|| true` → Tasks 1-3; (b) fatal emits → investigated, already fatal, pinned by Task 7; (c) wire budget telemetry → Tasks 4-6. Release-tree discipline → Task 8.
- The budget telemetry PRODUCER is deliberately out of P0 (spec places custody in P1's kernel work); the standing breach card is the honest interim state and is called out in Global Constraints.
- Type consistency: `budget_path` name is identical across sense kwargs, run_manager_cycle kwargs, and CLI dest (`args.budget` → `budget_path=args.budget`).
