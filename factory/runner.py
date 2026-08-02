"""Deterministic graph runner: executes ONE release-pinned v2 control graph,
one node at a time, with signed step receipts as the only transition tokens.

Trust model
-----------
The runner is the TRUSTED context: it holds the kernel signer (the department
processes it launches are scrubbed of it by factory/launch.py, so a department
cannot mint its own transitions). The runner still never grants effect
authority — nodes reach sends/reads/models only through kernel/ gateways,
which fail closed on their own. The runner's shadow enforcement is
observational and fail-closed: a node receipt reporting external effects
KILLS the run; it never promotes anything.

Release provenance
------------------
The runner loads ONLY the pinned graph AND verifies every executable impl's
live bytes against the pinned release manifest before any execution. A live
file that differs from its pin is a release_integrity refusal — nothing may
execute under a release_hash it doesn't belong to.

Transition rule (deny-by-default, all four or nothing)
------------------------------------------------------
Every transition — normal, refusal, escalation, terminal, AND the run-level
failure/escalation exits — consumes a freshly minted, verified step receipt
(kernel/step_receipts). A successor becomes runnable only on:
  1. a VALID signed step receipt for the predecessor,
  2. output-contract + receipt-schema conformance of the predecessor output,
  3. a satisfied edge predicate over that receipt JSON (factory/rungraph),
  4. graph/release version agreement (bound into every receipt).
Tokens are single-use PERIOD; fan-out mints one token per transition; and
consumption is DURABLE (per-run fsync'd ledger), so a restart cannot replay.
Full signed tokens are persisted in the run records — reverifiable after a
runner swap. A failed node still earns a transition token: the runner issues
a receipt over its own failure record, so refusal/escalation routing and the
failure/escalation exits are receipt-gated exactly like the success path.
Even the escalated/failed EXITS carry their own validated transition row —
a failed token verification escalates through a runner-signed escalation
receipt recording why. The ONLY unreceipted exit is `killed` (kill switch,
shadow violation, or a signing plane so broken that even the escalation
gate cannot validate): a safety abort grants nothing, so it needs no token.
Each run records a termination_reason, carried into the SIGNED projection.

Run state machine:  pending -> running <-> awaiting_receipt
                    -> done | failed | escalated | killed   (terminal)
awaiting_receipt exits only after token validation completes.

Records (fenced: flock on state/.records.lock, atomic JSON writes, append-only
jsonl): every attempt, transition, refusal, and state change appends to
state/runs.jsonl; per-run state lives in state/graph_runs/<run_id>/
run_state.json (schema graph-run-v1). Canonical-JSON policy: non-finite
numbers (NaN/Inf) are rejected at the graph, node-output, receipt, and
projection boundaries — they have no canonical form to hash or sign.

Idempotency + recovery: the run directory is the durable lock, keyed
(loop_id, FULL sha256(trigger_fingerprint)); only the hash is recorded. A
live run also holds an OS advisory lock (.run.lock, released by the kernel on
process death). Duplicate trigger against a TERMINAL run = recorded no-op.
Duplicate trigger against a non-terminal run: if the advisory lock is held
the run is in flight (no-op); if it is free the prior process died mid-run —
the run is RESUMED (re-executed under the same run_id; runs.jsonl keeps the
full history, the durable nonce ledger keeps pre-crash consumptions), never
entombed behind the lock.

Runner-agnostic by design (the owner reserves the right to rebuild this
module): graphs, step receipts, records, and the signed projection
(factory/projection.py) are all public, versioned formats — no runner-private
state.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_VERSION = "2.2.0"
RUN_STATE_SCHEMA = "graph-run-v1"
DEFAULT_LOCK_TIMEOUT_S = 10.0
TERMINAL_RUN_STATES = ("done", "failed", "escalated", "killed")


def _load(name: str, rel: str):
    key = f"runner_dep_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


class RunnerRefused(RuntimeError):
    """The run is refused before any execution. Deny-by-default."""


class StateError(RuntimeError):
    """An illegal run-state transition was attempted."""


class RecordsLockTimeout(RuntimeError):
    """The department records fence could not be acquired."""


class SigningPlaneBroken(RuntimeError):
    """Receipt issuance/signing itself raised — the trusted plane is broken.
    The run terminates as killed with a durable gate_failure finding; the
    exception never escapes the runner."""


# --------------------------------------------------------------------------- #
# Run state machine (finite + enumerable = a state machine, never an LLM)
# --------------------------------------------------------------------------- #

RUN_STATES = ("pending", "running", "awaiting_receipt") + TERMINAL_RUN_STATES
_ALLOWED_TRANSITIONS = {
    "pending": {"running", "killed"},
    "running": {"awaiting_receipt", "done", "failed", "escalated", "killed"},
    "awaiting_receipt": {"running", "done", "failed", "escalated", "killed"},
    "done": set(),
    "failed": set(),
    "escalated": set(),
    "killed": set(),
}


class RunStateMachine:
    def __init__(self, state: str = "pending"):
        if state not in RUN_STATES:
            raise StateError(f"unknown run state {state!r}")
        self.state = state

    def advance(self, new_state: str) -> str:
        if new_state not in RUN_STATES:
            raise StateError(f"unknown run state {new_state!r}")
        if new_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise StateError(f"illegal run transition {self.state} -> {new_state}")
        self.state = new_state
        return self.state


# --------------------------------------------------------------------------- #
# Fenced records (same discipline the departments already use)
# --------------------------------------------------------------------------- #

@contextmanager
def records_lock(state_dir: Path, timeout: float = DEFAULT_LOCK_TIMEOUT_S):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / ".records.lock"
    deadline = time.monotonic() + max(0.0, float(timeout))
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise RecordsLockTimeout(
                        f"timed out acquiring records lock: {lock_path}") from exc
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _reject_constant(name: str):
    raise ValueError(f"non-finite JSON constant {name!r} rejected "
                     f"(canonical-JSON policy)")


def _strict_loads(text: str):
    return json.loads(text, parse_constant=_reject_constant)


# --------------------------------------------------------------------------- #
# Release-pinned graph loading + release provenance of every impl
# --------------------------------------------------------------------------- #

def load_pinned_graph(dept_dir: Path) -> dict:
    """Load the department's control graph ONLY through its pinned release.
    The live subgraphs.json AND every executable impl must hash to their
    release-pinned artifacts — anything else refuses before any execution."""
    release = _load("release", "factory/release.py")
    rungraph = _load("rungraph", "factory/rungraph.py")
    dept_dir = Path(dept_dir)
    releases_root = dept_dir / "releases"
    current = release.read_current(releases_root)
    if current is None:
        raise RunnerRefused("no release pinned — the runner executes pinned "
                            "graphs only (run the release pin first)")
    manifest = release.load_manifest(releases_root / current)
    pinned = {a["path"]: a["sha256"] for a in manifest["artifacts"]}
    pinned_hash = pinned.get("subgraphs.json")
    if pinned_hash is None:
        raise RunnerRefused("release does not pin subgraphs.json — nothing to run")
    graph_path = dept_dir / "subgraphs.json"
    if not graph_path.exists():
        raise RunnerRefused("subgraphs.json missing from the live tree")
    live_bytes = graph_path.read_bytes()
    live_hash = hashlib.sha256(live_bytes).hexdigest()
    if live_hash != pinned_hash:
        raise RunnerRefused(
            f"graph drift: live subgraphs.json ({live_hash[:12]}) does not match "
            f"release {manifest['hash']} ({pinned_hash[:12]}) — re-pin through "
            f"the process-change runbook")
    try:
        data = _strict_loads(live_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RunnerRefused(f"subgraphs.json is not valid JSON: {exc}") from exc
    version = rungraph.manifest_version(data)
    if version != rungraph.GRAPH_SCHEMA_VERSION:
        raise RunnerRefused(
            f"schema_version {version} is not executable — the runner needs "
            f"schema_version {rungraph.GRAPH_SCHEMA_VERSION} (v1 manifests stay "
            f"lint-only; adoption is optional per department)")
    failures = rungraph.validate_manifest(data)
    if failures:
        raise RunnerRefused("graph validation failed: " + "; ".join(failures))
    # Release provenance (review B1): a live impl whose bytes differ from the
    # pin would execute under a release_hash it doesn't belong to and earn
    # signed receipts for foreign code. Verify EVERY executable impl.
    for subgraph in data.get("subgraphs", []):
        if not isinstance(subgraph, dict):
            continue
        for node in rungraph.executable_nodes(subgraph).values():
            impl = node["impl"]
            impl_pinned = pinned.get(impl)
            if impl_pinned is None:
                raise RunnerRefused(
                    f"release_integrity: impl '{impl}' is not pinned in "
                    f"release {manifest['hash']}")
            impl_path = dept_dir / impl
            if not impl_path.exists():
                raise RunnerRefused(
                    f"release_integrity: impl '{impl}' is pinned but missing "
                    f"from the live tree")
            impl_live = hashlib.sha256(impl_path.read_bytes()).hexdigest()
            if impl_live != impl_pinned:
                raise RunnerRefused(
                    f"release_integrity: impl '{impl}' ({impl_live[:12]}) does "
                    f"not match release {manifest['hash']} ({impl_pinned[:12]}) "
                    f"— re-pin through the process-change runbook")
    return {
        "data": data,
        "graph_hash": live_hash,
        "release_hash": manifest["hash"],
        "manifest": manifest,
    }


def _select_subgraph(data: dict, subgraph_id: str | None) -> dict:
    executable = [sg for sg in data.get("subgraphs", [])
                  if isinstance(sg, dict) and ("entry" in sg or "edges" in sg)]
    if subgraph_id is not None:
        for sg in executable:
            if sg.get("id") == subgraph_id:
                return sg
        raise RunnerRefused(f"no executable subgraph with id {subgraph_id!r}")
    if len(executable) != 1:
        raise RunnerRefused(
            f"{len(executable)} executable subgraphs — pass subgraph_id to "
            f"disambiguate")
    return executable[0]


# --------------------------------------------------------------------------- #
# The runner
# --------------------------------------------------------------------------- #

def _now_iso(now_fn) -> str:
    return datetime.fromtimestamp(now_fn(), tz=timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _try_flock(path: Path):
    """Acquire a non-blocking advisory lock. Returns the open handle (keep it
    open for the lock's lifetime) or None if another live process holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BlockingIOError:
        handle.close()
        return None


class _Run:
    """One graph run: bookkeeping + fenced persistence."""

    def __init__(self, *, dept_dir: Path, state_dir: Path, run_id: str,
                 loop_id: str, fingerprint_hash: str, graph_hash: str,
                 release_hash: str, now_fn, resumed_from: str | None = None,
                 prior_record: dict | None = None):
        self.dept_dir = dept_dir
        self.state_dir = state_dir
        self.run_dir = state_dir / "graph_runs" / run_id
        self.run_id = run_id
        self.loop_id = loop_id
        self.machine = RunStateMachine()
        self.now_fn = now_fn
        self.record = {
            "schema": RUN_STATE_SCHEMA,
            "run_id": run_id,
            "loop_id": loop_id,
            "department": dept_dir.name,
            "trigger_fingerprint_sha256": fingerprint_hash,
            "graph_hash": graph_hash,
            "release_hash": release_hash,
            "runner_version": RUNNER_VERSION,
            "state": self.machine.state,
            "created_at": _now_iso(now_fn),
            "updated_at": _now_iso(now_fn),
            "nodes": {},
            "transitions": [],
        }
        if prior_record:
            # Append-preserving resume: prior node records and transition rows
            # (including their full signed tokens) are NEVER discarded.
            self.record["created_at"] = prior_record.get(
                "created_at", self.record["created_at"])
            self.record["nodes"] = dict(prior_record.get("nodes", {}))
            self.record["transitions"] = list(
                prior_record.get("transitions", []))
            self.record["resumes"] = int(prior_record.get("resumes", 0)) + 1
        if resumed_from is not None:
            self.record["resumed_from"] = resumed_from

    def log(self, event: str, **fields) -> None:
        row = {"ts": _now_iso(self.now_fn), "event": event,
               "run_id": self.run_id, "loop_id": self.loop_id, **fields}
        with records_lock(self.state_dir):
            _append_jsonl(self.state_dir / "runs.jsonl", row)

    def persist(self) -> None:
        self.record["state"] = self.machine.state
        self.record["updated_at"] = _now_iso(self.now_fn)
        with records_lock(self.state_dir):
            _atomic_write_json(self.run_dir / "run_state.json", self.record)

    def advance(self, new_state: str, event: str | None = None, **fields) -> None:
        self.machine.advance(new_state)
        self.persist()
        if event:
            self.log(event, state=new_state, **fields)


def _launch_node(dept_name: str, script: Path, *, root: Path, env_base) -> tuple:
    launch = _load("launch", "factory/launch.py")
    captured: dict = {}

    def _capture(command, env):
        proc = subprocess.run(command, env=env, capture_output=True, text=True)
        captured["proc"] = proc
        return proc

    returncode = launch.launch_command(
        dept_name, [sys.executable, str(script)],
        base=env_base, root=root, runner=_capture)
    proc = captured.get("proc")
    stdout = proc.stdout if proc is not None else ""
    stderr = proc.stderr if proc is not None else ""
    return returncode, stdout, stderr


def _execute_with_policy(run: _Run, node: dict, *, dept_name: str, root: Path,
                         env_base, sleep_fn) -> tuple:
    """Run one node under its failure policy.
    Returns (output|None, failure|None, attempts_used)."""
    rungraph = _load("rungraph", "factory/rungraph.py")
    policy = node["failure_policy"]
    attempts = int(policy["max_retries"]) + 1
    script = (run.dept_dir / node["impl"]).resolve()
    if not script.is_relative_to(run.dept_dir.resolve()):
        return None, {"reason": "impl_escapes_department", "exit_code": None,
                      "attempt": 0}, 0
    failure = None
    attempt = 0
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            sleep_fn(float(policy["backoff_s"]))
        returncode, stdout, stderr = _launch_node(
            dept_name, script, root=root, env_base=env_base)
        run.log("node_attempt", node_id=node["id"], attempt=attempt,
                exit_code=returncode)
        if returncode != 0:
            failure = {"reason": "nonzero_exit", "exit_code": returncode,
                       "attempt": attempt, "stderr_tail": stderr[-500:]}
            continue
        try:
            output = _strict_loads(stdout)
            if not isinstance(output, dict):
                raise ValueError("node output must be a JSON object")
        except ValueError as exc:
            failure = {"reason": f"unparseable_output: {exc}", "exit_code": 0,
                       "attempt": attempt}
            continue
        contract_fails = (rungraph.validate_instance(node["outputs"], output)
                          + rungraph.validate_instance(node["receipt_schema"],
                                                       output))
        if contract_fails:
            failure = {"reason": "output_contract: " + "; ".join(contract_fails),
                       "exit_code": 0, "attempt": attempt}
            continue
        return output, None, attempt
    return None, failure, attempt


def _log_refusal(state_dir: Path, reason: str, now_fn) -> None:
    row = {"ts": _now_iso(now_fn), "event": "run_refused", "reason": reason}
    with records_lock(state_dir):
        _append_jsonl(Path(state_dir) / "runs.jsonl", row)


def run_graph(dept_dir, *, trigger_fingerprint: str, signer=None,
              subgraph_id: str | None = None, root=None, env_base=None,
              now_fn=time.time, sleep_fn=time.sleep,
              receipt_ttl_s=None) -> dict:
    """Execute one pinned graph run.
    Returns {"run_id", "state", "duplicate", "resumed"}."""
    receipts = _load("kreceipts", "kernel/receipts.py")
    step_receipts = _load("step_receipts", "kernel/step_receipts.py")
    rungraph = _load("rungraph", "factory/rungraph.py")

    dept_dir = Path(dept_dir)
    root = Path(root) if root is not None else ROOT
    if signer is None:
        signer = receipts.LocalSigner()  # raises on a missing key: fail closed
    if receipt_ttl_s is None:
        receipt_ttl_s = step_receipts.DEFAULT_TTL_S
    state_dir = dept_dir / "state"

    try:
        loaded = load_pinned_graph(dept_dir)
        subgraph = _select_subgraph(loaded["data"], subgraph_id)
    except RunnerRefused as exc:
        _log_refusal(state_dir, str(exc), now_fn)
        raise
    loop_id = subgraph["id"]
    nodes = rungraph.executable_nodes(subgraph)
    edges = subgraph["edges"]

    # Idempotency + recovery: run dir keyed on the FULL fingerprint hash; a
    # live run holds the advisory lock. Terminal duplicate = no-op; wedged
    # non-terminal (lock free = prior process dead) = resume, never entombed.
    fingerprint_hash = _sha256_text(str(trigger_fingerprint))
    run_id = f"{loop_id}-{fingerprint_hash}"
    run_dir = state_dir / "graph_runs" / run_id
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    resumed_from: str | None = None
    fresh = True
    try:
        run_dir.mkdir(exist_ok=False)
    except FileExistsError:
        fresh = False
    run_lock = _try_flock(run_dir / ".run.lock")
    if run_lock is None:
        row = {"ts": _now_iso(now_fn), "event": "duplicate_trigger_noop",
               "run_id": run_id, "loop_id": loop_id, "existing_state": "in_flight"}
        with records_lock(state_dir):
            _append_jsonl(state_dir / "runs.jsonl", row)
        return {"run_id": run_id, "state": "in_flight", "duplicate": True,
                "resumed": False}
    try:
        if not fresh:
            existing: dict = {}
            state_path = run_dir / "run_state.json"
            if state_path.exists():
                try:
                    existing = _strict_loads(
                        state_path.read_text(encoding="utf-8"))
                except ValueError:
                    existing = {}
            prior_state = existing.get("state", "unknown")
            if prior_state in TERMINAL_RUN_STATES:
                row = {"ts": _now_iso(now_fn), "event": "duplicate_trigger_noop",
                       "run_id": run_id, "loop_id": loop_id,
                       "existing_state": prior_state}
                with records_lock(state_dir):
                    _append_jsonl(state_dir / "runs.jsonl", row)
                return {"run_id": run_id, "state": prior_state,
                        "duplicate": True, "resumed": False}
            # Non-terminal with a free lock: the prior process died mid-run.
            # Before the checkpoint is trusted, two fences (deny-by-default):
            # C4 — the run must still belong to the CURRENTLY pinned graph and
            # release; a re-pin between crash and resume refuses, leaving the
            # run resumable under the old release or by owner decision.
            prior_rows = existing.get("transitions", [])
            if existing.get("graph_hash") is not None and (
                    existing.get("graph_hash") != loaded["graph_hash"]
                    or existing.get("release_hash") != loaded["release_hash"]):
                message = (
                    f"release_integrity: run {run_id} was recorded under "
                    f"graph {str(existing.get('graph_hash'))[:12]}/release "
                    f"{existing.get('release_hash')} but the current pin is "
                    f"{loaded['graph_hash'][:12]}/{loaded['release_hash']} — "
                    f"resume refused; prior receipts stay untouched")
                _log_refusal(state_dir, message, now_fn)
                raise RunnerRefused(message)
            # C3d — every persisted receipt is REVERIFIED before the frontier
            # is trusted; 'expired' means authentic-but-stale and is accepted,
            # anything else is tampering and refuses resume.
            for prior_row in prior_rows:
                try:
                    verdict = step_receipts.reverify_transition(
                        prior_row, record=existing, signer=signer,
                        now=now_fn())
                    ok = verdict.ok or verdict.reason == "expired"
                    detail = verdict.reason
                except Exception as exc:
                    ok, detail = False, f"unverifiable: {exc}"
                if not ok:
                    message = (
                        f"resume_integrity: transition "
                        f"{prior_row.get('from')}->{prior_row.get('to')} in "
                        f"run {run_id} failed reverification ({detail}) — "
                        f"resume refused")
                    _log_refusal(state_dir, message, now_fn)
                    raise RunnerRefused(message)
            resumed_from = prior_state

        run = _Run(dept_dir=dept_dir, state_dir=state_dir, run_id=run_id,
                   loop_id=loop_id, fingerprint_hash=fingerprint_hash,
                   graph_hash=loaded["graph_hash"],
                   release_hash=loaded["release_hash"], now_fn=now_fn,
                   resumed_from=resumed_from,
                   prior_record=existing if resumed_from is not None else None)
        run.persist()
        if resumed_from is None:
            run.log("run_created", graph_hash=loaded["graph_hash"],
                    release_hash=loaded["release_hash"])
        else:
            run.log("run_resumed", prior_state=resumed_from,
                    graph_hash=loaded["graph_hash"],
                    release_hash=loaded["release_hash"])

        final_state = _execute_run(
            run, subgraph, nodes, edges, loaded=loaded, signer=signer,
            step_receipts=step_receipts, rungraph=rungraph, root=root,
            env_base=env_base, now_fn=now_fn, sleep_fn=sleep_fn,
            receipt_ttl_s=receipt_ttl_s)

        try:
            _export_projection(dept_dir, state_dir, subgraph, loaded, signer,
                               now_fn=now_fn)
        except Exception as exc:
            # A projection that cannot be signed is simply ABSENT — the
            # auditor plane treats a missing/stale projection as findings;
            # the run outcome itself is already durably recorded.
            with records_lock(state_dir):
                _append_jsonl(state_dir / "runs.jsonl",
                              {"ts": _now_iso(now_fn),
                               "event": "projection_export_failed",
                               "run_id": run_id,
                               "reason": f"{type(exc).__name__}: {exc}"})
        return {"run_id": run_id, "state": final_state, "duplicate": False,
                "resumed": resumed_from is not None}
    finally:
        run_lock.close()


def _execute_run(run: _Run, subgraph: dict, nodes: dict, edges: list, *,
                 loaded: dict, signer, step_receipts, rungraph, root,
                 env_base, now_fn, sleep_fn, receipt_ttl_s) -> str:
    dept_dir = run.dept_dir
    state_dir = run.state_dir
    identity = dict(
        department=dept_dir.name, graph_id=run.loop_id,
        graph_hash=loaded["graph_hash"], release_hash=loaded["release_hash"],
        run_id=run.run_id)
    # Durable per-run consumption ledger: a restart keeps every consumption.
    consumed = step_receipts.DurableNonceStore(
        run.run_dir / "consumed_nonces.jsonl")

    final_reason: str | None = None
    terminal_reached = False

    def _gated_transition(*, src: str, dst, kind: str, attempt: int,
                          output_hash: str, note: str | None = None,
                          failed_check: str | None = None) -> str | None:
        """Mint one fresh token for THIS transition, verify it (single-use,
        durable consumption), and record it with the full signed token AND
        the canonical output hash it binds (auditor reverification needs only
        the row + the run record). Returns None on success, else the failed
        check's reason — no state moves on a reason. An exception from
        issuance/signing or the durable ledger is a broken signing plane:
        durable gate_failure finding, then SigningPlaneBroken (run -> killed,
        never an escaping exception)."""
        nonlocal final_reason
        try:
            token = step_receipts.issue_step_receipt(
                signer=signer, now=now_fn(), output_hash=output_hash,
                node_id=src, attempt=attempt, ttl_s=receipt_ttl_s, **identity)
            check = step_receipts.verify_step_receipt(
                token, signer=signer, now=now_fn(), output_hash=output_hash,
                consumed=consumed, node_id=src, attempt=attempt, **identity)
        except Exception as exc:
            run.log("gate_failure", node_id=src, why=f"signing_plane:{kind}",
                    reason=f"{type(exc).__name__}: {exc}")
            final_reason = "gate_failure:signing_plane"
            raise SigningPlaneBroken(str(exc)) from exc
        if not check.ok:
            run.log("transition_blocked", node_id=src, to=dst, kind=kind,
                    reason=check.reason)
            return check.reason
        row = {"from": src, "to": dst, "kind": kind, "attempt": attempt,
               "step_receipt": token,
               "step_receipt_sha256": _sha256_text(token),
               "output_sha256": output_hash,
               "ts": _now_iso(now_fn)}
        if note is not None:
            row["note"] = note
        if failed_check is not None:
            row["failed_check"] = failed_check
        run.record["transitions"].append(row)
        run.persist()
        run.log("transition", **{k: v for k, v in row.items()
                                 if k != "step_receipt"})
        return None

    def _fire_exit(src: str, attempt: int, out_hash: str, why: str,
                   exit_state: str = "escalated", kind: str = "escalation",
                   failed_check: str | None = None) -> str:
        """Receipt-gate a run exit as an explicit checkpoint decision. The
        concrete failed check (C2) rides in the receipt-bearing row. If even
        this gate cannot validate, the signing plane is broken and the run
        takes the accepted unreceipted safety abort: killed."""
        nonlocal final_reason
        rec = run.record["nodes"].setdefault(
            src, {"state": "exit", "attempts": attempt, "output_hash": out_hash})
        decision = {"edge": f"exit:{why}", "kind": kind, "to": None,
                    "satisfied": True, "state": "pending",
                    "exit": exit_state, "why": why}
        rec.setdefault("decisions", []).append(decision)
        run.persist()
        reason = _gated_transition(src=src, dst=None, kind=kind,
                                   attempt=attempt, output_hash=out_hash,
                                   note=why, failed_check=failed_check)
        if reason is None:
            decision["state"] = "fired"
            final_reason = why
            run.persist()
            return exit_state
        run.log("gate_failure", node_id=src, why=why, reason=reason)
        final_reason = f"gate_failure:{why}"
        return "killed"

    def _complete(nid: str) -> bool:
        rec = run.record["nodes"].get(nid)
        if not rec or rec.get("state") not in ("done", "failed", "exit"):
            return False
        decisions = rec.get("decisions")
        if decisions is None:
            return False
        return all(d.get("state") == "fired" for d in decisions
                   if d.get("satisfied") is True)

    def _decide_success(node_id: str, output: dict) -> list:
        """Evaluate every out-edge ONCE and persist the decisions as the
        durable checkpoint BEFORE any transition fires (C3). A blocked
        predicate is recorded as satisfied=None — never true, never false."""
        decisions = []
        satisfied_any = False
        for idx, edge in enumerate(edges):
            if edge.get("from") != node_id:
                continue
            try:
                sat = rungraph.eval_predicate(edge["when"], output)
            except rungraph.PredicateError as exc:
                run.log("predicate_blocked", node_id=node_id,
                        to=edge.get("to"), reason=str(exc))
                sat = None
            decision = {"edge": str(idx), "kind": edge["kind"],
                        "to": edge.get("to"), "satisfied": sat,
                        "state": "pending"}
            if edge["kind"] == "escalation" and edge.get("to") is None:
                decision["exit"] = "escalated"
                decision["why"] = "escalation_edge"
            if sat is True:
                satisfied_any = True
            decisions.append(decision)
        if not satisfied_any:
            run.log("no_edge_satisfied", node_id=node_id)
            decisions.append({"edge": "exit:no_edge_satisfied",
                              "kind": "escalation", "to": None,
                              "satisfied": True, "state": "pending",
                              "exit": "escalated", "why": "no_edge_satisfied"})
        return decisions

    def _decide_failure(node_id: str, node: dict, failure_output: dict) -> list:
        on_fail = node["failure_policy"]["on_fail"]
        if on_fail == "fail":
            return [{"edge": "on_fail", "kind": "failure", "to": None,
                     "satisfied": True, "state": "pending",
                     "exit": "failed", "why": "on_fail_fail"}]
        if on_fail == "escalate":
            return [{"edge": "on_fail", "kind": "escalation", "to": None,
                     "satisfied": True, "state": "pending",
                     "exit": "escalated", "why": "on_fail_escalate"}]
        for idx, edge in enumerate(edges):
            if (edge.get("from") != node_id or edge.get("to") != on_fail
                    or edge.get("kind") not in ("refusal", "escalation")):
                continue
            try:
                satisfied = rungraph.eval_predicate(edge["when"], failure_output)
            except rungraph.PredicateError as exc:
                run.log("predicate_blocked", node_id=node_id, to=on_fail,
                        reason=str(exc))
                satisfied = False
            if satisfied:
                return [{"edge": str(idx), "kind": edge["kind"],
                         "to": on_fail, "satisfied": True, "state": "pending"}]
            break
        run.log("failure_route_blocked", node_id=node_id, to=on_fail)
        return [{"edge": "exit:failure_route_blocked", "kind": "escalation",
                 "to": None, "satisfied": True, "state": "pending",
                 "exit": "escalated", "why": "failure_route_blocked"}]

    def _fire_decisions(node_id: str, rec: dict, attempt: int,
                        out_hash: str) -> str | None:
        """Fire every satisfied pending decision. Each firing flips its
        explicit checkpoint state to 'fired' AFTER its receipt-bearing row is
        recorded, so resume knows exactly which edges remain."""
        nonlocal terminal_reached, final_reason
        result = None
        for decision in list(rec.get("decisions", [])):
            if (decision.get("satisfied") is not True
                    or decision.get("state") != "pending"):
                continue
            kind = decision["kind"]
            dst = decision.get("to")
            reason = _gated_transition(src=node_id, dst=dst, kind=kind,
                                       attempt=attempt, output_hash=out_hash,
                                       note=decision.get("why"))
            if reason is not None:
                result = _fire_exit(node_id, attempt, out_hash,
                                    f"verification_failed:{kind}",
                                    failed_check=reason)
                continue
            decision["state"] = "fired"
            run.persist()
            if decision.get("exit"):
                final_reason = decision.get("why")
                result = "failed" if decision["exit"] == "failed" else "escalated"
                if decision.get("why") == "escalation_edge":
                    run.log("escalation_edge", node_id=node_id)
                continue
            if kind == "terminal":
                terminal_reached = True
                continue
            if dst is not None and dst not in queued and not _complete(dst):
                queued.add(dst)
                frontier.append(dst)
        return result

    # Reconstruction from the explicit checkpoint (identical for fresh and
    # resumed runs — a fresh record just yields entry-only). A fired exit
    # decision concludes the run in ITS state: resume never appends a second
    # terminal row nor mutates failed into escalated (C3c).
    concluded: tuple | None = None
    for rec in run.record["nodes"].values():
        for decision in rec.get("decisions", []):
            if decision.get("state") != "fired":
                continue
            if decision.get("kind") == "terminal":
                terminal_reached = True
            if decision.get("exit"):
                concluded = (decision["exit"], decision.get("why"))
    frontier: deque = deque()
    queued: set = set()

    def _want(nid: str) -> None:
        if nid not in queued and not _complete(nid):
            queued.add(nid)
            frontier.append(nid)

    _want(subgraph["entry"])
    for nid, rec in run.record["nodes"].items():
        for decision in rec.get("decisions", []):
            if decision.get("satisfied") is not True:
                continue
            if decision.get("state") == "fired" and decision.get("to"):
                _want(decision["to"])
            elif decision.get("state") == "pending":
                _want(nid)
    queued |= {nid for nid in run.record["nodes"] if _complete(nid)}
    final_state: str | None = None

    run.advance("running", "run_started")
    if concluded is not None:
        final_state, final_reason = concluded

    try:
        while frontier and final_state is None:
            node_id = frontier.popleft()
            if (state_dir / "KILL").exists():
                run.log("kill_switch", node_id=node_id, phase="pre_node")
                final_state, final_reason = "killed", "kill_switch"
                break
            rec = run.record["nodes"].get(node_id)
            if (rec is not None and rec.get("decisions") is not None
                    and rec.get("state") in ("done", "failed", "exit")):
                # Resume refire: the node already completed and checkpointed
                # its decisions — fire the remaining pending edges from the
                # persisted output hash, never re-execute (C3b).
                attempt = int(rec.get("attempts", 0))
                out_hash = rec["output_hash"]
                run.advance("awaiting_receipt")
                run.log("resume_refire", node_id=node_id)
            else:
                node = nodes[node_id]
                node_record = run.record["nodes"].setdefault(
                    node_id, {"state": "pending", "attempts": 0})
                node_record["state"] = "running"
                run.persist()
                output, failure, attempts_used = _execute_with_policy(
                    run, node, dept_name=dept_dir.name, root=root,
                    env_base=env_base, sleep_fn=sleep_fn)
                # All token work happens INSIDE awaiting_receipt; the state
                # exits only after every transition holds a validated token.
                run.advance("awaiting_receipt")
                # Kill poll AFTER node completion, BEFORE any transition: a
                # kill raised while the node ran stops the graph walk cold.
                if (state_dir / "KILL").exists():
                    run.log("kill_switch", node_id=node_id, phase="post_node")
                    final_state, final_reason = "killed", "kill_switch"
                    break
                rec = node_record
                attempt = rec["attempts"] = attempts_used
                if output is not None:
                    # Shadow enforcement (observational, fail-closed): the
                    # kernel dispatcher is the authority on effects; a receipt
                    # CLAIMING an external action in an unpromoted run is a
                    # violation, not a debate.
                    external = output.get("external_actions_taken", 0)
                    if external not in (0, None):
                        rec["state"] = "failed"
                        rec["reason"] = "external_actions_taken != 0 in shadow"
                        run.log("shadow_violation", node_id=node_id,
                                external_actions_taken=external)
                        final_state, final_reason = "killed", "shadow_violation"
                        break
                    rec["state"] = "done"
                    out_hash = rec["output_hash"] = \
                        step_receipts.output_hash(output)
                    rec["decisions"] = _decide_success(node_id, output)
                    run.persist()  # durable checkpoint BEFORE any firing (C3)
                    run.log("node_done", node_id=node_id, attempt=attempt)
                else:
                    # Failure path: the runner's own failure record becomes
                    # the receipt-bound step output, and the on_fail route is
                    # checkpointed as an explicit decision before it fires.
                    rec["state"] = "failed"
                    rec["reason"] = failure["reason"]
                    failure_output = {"status": "node_failed",
                                      "node_id": node_id,
                                      "reason": failure["reason"],
                                      "exit_code": failure["exit_code"],
                                      "attempts": attempt}
                    out_hash = rec["output_hash"] = \
                        step_receipts.output_hash(failure_output)
                    rec["decisions"] = _decide_failure(node_id, node,
                                                       failure_output)
                    run.persist()
                    run.log("node_failed", node_id=node_id, **failure)

            result = _fire_decisions(node_id, rec, attempt, out_hash)
            if result is not None:
                final_state = result
            if final_state is None:
                run.advance("running")

        if final_state is None:
            if terminal_reached:
                final_state, final_reason = "done", "terminal_edge"
            else:
                # The walk ended without a terminal edge firing: a stall.
                # Even this exit is receipt-gated, bound to the run itself.
                run.log("no_terminal_reached")
                stall_hash = step_receipts.output_hash(
                    {"status": "run_stalled", "run_id": run.run_id})
                final_state = _fire_exit("__run__", 0, stall_hash,
                                         "no_terminal_reached")
    except SigningPlaneBroken:
        final_state = "killed"
        if not (final_reason or "").startswith("gate_failure"):
            final_reason = "gate_failure:signing_plane"
    run.record["termination_reason"] = final_reason
    run.advance(final_state, f"run_{final_state}")
    return final_state


# --------------------------------------------------------------------------- #
# Projection export (the runner EXPORTS; the auditor plane verifies)
# --------------------------------------------------------------------------- #

def _export_projection(dept_dir: Path, state_dir: Path, subgraph: dict,
                       loaded: dict, signer, *, now_fn) -> None:
    projection = _load("projection", "factory/projection.py")
    rungraph = _load("rungraph", "factory/rungraph.py")
    runs = []
    runs_root = state_dir / "graph_runs"
    if runs_root.is_dir():
        for state_path in sorted(runs_root.glob("*/run_state.json")):
            try:
                record = _strict_loads(state_path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if record.get("loop_id") != subgraph["id"]:
                continue
            runs.append({"run_id": record.get("run_id"),
                         "state": record.get("state"),
                         "termination_reason": record.get("termination_reason"),
                         "transitions": record.get("transitions", [])})
    factory_version = loaded["manifest"].get("factory_version") or {
        "graph_schema_version": rungraph.GRAPH_SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "telemetry_schema_version": projection.TELEMETRY_SCHEMA_VERSION,
        "template_set_hash": "unrecorded",
    }
    body = projection.build_projection(
        department=dept_dir.name, graph_id=subgraph["id"],
        graph_hash=loaded["graph_hash"], release_hash=loaded["release_hash"],
        factory_version=factory_version,
        nodes=subgraph.get("nodes", []), edges=subgraph.get("edges", []),
        runs=runs, generated_at=_now_iso(now_fn))
    signed = projection.sign_projection(body, signer)
    with records_lock(state_dir):
        _atomic_write_json(state_dir / "receipts" / "execution-projection.json",
                           signed)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

_EXIT_BY_STATE = {"done": 0, "failed": 3, "escalated": 4, "killed": 5}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic runner for a release-pinned v2 control graph")
    parser.add_argument("--department", required=True)
    parser.add_argument("--root", default=None)
    parser.add_argument("--trigger-fingerprint", required=True,
                        help="idempotency key for this trigger (e.g. the date "
                             "stamp); only its sha256 is recorded")
    parser.add_argument("--subgraph", default=None)
    args = parser.parse_args()
    root = Path(args.root) if args.root else ROOT
    dept_dir = root / "departments" / args.department
    try:
        result = run_graph(dept_dir, trigger_fingerprint=args.trigger_fingerprint,
                           subgraph_id=args.subgraph, root=root)
    except (RunnerRefused, ValueError) as exc:
        print(json.dumps({"ok": False, "refused": str(exc)}))
        return 2
    print(json.dumps(result))
    if result.get("duplicate"):
        return 0
    return _EXIT_BY_STATE.get(result["state"], 4)


if __name__ == "__main__":
    sys.exit(main())
