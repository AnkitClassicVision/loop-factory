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

Transition rule (deny-by-default, all four or nothing)
------------------------------------------------------
A successor becomes runnable only on:
  1. a VALID signed step receipt for the predecessor (kernel/step_receipts),
  2. output-contract + receipt-schema conformance of the predecessor output,
  3. a satisfied edge predicate over that receipt JSON (factory/rungraph),
  4. graph/release version agreement (the live graph bytes still hash to the
     release-pinned artifact bound into every receipt).
A failed node still produces a transition token: the runner issues a step
receipt over its own failure record ({"status": "node_failed", ...}), so
refusal/escalation routing is receipt-gated exactly like the success path.

Runner-agnostic by design (the owner reserves the right to rebuild this
module): graphs (subgraphs.json v2), step receipts (kernel format), records
(runs.jsonl + run_state.json documented below) and the signed projection
(factory/projection.py) are all public, versioned formats — no runner-private
state.

Run state machine:  pending -> running <-> awaiting_receipt
                    -> done | failed | escalated | killed   (terminal)

Records (fenced: flock on state/.records.lock, atomic JSON writes, append-only
jsonl): every attempt, transition, and state change appends to
state/runs.jsonl; per-run state lives in state/graph_runs/<run_id>/
run_state.json (schema graph-run-v1). Idempotency: the run directory is the
lock — keyed (loop_id, sha256(trigger_fingerprint)); a duplicate trigger is a
recorded no-op, never a second run. Only a fingerprint HASH is recorded.
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
RUNNER_VERSION = "2.0.0"
RUN_STATE_SCHEMA = "graph-run-v1"
DEFAULT_LOCK_TIMEOUT_S = 10.0


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


# --------------------------------------------------------------------------- #
# Run state machine (finite + enumerable = a state machine, never an LLM)
# --------------------------------------------------------------------------- #

RUN_STATES = ("pending", "running", "awaiting_receipt",
              "done", "failed", "escalated", "killed")
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
            json.dump(value, handle, indent=2, sort_keys=True)
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
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


# --------------------------------------------------------------------------- #
# Release-pinned graph loading
# --------------------------------------------------------------------------- #

def load_pinned_graph(dept_dir: Path) -> dict:
    """Load the department's control graph ONLY through its pinned release.
    The live subgraphs.json must hash to the release-pinned artifact —
    anything else is drift and refuses before any execution."""
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
        data = json.loads(live_bytes.decode("utf-8"))
    except ValueError as exc:
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


class _Run:
    """One graph run: bookkeeping + fenced persistence."""

    def __init__(self, *, dept_dir: Path, state_dir: Path, run_id: str,
                 loop_id: str, fingerprint_hash: str, graph_hash: str,
                 release_hash: str, now_fn):
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
            output = json.loads(stdout)
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


def run_graph(dept_dir, *, trigger_fingerprint: str, signer=None,
              subgraph_id: str | None = None, root=None, env_base=None,
              now_fn=time.time, sleep_fn=time.sleep,
              receipt_ttl_s=None) -> dict:
    """Execute one pinned graph run. Returns {"run_id", "state", "duplicate"}."""
    receipts = _load("kreceipts", "kernel/receipts.py")
    step_receipts = _load("step_receipts", "kernel/step_receipts.py")
    rungraph = _load("rungraph", "factory/rungraph.py")

    dept_dir = Path(dept_dir)
    root = Path(root) if root is not None else ROOT
    if signer is None:
        signer = receipts.LocalSigner()  # raises on a missing key: fail closed
    if receipt_ttl_s is None:
        receipt_ttl_s = step_receipts.DEFAULT_TTL_S

    loaded = load_pinned_graph(dept_dir)
    subgraph = _select_subgraph(loaded["data"], subgraph_id)
    loop_id = subgraph["id"]
    nodes = rungraph.executable_nodes(subgraph)
    edges = subgraph["edges"]
    state_dir = dept_dir / "state"

    # Idempotency: the run directory IS the lock, keyed (loop_id, fingerprint).
    fingerprint_hash = _sha256_text(str(trigger_fingerprint))
    run_id = f"{loop_id}-{fingerprint_hash[:16]}"
    run_dir = state_dir / "graph_runs" / run_id
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_dir.mkdir(exist_ok=False)
    except FileExistsError:
        existing: dict = {}
        state_path = run_dir / "run_state.json"
        if state_path.exists():
            try:
                existing = json.loads(state_path.read_text(encoding="utf-8"))
            except ValueError:
                existing = {}
        row = {"ts": _now_iso(now_fn), "event": "duplicate_trigger_noop",
               "run_id": run_id, "loop_id": loop_id,
               "existing_state": existing.get("state", "unknown")}
        with records_lock(state_dir):
            _append_jsonl(state_dir / "runs.jsonl", row)
        return {"run_id": run_id, "state": existing.get("state", "unknown"),
                "duplicate": True}

    run = _Run(dept_dir=dept_dir, state_dir=state_dir, run_id=run_id,
               loop_id=loop_id, fingerprint_hash=fingerprint_hash,
               graph_hash=loaded["graph_hash"],
               release_hash=loaded["release_hash"], now_fn=now_fn)
    run.persist()
    run.log("run_created", graph_hash=loaded["graph_hash"],
            release_hash=loaded["release_hash"])

    identity = dict(
        department=dept_dir.name, graph_id=loop_id,
        graph_hash=loaded["graph_hash"], release_hash=loaded["release_hash"],
        run_id=run_id)
    consumed: set = set()

    def _issue(node_id: str, attempt: int, output: dict) -> str:
        return step_receipts.issue_step_receipt(
            signer=signer, now=now_fn(), output=output, node_id=node_id,
            attempt=attempt, ttl_s=receipt_ttl_s, **identity)

    def _transition(token: str, *, src: str, dst, kind: str, attempt: int,
                    output: dict) -> bool:
        successor = dst if dst is not None else f"__{kind}__"
        check = step_receipts.verify_step_receipt(
            token, signer=signer, now=now_fn(), output=output,
            consumed=consumed, successor=successor, node_id=src,
            attempt=attempt, **identity)
        if not check.ok:
            run.log("transition_blocked", node_id=src, to=dst, kind=kind,
                    reason=check.reason)
            return False
        row = {"from": src, "to": dst, "kind": kind, "attempt": attempt,
               "step_receipt_sha256": _sha256_text(token),
               "ts": _now_iso(now_fn)}
        run.record["transitions"].append(row)
        run.persist()
        run.log("transition", **row)
        return True

    run.advance("running", "run_started")
    frontier: deque = deque([subgraph["entry"]])
    queued = {subgraph["entry"]}
    terminal_reached = False
    final_state: str | None = None

    while frontier and final_state is None:
        node_id = frontier.popleft()
        node = nodes[node_id]
        if (state_dir / "KILL").exists():
            run.log("kill_switch", node_id=node_id)
            final_state = "killed"
            break
        node_record = run.record["nodes"].setdefault(
            node_id, {"state": "pending", "attempts": 0})
        node_record["state"] = "running"
        run.persist()

        output, failure, attempts_used = _execute_with_policy(
            run, node, dept_name=dept_dir.name, root=root, env_base=env_base,
            sleep_fn=sleep_fn)
        run.advance("awaiting_receipt")

        if output is not None:
            # Shadow enforcement (observational, fail-closed): the kernel
            # dispatcher is the authority on effects; a receipt CLAIMING an
            # external action in an unpromoted run is a violation, not a debate.
            external = output.get("external_actions_taken", 0)
            if external not in (0, None):
                node_record["state"] = "failed"
                node_record["reason"] = "external_actions_taken != 0 in shadow"
                run.log("shadow_violation", node_id=node_id,
                        external_actions_taken=external)
                final_state = "killed"
                break
            attempt = node_record["attempts"] = attempts_used
            token = _issue(node_id, attempt, output)
            node_record["state"] = "done"
            node_record["receipt_sha256"] = _sha256_text(token)
            node_record["output_hash"] = step_receipts.output_hash(output)
            run.advance("running")
            run.log("node_done", node_id=node_id, attempt=attempt)

            satisfied_any = False
            for edge in edges:
                if edge.get("from") != node_id:
                    continue
                try:
                    satisfied = rungraph.eval_predicate(edge["when"], output)
                except rungraph.PredicateError as exc:
                    run.log("predicate_blocked", node_id=node_id,
                            to=edge.get("to"), reason=str(exc))
                    continue
                if not satisfied:
                    continue
                satisfied_any = True
                kind = edge["kind"]
                if kind == "terminal":
                    if _transition(token, src=node_id, dst=None,
                                   kind="terminal", attempt=attempt,
                                   output=output):
                        terminal_reached = True
                    else:
                        final_state = "escalated"
                    continue
                dst = edge.get("to")
                if kind == "escalation" and dst is None:
                    if _transition(token, src=node_id, dst=None,
                                   kind="escalation", attempt=attempt,
                                   output=output):
                        run.log("escalation_edge", node_id=node_id)
                    final_state = "escalated"
                    continue
                if not _transition(token, src=node_id, dst=dst, kind=kind,
                                   attempt=attempt, output=output):
                    final_state = "escalated"
                    continue
                done_state = run.record["nodes"].get(dst, {}).get("state")
                if dst not in queued and done_state != "done":
                    queued.add(dst)
                    frontier.append(dst)
            if not satisfied_any:
                run.log("no_edge_satisfied", node_id=node_id)
                final_state = "escalated"
            continue

        # Failure path: retries exhausted. The runner's own failure record
        # becomes the receipt output, so refusal routing stays receipt-gated.
        attempt = node_record["attempts"] = attempts_used
        node_record["state"] = "failed"
        node_record["reason"] = failure["reason"]
        run.advance("running")
        run.log("node_failed", node_id=node_id, **failure)
        failure_output = {"status": "node_failed", "node_id": node_id,
                          "reason": failure["reason"],
                          "exit_code": failure["exit_code"],
                          "attempts": attempt}
        on_fail = node["failure_policy"]["on_fail"]
        if on_fail == "fail":
            final_state = "failed"
        elif on_fail == "escalate":
            final_state = "escalated"
        else:
            token = _issue(node_id, attempt, failure_output)
            routed = False
            for edge in edges:
                if (edge.get("from") != node_id or edge.get("to") != on_fail
                        or edge.get("kind") not in ("refusal", "escalation")):
                    continue
                try:
                    satisfied = rungraph.eval_predicate(edge["when"],
                                                        failure_output)
                except rungraph.PredicateError as exc:
                    run.log("predicate_blocked", node_id=node_id, to=on_fail,
                            reason=str(exc))
                    continue
                if satisfied and _transition(
                        token, src=node_id, dst=on_fail, kind=edge["kind"],
                        attempt=attempt, output=failure_output):
                    if on_fail not in queued:
                        queued.add(on_fail)
                        frontier.append(on_fail)
                    routed = True
                break
            if not routed:
                run.log("failure_route_blocked", node_id=node_id, to=on_fail)
                final_state = "escalated"

    if final_state is None:
        final_state = "done" if terminal_reached else "escalated"
        if final_state == "escalated":
            run.log("no_terminal_reached")
    run.advance(final_state, f"run_{final_state}")

    _export_projection(dept_dir, state_dir, subgraph, loaded, signer,
                       now_fn=now_fn)
    return {"run_id": run_id, "state": final_state, "duplicate": False}


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
                record = json.loads(state_path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if record.get("loop_id") != subgraph["id"]:
                continue
            runs.append({"run_id": record.get("run_id"),
                         "state": record.get("state"),
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
