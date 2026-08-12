# P1: Run-Manifest Custody + Verifier (advisory) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every podcast daily run declares an immutable plan (run manifest) before its first node, every node's produced record binds to that run, and a deterministic verifier diffs plan vs produced and surfaces red verdicts as manager findings — advisory this phase, blocking at the owner-approved flip.

**Architecture:** Phase P1 of `docs/superpowers/specs/2026-08-05-loop-brain-reconcile-design.md` (C2). New `kernel/run_manifest.py` owns mint + verify. Roster is release-bound: it lives inside the pinned release tree and mint refuses on hash mismatch (omission-forgery closed). Run identity propagates by env (`LOOP_FACTORY_RUN_ID`) so sensors need zero changes. Verdicts append a `runmanifest` observation the existing compare/escalate chain carries; the manager senses the newest verdict like it senses drift.

**Honest production caveat (this phase):** the daily chain holds no credentials by design (its systemd unit bans credential env lines), so production manifests/verdicts carry `"signature": null`. Signature code paths ship fully implemented and tested via injected signers; the independently keyed verifier unit that closes verdict forgery is the P2 hardening item (spec: reconciler monoculture). Advisory mode = red verdicts are warn findings + observations; nothing new blocks yet. The blocking flip is a deliberate owner action at P1 exit.

**Tech Stack:** Python 3 stdlib, bash, pytest. Reuses `kernel/receipts.py` (LocalSigner, issue_receipt/verify_receipt), `factory/runrecord.py` v2 records.

## Global Constraints

- Shadow-only; no external effects; kernel gateways untouched.
- Deny-by-default: mint REFUSES (rc 1, chain aborts) on roster drift, unreadable release, or existing manifest for the same run id. Verifier crash = rc 1 (node failure); red/unknown verdict = rc 2 (advisory continue, observation recorded).
- No secrets in repo, tests, or output. `OE_KERNEL_SIGNING_KEY` values never appear anywhere; tests inject literal dummy keys like `"test-key"`.
- Records always; every new file the chain writes lives under `departments/podcast/state/`.
- Watch every new test fail RED before implementing.
- Release-tree files change (`podcast_daily.sh`, `compare_charter.py`, new `run-roster.json`): Task 8-equivalent (integration) MUST re-run process-change QA + re-pin.

## Frozen interfaces (all lanes build against these exactly)

CLI (module runnable via `PYTHONPATH=. python3 -m kernel.run_manifest ...`):

- `mint --department podcast --dept-dir <path> --state-dir <path> --trigger daily`
  → stdout JSON `{"run_id": "...", "manifest": "<path>", "signed": false}`; rc 0 minted; rc 1 refused (roster missing/drifted vs pinned release, release unreadable, manifest already exists).
- `verify --dept-dir <path> --state-dir <path> --run-id <id>`
  → writes `<state>/run-manifests/<run_id>.verdict.json`, appends one observation row to `<state>/observations.jsonl`, stdout JSON verdict summary; rc 0 green; rc 2 red or unknown (advisory); rc 1 crash.

Manifest JSON (`<state>/run-manifests/<run_id>.json`, create-no-replace via `os.open(..., O_CREAT|O_EXCL)`):

```json
{"schema": "run-manifest", "rev": 1, "run_id": "...", "department": "podcast",
 "created_at": "<iso8601 utc>", "trigger": "daily",
 "release": {"hash": "<current release hash>"},
 "roster_hash": "<sha256 of roster file bytes>",
 "roster": [{"ordinal": 1, "node": "sense_estate", "required": true}],
 "nonce": "<hex>", "action_class": "run_manifest", "signature": null}
```

Verdict JSON (`<state>/run-manifests/<run_id>.verdict.json`):

```json
{"schema": "run-verdict", "rev": 1, "run_id": "...",
 "status": "green|red|unknown", "missing": [], "unexpected": [],
 "duplicates": [], "reordered": [], "reason": "...",
 "checked_at": "<iso8601 utc>", "signature": null}
```

Observation row appended by `verify`:

```json
{"ts": "...", "sensor": "runmanifest", "subject": "runmanifest-<run_id>",
 "status": "alarm|unknown", "evidence": "<verdict path>",
 "detail": "...", "metrics": {"missing": 0, "unexpected": 0, "duplicates": 0, "reordered": 0}}
```

Roster file `departments/podcast/runtime/run-roster.json`:

```json
{"schema": "run-roster", "rev": 1, "department": "podcast",
 "nodes": [{"ordinal": 1, "node": "sense_estate", "required": true}]}
```

`factory/runrecord.py`: `emit_record(..., run_id: str | None = None)` → identity used is `run_id or os.environ.get("LOOP_FACTORY_RUN_ID") or new_run_id()`.

`kernel/capabilities.py`: `LOOP_FACTORY_RUN_ID` joins `_ALLOWED_ENV` (benign run identity, not a credential).

`factory/manager.py`: `sense_manifest_verdict(state_dir) -> dict` returns `{"manifest_verdict_status": "green|red|unknown|absent|none", "manifest_verdict_counts": {...}}` — `none` when `<state>/run-manifests/` does not exist (department not adopted; no findings), `absent` when manifests exist but the newest has no verdict. Merged into sensed in `run_manager_cycle` exactly like `sense_graph_escalations`. compare(): `runmanifest_red` warn when status red; `runmanifest_unverified` warn when status unknown or absent. (Advisory: both warn. The blocking flip upgrades red to breach later — do NOT implement the flip now.)

---

### Task 1: `kernel/run_manifest.py` — mint, verify, CLI, adversarial fixtures

**Files:**
- Create: `kernel/run_manifest.py`
- Test: `tests/test_run_manifest.py` (create)

**Interfaces:**
- Consumes: `factory/runrecord.py` `new_run_id()` and v2 rows in `<state>/runs-v2.jsonl`; `departments/<d>/releases/current` (text: release hash) and `releases/<hash>/manifest.json` (JSON whose `artifacts` list carries `{"path": ..., "sha256": ...}` entries — READ the real file `departments/podcast/releases/e0a9510823016be4/manifest.json` first and match its actual key names; if the artifact list uses different key names, adapt the lookup, never the contract).
- Produces: the frozen CLI + manifest/verdict/observation shapes above, plus library functions `mint(...) -> dict` and `verify(...) -> dict` used by tests.

Behavior contract:

- `mint`: read `releases/current` → release hash; load the release manifest; locate the entry for `departments/<dept>/runtime/run-roster.json`; sha256 the LIVE roster file; mismatch or missing entry → print reason to stderr, rc 1 (roster drift is forgery-by-omission — refuse). Load roster JSON (validate schema/rev/nodes shape; invalid → rc 1). Build manifest with `run_id=new_run_id()`, `nonce=secrets.token_hex(16)`. If an `OE_KERNEL_SIGNING_KEY` env var is present, sign: `signature = LocalSigner().sign(canonical_json_without_signature_field)` (import LocalSigner from `kernel/receipts.py` using the same `importlib` file-location pattern `kernel/step_receipts.py` uses — kernel modules are loaded by path in this repo). Else `signature: null`. Write with `O_CREAT|O_EXCL` (existing file → rc 1).
- `verify`: load manifest (missing/unparseable → verdict `unknown`, reason `manifest_missing`, rc 2). If manifest has a signature and the key env is present, verify it; bad signature → verdict `red`, reason `signature_invalid`. If signature is null → status can be at best `unknown`? NO — in advisory phase an unsigned manifest still yields a REAL diff: compute the diff and set status `green`/`red` from the diff, but put `"unsigned"` in `reason` when signature is null so the observation detail carries it. Diff: read `<state>/runs-v2.jsonl`, keep rows whose `run_id` equals the manifest's; `missing` = required roster nodes with no row; `unexpected` = rows whose node is not in the roster; `duplicates` = nodes with >1 row whose `status == "ok"`; `reordered` = required nodes whose first-row timestamp order disagrees with roster ordinal order (compare the sequence of observed nodes filtered to roster members against the roster's ordinal sort). status = red if any of the four lists is non-empty else green. Write verdict (plain write, overwrite allowed — verdicts are re-computable), append the observation row (status `alarm` for red, `unknown` for unknown), print summary, rc per contract.
- Pure stdlib. No imports from `factory/` except by file path (`importlib.util.spec_from_file_location`) for `runrecord` — kernel must not package-import factory; read runs-v2 rows with plain `json.loads` per line instead if simpler (preferred: no factory import at all; `new_run_id` can be replicated as `uuid4().hex` ONLY IF runrecord's `new_run_id` is uuid-based — READ `factory/runrecord.py:69` first and copy its exact mechanism so run ids stay format-compatible).

- [ ] **Step 1: Write the failing tests** — `tests/test_run_manifest.py` with a fixture builder that creates a fake dept tree: `releases/current` → hash `abc123`, `releases/abc123/manifest.json` listing the roster file with its real sha256, `runtime/run-roster.json` with 3 nodes (2 required, 1 optional), empty `state/`. Tests (all through the library functions; one CLI smoke test via subprocess):

```python
def test_mint_writes_manifest_and_refuses_second_mint(dept_tree): ...
    # mint ok -> file exists, schema/rev/run_id/roster_hash correct, signed False
    # second mint SAME run id impossible by construction (new_run_id differs), so
    # instead: call the internal write with an existing path -> rc/raise refusal

def test_mint_refuses_roster_drift(dept_tree): ...
    # append a byte to run-roster.json after the release manifest recorded its
    # hash -> mint returns refusal (drift), no manifest file written

def test_mint_refuses_missing_release_entry(dept_tree): ...

def test_verify_green_when_all_required_nodes_ran(dept_tree): ...
    # write runs-v2 rows for both required nodes with the manifest run_id -> green, rc 0 path

def test_verify_red_missing_node(dept_tree): ...
    # only one required node ran -> red, missing lists the other, observation
    # row appended with sensor runmanifest status alarm

def test_verify_red_unexpected_and_duplicate_and_reordered(dept_tree): ...
    # extra node row -> unexpected; two ok rows same node -> duplicates;
    # required nodes present but in reversed ts order -> reordered

def test_verify_unknown_when_manifest_missing(dept_tree): ...

def test_signature_roundtrip_and_tamper(dept_tree, monkeypatch): ...
    # monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "test-key"): mint -> signed
    # True; verify ok. Tamper one roster ordinal inside the written manifest ->
    # verify -> red, reason signature_invalid

def test_wrong_run_id_rows_are_ignored(dept_tree): ...
    # rows with a different run_id must not satisfy the roster
```

- [ ] **Step 2: Run to verify RED** — `python3 -m pytest tests/test_run_manifest.py -v` → every test fails with import error (module absent).
- [ ] **Step 3: Implement `kernel/run_manifest.py`** per the behavior contract. Keep it one file, ~250 lines, stdlib only, `main()` with argparse subcommands `mint`/`verify`, module docstring citing the spec.
- [ ] **Step 4: Run to verify GREEN** — same command, all pass.
- [ ] **Step 5: Commit** — `git add kernel/run_manifest.py tests/test_run_manifest.py && git commit -m "feat(kernel): run-manifest mint + verify — release-bound roster, deterministic diff, advisory verdicts"`

---

### Task 2: run-id binding — `emit_record` env fallback + capabilities allowlist

**Files:**
- Modify: `factory/runrecord.py:281-341` (`emit_record`)
- Modify: `kernel/capabilities.py:24-33` (`_ALLOWED_ENV`)
- Test: `tests/test_runrecord_runid.py` (create)

**Interfaces:**
- Produces: `emit_record(..., run_id=None)`; env var name `LOOP_FACTORY_RUN_ID` (exact string, all lanes).

- [ ] **Step 1: Failing tests**

```python
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from factory import runrecord


def _emit(tmp_path, **kw):
    runrecord.emit_record(tmp_path, department="d", node="n", status="ok", **kw)
    rows = [json.loads(l) for l in (tmp_path / "runs-v2.jsonl").read_text().splitlines()]
    return rows[-1]


def test_explicit_run_id_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_FACTORY_RUN_ID", "env-id-123")
    assert _emit(tmp_path, run_id="explicit-1")["run_id"] == "explicit-1"


def test_env_run_id_used_when_not_passed(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_FACTORY_RUN_ID", "env-id-123")
    assert _emit(tmp_path)["run_id"] == "env-id-123"


def test_fresh_id_when_neither(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOP_FACTORY_RUN_ID", raising=False)
    assert _emit(tmp_path)["run_id"]


def test_capabilities_allowlist_passes_run_id(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "caps", Path(__file__).parents[1] / "kernel" / "capabilities.py")
    caps = importlib.util.module_from_spec(spec); spec.loader.exec_module(caps)
    env = caps.department_env({"LOOP_FACTORY_RUN_ID": "r1", "AWS_SECRET_ACCESS_KEY": "nope"})
    assert env.get("LOOP_FACTORY_RUN_ID") == "r1"
    assert "AWS_SECRET_ACCESS_KEY" not in env
```

(READ `kernel/capabilities.py` `department_env`'s real signature first; if it requires a `capabilities=` argument, pass the minimal value its other tests use — copy their call shape.)

- [ ] **Step 2: RED** — first two and last tests fail.
- [ ] **Step 3: Implement** — `emit_record` signature gains `run_id: str | None = None`; the `build_record` call uses `run_id=run_id or os.environ.get("LOOP_FACTORY_RUN_ID") or new_run_id()` (add `import os` if absent, keep the docstring note that the env var is how the daily chain binds all node records to one minted run). `_ALLOWED_ENV` gains `"LOOP_FACTORY_RUN_ID"` with a comment: `# run identity minted by kernel.run_manifest — benign, never a credential`.
- [ ] **Step 4: GREEN** — new file + `tests/test_manager_hardening.py` + existing runrecord tests all pass: `python3 -m pytest tests/ -q`.
- [ ] **Step 5: Commit** — `git commit -m "feat(records): bind node records to the minted run via LOOP_FACTORY_RUN_ID"`

---

### Task 3: roster + daily-chain wiring + compare transitions

**Files:**
- Create: `departments/podcast/runtime/run-roster.json`
- Modify: `departments/podcast/runtime/podcast_daily.sh` (top of section 1, and before the manager line)
- Modify: `departments/podcast/runtime/compare_charter.py` (FAILURE_CLASSES, MEANINGS, QUESTIONS)
- Test: `departments/podcast/tests/test_compare_charter.py` (extend), `departments/podcast/tests/test_daily_failclosed.py` (extend), `departments/podcast/tests/test_run_roster.py` (create)

**Interfaces:**
- Consumes: Task 1's CLI exactly as frozen; Task 2's env var name.
- Produces: roster content other lanes' fixtures may mirror; compare classes `runmanifest_missing_steps` / `runmanifest_unverified`.

- [ ] **Step 1: Author the roster from evidence, with an executable consistency test.** For every `*.py` invoked by `podcast_daily.sh` between the mint point and the manager line, mark `required: true` iff the script (or a module it imports from the same runtime dir) calls `runrecord.emit_record` or `runrecord.timed_emit` on its success path; else `required: false`. Write `test_run_roster.py`:

```python
def test_every_required_roster_node_is_invoked_by_the_daily_chain():
    # parse podcast_daily.sh; every roster node name must appear as
    # runtime/<node>.py in the script; every required node's source must
    # contain "emit_record" or "timed_emit"
```

plus the inverse guard: every `runtime/*.py` invoked by the shell between mint and manager appears in the roster (any ordinal). This makes roster fantasy and roster rot both executable failures.

- [ ] **Step 2: RED** — roster file absent → tests fail.
- [ ] **Step 3: Write the roster**, ordinals following the shell's invocation order: sense_estate, pipeline_sensor, publish_verifier, manifest_sensor, hopper_sensor, funnel_floor_sensor, expectation_reconcile, comms_reconcile_sensor, dag_supervisor, compare_charter, fingerprint_dedup, escalate_outbox — required flags per the Step 1 evidence rule.
- [ ] **Step 4: Wire the shell.** After the `mkdir -p` line and before section 1's first sensor:

```
# P1: mint the run manifest BEFORE the first node (spec C2). Mint refusal is a
# hard stop: a run that cannot declare its plan does not run (deny-by-default).
mint_out="$(PYTHONPATH="${REPO}" python3 -m kernel.run_manifest mint --department "${DEPARTMENT}" --dept-dir "${REPO}/departments/${DEPARTMENT}" --state-dir "${STATE_DIR}" --trigger daily)"
LOOP_FACTORY_RUN_ID="$(json_object_field run_id <<<"${mint_out}")"
export LOOP_FACTORY_RUN_ID
```

And after `escalate_outbox` / before the manager line (verifier runs once the sensor chain has drained; exit 2 = advisory red/unknown, continue; other nonzero = crash, abort — same pattern as dag_supervisor):

```
ver_rc=0
PYTHONPATH="${REPO}" python3 -m kernel.run_manifest verify --dept-dir "${REPO}/departments/${DEPARTMENT}" --state-dir "${STATE_DIR}" --run-id "${LOOP_FACTORY_RUN_ID}" || ver_rc=$?
if [ "${ver_rc}" -ne 0 ] && [ "${ver_rc}" -ne 2 ]; then
    echo "run_manifest verify failed with rc=${ver_rc} (not a verdict)" >&2
    exit "${ver_rc}"
fi
```

Note the verifier runs AFTER compare/dedup/escalate in P1 (those already consumed this run's earlier observations); its own observation is consumed by the NEXT day's compare pass and by the manager's verdict sensing today — acceptable for advisory, called out in the map row.
Extend `test_daily_failclosed.py` with a static-pin test like P0's: the script must contain `run_manifest mint` before the first `sense_estate` line, `export LOOP_FACTORY_RUN_ID`, and a `ver_rc` capture block.

- [ ] **Step 5: compare transitions.** FAILURE_CLASSES += `("runmanifest", "alarm"): ("runmanifest_missing_steps", "high")`, `("runmanifest", "unknown"): ("runmanifest_unverified", "med")`; MEANINGS both in owner language (alarm: "A daily run declared steps in advance and at least one declared step has no matching completion record — work the run promised did not provably happen."; needs: "Ops must rerun or repair the missing step and confirm the roster is current; you decide nothing unless a step should leave the roster."); QUESTIONS `"runmanifest"`: "Which declared step has no completion record for this run, and is the fix a rerun or a roster correction?". Extend `test_compare_charter.py` with the two mapping tests (same style as the expectation ones).
- [ ] **Step 6: GREEN** — `bash -n` + `python3 -m pytest departments/podcast/tests -q` all pass.
- [ ] **Step 7: Commit** — `git commit -m "feat(podcast): run-manifest mint/verify wired into the daily chain — roster release-bound, advisory verdicts"`

---

### Task 4: manager senses the verdict

**Files:**
- Modify: `factory/manager.py` (new `sense_manifest_verdict`, merge in `run_manager_cycle`, two compare findings)
- Test: `tests/test_manager_verdict.py` (create)

**Interfaces:**
- Consumes: verdict JSON shape (frozen above) in `<state>/run-manifests/`.
- Produces: sensed keys `manifest_verdict_status`, `manifest_verdict_counts`; findings `runmanifest_red` (warn) and `runmanifest_unverified` (warn).

- [ ] **Step 1: Failing tests**

```python
def test_no_adoption_dir_is_none_and_silent(tmp_path): ...
    # no run-manifests/ dir -> status "none"; compare adds NO runmanifest findings

def test_red_verdict_becomes_warn_finding(tmp_path): ...
    # newest <id>.verdict.json status red -> sensed status "red";
    # compare -> finding code "runmanifest_red", severity "warn"

def test_manifest_without_verdict_is_absent_warn(tmp_path): ...
    # <id>.json exists, no <id>.verdict.json -> "absent" -> runmanifest_unverified warn

def test_unknown_verdict_warns(tmp_path): ...

def test_run_manager_cycle_merges_verdict(tmp_path): ...
    # write red verdict fixture; run_manager_cycle(tmp_path) findings include runmanifest_red
```

- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement.** `sense_manifest_verdict(state_dir)`: dir absent → `{"manifest_verdict_status": "none", "manifest_verdict_counts": {}}`; else newest `*.json` (not `*.verdict.json`) by mtime → its verdict file → status + counts (missing/unexpected/duplicates/reordered lengths); no verdict file → `absent`; unreadable verdict → `unknown`. Merge in `run_manager_cycle` right after `sensed.update(sense_graph_escalations(state_dir))`: `sensed.update(sense_manifest_verdict(state_dir))`. In `compare()`, after the budget findings: status `red` → `_finding("runmanifest_red", "warn", "the last daily run's declared plan has steps with no completion record — see run-manifests verdict", observed=..., setpoint=None)`; status in {`absent`, `unknown`} → `_finding("runmanifest_unverified", "warn", "a run manifest exists but no trustworthy verdict does — the verifier did not run or could not read it", ...)`. `none` → nothing. (ADVISORY: both warn — do not implement any breach here; the blocking flip is a later owner-gated one-line change.)
- [ ] **Step 4: GREEN** — `python3 -m pytest tests/test_manager_verdict.py tests/test_manager_hardening.py tests/test_manager_budget_failclosed.py -v`.
- [ ] **Step 5: Commit** — `git commit -m "feat(manager): sense run-manifest verdicts — red/unverified surface as advisory warn findings"`

---

### Task 5 (coordinator only): integration, map, shadow, re-pin

- [ ] Apply lane patches, `python3 loopfactory.py check` + `python3 -m pytest departments/podcast/tests -q` (full green required).
- [ ] Map update: procedural-graph.md gains N12 `run_manifest mint/verify` row (kernel custody, advisory contract 0/2/1, next-day compare consumption note) and the SG-WATCHDOG chain sketch gains the mint/verify brackets.
- [ ] `python3 loopfactory.py validate --name podcast` PASS.
- [ ] One shadow run of `podcast_daily.sh`: expect rc 0, `delivered_count: 0`, a minted manifest + verdict in `state/run-manifests/`, and manager findings gaining `runmanifest_*` only if the verdict is genuinely red/unknown.
- [ ] Re-pin: `python3 loopfactory.py release pin --name podcast --source-ref <sha> --flip`; drift cleared on next manager tick; `loopfactory.py qa --name podcast` zero mismatches.
- [ ] Commit map + releases; update Work Ledger.

## Self-Review Notes

- Spec C2 coverage this phase: kernel custody (mint, create-no-replace, release-bound roster) YES; run-bound records YES; deterministic diff + verdict YES; manager consumption YES (advisory); block-acts / red-board behavior NO — that is the blocking flip, deliberately deferred with the owner; per-unit event manifests NO (P3+); independently keyed verifier NO (P2 hardening, stated).
- Verifier placement after escalate_outbox means same-day compare does not see the runmanifest observation; the manager verdict sensing covers same-day visibility. Called out in the shell comment and map row.
- Names consistent: `LOOP_FACTORY_RUN_ID`, `run-roster.json`, `runmanifest` sensor, `runmanifest_red`/`runmanifest_unverified` (manager) vs `runmanifest_missing_steps`/`runmanifest_unverified` (compare classes) — distinct namespaces (manager findings vs charter incident classes), both spelled here.
