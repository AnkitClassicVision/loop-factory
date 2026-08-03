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

Idempotency + recovery: a fenced trigger binding maps
(loop_id, FULL sha256(trigger_fingerprint)) to the one canonical identity
minted by factory/runrecord.py; only the hash is recorded. The canonical run
directory holds an OS advisory lock (.run.lock, released by the kernel on
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
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_VERSION = "2.5.0"
RUN_STATE_SCHEMA = "graph-run-v1"
# Runner-mediated appends (review B1, Option C). The runner injects a
# per-attempt record SPOOL path into every node process AFTER the capability
# scrub (same pattern as OE_DEPARTMENT in factory/launch.py; canonical name
# in kernel/capabilities.py). Appenders write there; canonical streams are
# unreachable from node code. Post-node — and BEFORE any transition receipt
# mints — the runner validates each spooled row, stamps identity from its
# OWN execution state (identity is assigned, never claimed), signs each
# promoted row, and appends under the records fence. Nothing secret travels
# to nodes at all.
RECORD_SPOOL_ENV = "OE_RECORD_SPOOL"
SPOOL_STREAMS = ("runs-v2.jsonl", "telemetry.jsonl", "scores.jsonl")
MAX_SPOOL_ROWS_PER_STREAM = 1000
MAX_SPOOL_ROW_BYTES = 65536
MAX_SPOOL_FILE_BYTES = 8 * 1024 * 1024
PROMOTION_SCHEMA = "promotion/v1"
PROMOTION_MARKER_SCHEMA = "promotion-marker/v1"


def run_control_dir(root, department: str, run_id: str) -> Path:
    """The runner's own control plane for one run — promotion markers only.

    Deliberately OUTSIDE departments/<dept>/state (review F1): the node's
    sanctioned paths are its state directory and the spool it is handed, and
    neither leads here, so node code following the toolchain cannot forge a
    marker and strand its own records. Same-uid OS access can still write
    anywhere on the filesystem — that is the same residual risk documented
    at _promote_spool, and the reason markers are SIGNED and verified before
    a skip is honored, with factory/rollup.py as the read-time backstop.
    """
    # Beside the estate records plane, never inside it: estate/state is a
    # scanned records tree (factory/rollup.py), and control markers are not
    # records — putting them there would make every run look like a
    # half-configured estate.
    return Path(root) / "estate" / "run-control" / department / run_id
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


def _launch_node(dept_name: str, script: Path, *, root: Path, env_base,
                 spool_dir: Path) -> tuple:
    launch = _load("launch", "factory/launch.py")
    captured: dict = {}

    def _capture(command, env):
        # The spool path is injected AFTER the capability scrub, exactly
        # like OE_DEPARTMENT — a location, never a credential.
        env = dict(env)
        env[RECORD_SPOOL_ENV] = str(spool_dir)
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


def _canon_row_bytes(row) -> bytes:
    return json.dumps(row, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _promotion_row_id(run_id: str, node_id: str, stream: str,
                      index: int) -> str:
    """Stable across re-executions (rollup._stable_id pattern): a crash
    between the canonical appends and the promotion marker re-appends the
    same logical rows, and this id collapses them at read time."""
    material = "\x1f".join(("promotion", run_id, node_id, stream, str(index)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _read_spool_stream(spool_dir: Path, stream: str) -> list:
    """Read one spool stream under hostile-input bounds (review F5).

    Order matters: lstat FIRST (a symlink or non-regular entry is refused
    before it is ever opened — the same discipline as rollup._open_source),
    then the file-size bound BEFORE the read, then per-row and row-count
    bounds. A node cannot make the runner read an unbounded file or follow
    a link out of the spool."""
    path = spool_dir / stream
    try:
        info = path.lstat()
    except FileNotFoundError:
        return []
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"spool {stream} is not a regular file "
                         f"(symlinks and special files are refused)")
    if info.st_size > MAX_SPOOL_FILE_BYTES:
        raise ValueError(f"spool {stream} is {info.st_size} bytes, over the "
                         f"{MAX_SPOOL_FILE_BYTES}-byte bound")
    rows = []
    for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_SPOOL_ROW_BYTES:
            raise ValueError(f"spool {stream} row {number} exceeds "
                             f"{MAX_SPOOL_ROW_BYTES} bytes")
        try:
            row = _strict_loads(line)
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
        except ValueError as exc:
            raise ValueError(f"spool {stream} row {number} is not a valid "
                             f"record ({exc})") from exc
        rows.append(row)
        if len(rows) > MAX_SPOOL_ROWS_PER_STREAM:
            raise ValueError(f"spool {stream} overflows "
                             f"{MAX_SPOOL_ROWS_PER_STREAM} rows")
    return rows


def _marker_signature(signer, payload: dict) -> str:
    unsigned = {k: v for k, v in payload.items() if k != "sig"}
    return signer.sign(_canon_row_bytes(unsigned))


def _marker_ok(marker, signer, *, department: str, run_id: str,
               node_id: str) -> str | None:
    """Verify a promotion marker before any skip is honored (review F1).

    Signature, identity, and self-consistency (declared counts vs declared
    row ids) all have to hold. Returns None when the marker is trustworthy,
    else the reason it is not."""
    if not isinstance(marker, dict):
        return "marker is not an object"
    if marker.get("schema") != PROMOTION_MARKER_SCHEMA:
        return f"marker schema is not {PROMOTION_MARKER_SCHEMA}"
    signature = marker.get("sig")
    if not isinstance(signature, str) or not signature:
        return "marker carries no signature"
    try:
        if not signer.verify(_canon_row_bytes(
                {k: v for k, v in marker.items() if k != "sig"}), signature):
            return "marker signature does not verify"
    except Exception as exc:
        return f"marker signature unverifiable: {type(exc).__name__}"
    if (marker.get("department") != department
            or marker.get("run_id") != run_id
            or marker.get("node") != node_id):
        return "marker identity does not match this execution"
    counts = marker.get("counts")
    row_ids = marker.get("row_ids")
    if not isinstance(counts, dict) or not isinstance(row_ids, list):
        return "marker counts/row_ids are malformed"
    if any(not isinstance(value, int) or isinstance(value, bool)
           for value in counts.values()):
        return "marker counts are not integers"
    if not all(isinstance(row_id, str) for row_id in row_ids):
        return "marker row ids are not strings"
    if sum(counts.values()) != len(row_ids):
        return "marker counts do not match its declared row ids"
    return None


def _canonical_promotion_ids(state_dir: Path) -> set:
    ids = set()
    for stream in SPOOL_STREAMS:
        path = state_dir / stream
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = _strict_loads(line)
                promotion = row.get("promotion") if isinstance(row, dict) else None
                if isinstance(promotion, dict) and isinstance(
                        promotion.get("id"), str):
                    ids.add(promotion["id"])
            except ValueError:
                continue
    return ids


def _record_graph_incident(run: _Run, code: str, detail: str,
                           node_id: str | None = None) -> None:
    """Durable, rollup-visible incident for an identity-plane fault."""
    row = {"ts": _now_iso(run.now_fn), "code": code, "severity": "breach",
           "status": "open", "department": run.dept_dir.name,
           "run_id": run.run_id, "node": node_id, "detail": detail}
    with records_lock(run.state_dir):
        _append_jsonl(run.state_dir / "graph_incidents.jsonl", row)
    run.log("graph_incident", code=code, node_id=node_id, detail=detail)


def _promote_spool(run: _Run, *, node_id: str, attempt: int, spool_dir: Path,
                   signer, control_dir: Path, output: dict,
                   crash_hook=None) -> str | None:
    """Validate, stamp, sign, and append the node's spooled records.

    This is the ONLY path from node-emitted records to the canonical streams
    (review B1, Option C). Identity is ASSIGNED here from the runner's own
    execution state — department, graph run, node — never taken from the
    row; whatever a node claimed is overwritten. Every row is validated with
    the stream's own validator FIRST — including the FULL step-telemetry
    validator shared with rollup (review F4), so nothing invalid is ever
    signed; any malformed, overflowing, reserved-field, or wrong-stream row
    refuses the WHOLE promotion (nothing partial) and fails the node. Each
    promoted row is signed by the runner's signer so factory/rollup.py can
    verify at read time and quarantine direct same-uid file writes.

    Exactly-once across crash-resume (review F1/F3): markers live in the
    runner's OWN control directory, outside every path the node is handed
    (run_control_dir), and are SIGNED — a marker is honored as a skip only
    after its signature, identity, and declared counts/row-ids verify AND
    the rows it claims are actually present in the canonical streams. A
    marker that fails any of that is an incident and the node is
    re-promoted, never silently skipped. A pending marker written before the
    canonical appends lets a crashed promotion resume from the ORIGINAL
    spool without re-executing the node, so recovery re-appends
    byte-identical rows under the same stable promotion id instead of a
    second, differing body.

    RESIDUAL RISK (documented, not solved here): the defense is against the
    sanctioned node toolchain. Under a single uid a hostile process can
    still write anywhere on the filesystem — including the control dir — and
    can truncate or delete canonical files outright; detecting destructive
    edits needs OS-level separation (a distinct uid or a supervisor process)
    or hash-chaining of the streams. That is a promotion-ladder live-tier
    concern; the KMSSigner seam in kernel/receipts.py is reserved for
    exactly that hardening. Signed markers plus rollup's read-time
    verification are what make such tampering visible rather than silent.

    Returns None on success (or verified duplicate skip), else the refusal
    reason.
    """
    # factory/scores.py imports the kernel package; when the runner is
    # invoked by file path the repo root may be absent from sys.path.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    runrecord = _load("runrecord", "factory/runrecord.py")
    scores = _load("scores", "factory/scores.py")
    from factory import rollup as rollup_module

    department = run.dept_dir.name

    # ---- validate (all rows, before anything is written or signed) -------- #
    try:
        unknown = sorted(child.name for child in spool_dir.iterdir()
                         if child.name not in SPOOL_STREAMS)
        if unknown:
            return (f"spool_rejected: unknown spool entries {unknown} — "
                    f"nodes may emit only {list(SPOOL_STREAMS)}")
        spooled = {stream: _read_spool_stream(spool_dir, stream)
                   for stream in SPOOL_STREAMS}
        for stream, rows in spooled.items():
            for row in rows:
                if "promotion" in row:
                    raise ValueError(f"spool {stream} row carries the "
                                     f"reserved 'promotion' field")
                if stream == "runs-v2.jsonl":
                    runrecord.validate_record(row)
                elif stream == "scores.jsonl":
                    scores.validate_score(row)
                else:
                    # F4: the SAME validator rollup applies at read time,
                    # so an invalid row is refused before it can be signed
                    rollup_module.validate_telemetry_row(row, department)
                    _canon_row_bytes(row)  # canonical-JSON policy
    except (ValueError, OSError) as exc:
        return f"spool_promotion: {exc}"

    # ---- stamp + sign (identity assigned from runner state) --------------- #
    stamped_streams: dict[str, list] = {}
    try:
        for stream, rows in spooled.items():
            stamped_rows = []
            for index, row in enumerate(rows):
                stamped = dict(row)
                if stream == "runs-v2.jsonl":
                    stamped["department"] = department
                    stamped["run_id"] = run.run_id
                    stamped["node"] = node_id
                elif stream == "telemetry.jsonl":
                    stamped["loopfactory.department"] = department
                    stamped["loopfactory.run_id"] = run.run_id
                    stamped["loopfactory.node"] = node_id
                else:  # scores: target node stays the score's SUBJECT
                    target = dict(stamped.get("target_ref") or {})
                    target["department"] = department
                    target["run_id"] = run.run_id
                    stamped["target_ref"] = target
                stamped["promotion"] = {
                    "schema": PROMOTION_SCHEMA,
                    "id": _promotion_row_id(run.run_id, node_id, stream,
                                            index),
                    "attempt": attempt,
                }
                signature = signer.sign(_canon_row_bytes(stamped))
                stamped["promotion"] = {**stamped["promotion"],
                                        "sig": signature}
                stamped_rows.append(stamped)
            stamped_streams[stream] = stamped_rows
    except Exception as exc:
        run.log("gate_failure", node_id=node_id,
                why="signing_plane:promotion",
                reason=f"{type(exc).__name__}: {exc}")
        raise SigningPlaneBroken(str(exc)) from exc

    # ---- verified skip: has this node already been promoted? -------------- #
    counts = {stream: len(rows) for stream, rows in stamped_streams.items()}
    row_ids = [stamped["promotion"]["id"]
               for stamped_rows in stamped_streams.values()
               for stamped in stamped_rows]
    control_dir.mkdir(parents=True, exist_ok=True)
    ledger = control_dir / "promotions.jsonl"
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                marker = _strict_loads(line)
            except ValueError:
                # torn trailing line: never durably committed, re-promote
                continue
            if not isinstance(marker, dict) or marker.get("node") != node_id:
                continue
            fault = _marker_ok(marker, signer, department=department,
                               run_id=run.run_id, node_id=node_id)
            if fault is None:
                present = _canonical_promotion_ids(run.state_dir)
                missing = [row_id for row_id in marker["row_ids"]
                           if row_id not in present]
                if missing:
                    fault = (f"marker claims {len(missing)} promoted row(s) "
                             f"absent from the canonical streams")
            if fault is None:
                run.log("promotion_skipped_duplicate", node_id=node_id)
                return None
            _record_graph_incident(
                run, "promotion_marker_unverifiable", fault, node_id=node_id)
            break  # re-promote rather than trust an unverifiable marker

    # ---- pending marker, canonical appends, completion marker ------------- #
    pending_payload = {
        "schema": PROMOTION_MARKER_SCHEMA, "state": "pending",
        "department": department, "run_id": run.run_id, "node": node_id,
        "attempt": attempt, "counts": counts, "row_ids": row_ids,
        "spool_dir": str(spool_dir), "output": output,
        "ts": _now_iso(run.now_fn),
    }
    try:
        pending_payload["sig"] = _marker_signature(signer, pending_payload)
        completion_payload = {
            "schema": PROMOTION_MARKER_SCHEMA, "state": "promoted",
            "department": department, "run_id": run.run_id, "node": node_id,
            "attempt": attempt, "counts": counts, "row_ids": row_ids,
            "ts": _now_iso(run.now_fn),
        }
        completion_payload["sig"] = _marker_signature(
            signer, completion_payload)
    except Exception as exc:
        run.log("gate_failure", node_id=node_id, why="signing_plane:marker",
                reason=f"{type(exc).__name__}: {exc}")
        raise SigningPlaneBroken(str(exc)) from exc

    pending_path = control_dir / f"pending-{node_id}.json"
    _atomic_write_json(pending_path, pending_payload)
    with records_lock(run.state_dir):
        for stream, stamped_rows in stamped_streams.items():
            for stamped in stamped_rows:
                _append_jsonl(run.state_dir / stream, stamped)
    if crash_hook is not None:
        crash_hook("pre_promotion_marker")
    _append_jsonl(ledger, completion_payload)
    pending_path.unlink(missing_ok=True)
    run.log("spool_promoted", node_id=node_id, attempt=attempt, counts=counts)
    return None


def _pending_promotion(run: _Run, control_dir: Path, node_id: str,
                       signer) -> dict | None:
    """A promotion that began and never completed (review F3).

    Recovery must NOT re-execute the node: a second execution produces a
    different record body under the same stable promotion id, which is the
    two-signed-bodies defect. Instead the original spool and the node's
    original output are recovered from the signed pending marker, so the
    completed promotion re-appends byte-identical rows."""
    pending_path = control_dir / f"pending-{node_id}.json"
    if not pending_path.exists():
        return None
    try:
        marker = _strict_loads(pending_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        _record_graph_incident(run, "promotion_marker_unverifiable",
                               f"pending marker unreadable: {exc}",
                               node_id=node_id)
        return None
    fault = _marker_ok(marker, signer, department=run.dept_dir.name,
                       run_id=run.run_id, node_id=node_id)
    if fault is None and not isinstance(marker.get("output"), dict):
        fault = "pending marker carries no node output"
    if fault is None and not Path(marker.get("spool_dir", "")).is_dir():
        fault = "pending marker points at a missing spool"
    if fault is not None:
        _record_graph_incident(run, "promotion_marker_unverifiable", fault,
                               node_id=node_id)
        pending_path.unlink(missing_ok=True)
        return None
    return marker


def _execute_with_policy(run: _Run, node: dict, *, dept_name: str, root: Path,
                         env_base, sleep_fn, signer, now_fn,
                         crash_hook=None) -> tuple:
    """Run one node under its failure policy, then promote its spool.
    Returns (output|None, failure|None, attempts_used). Spool rows from
    FAILED attempts are never promoted — they stay in the per-attempt spool
    directory for audit; the runner's own failure record covers the books."""
    rungraph = _load("rungraph", "factory/rungraph.py")
    policy = node["failure_policy"]
    attempts = int(policy["max_retries"]) + 1
    script = (run.dept_dir / node["impl"]).resolve()
    if not script.is_relative_to(run.dept_dir.resolve()):
        return None, {"reason": "impl_escapes_department", "exit_code": None,
                      "attempt": 0}, 0
    control_dir = run_control_dir(root, dept_name, run.run_id)
    # F3: a promotion that began before a crash completes from its ORIGINAL
    # spool and output — re-executing would produce a different body under
    # the same promotion id.
    pending = _pending_promotion(run, control_dir, node["id"], signer)
    if pending is not None:
        run.log("promotion_resumed", node_id=node["id"],
                attempt=pending["attempt"])
        reason = _promote_spool(
            run, node_id=node["id"], attempt=pending["attempt"],
            spool_dir=Path(pending["spool_dir"]), signer=signer,
            control_dir=control_dir, output=pending["output"])
        if reason is not None:
            run.log("spool_rejected", node_id=node["id"], reason=reason)
            return None, {"reason": reason, "exit_code": 0,
                          "attempt": pending["attempt"]}, pending["attempt"]
        return pending["output"], None, pending["attempt"]

    failure = None
    attempt = 0
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            sleep_fn(float(policy["backoff_s"]))
        spool_dir = run.run_dir / "spool" / f"{node['id']}-{attempt}"
        if spool_dir.exists():
            # stale spool from a crashed prior execution of this attempt.
            # RETENTION: spools of FAILED attempts are kept for audit and
            # are bounded per attempt (MAX_SPOOL_FILE_BYTES per stream);
            # they live under the run directory, so a department's retention
            # sweep removes them with the run. A long-lived department with
            # many retried nodes should include state/graph_runs in that
            # sweep — nothing here prunes them automatically.
            shutil.rmtree(spool_dir)
        spool_dir.mkdir(parents=True)
        returncode, stdout, stderr = _launch_node(
            dept_name, script, root=root, env_base=env_base,
            spool_dir=spool_dir)
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
        if crash_hook is not None:
            crash_hook("pre_promotion")
        reason = _promote_spool(run, node_id=node["id"], attempt=attempt,
                                spool_dir=spool_dir, signer=signer,
                                control_dir=control_dir, output=output,
                                crash_hook=crash_hook)
        if reason is not None:
            run.log("spool_rejected", node_id=node["id"], reason=reason)
            return None, {"reason": reason, "exit_code": 0,
                          "attempt": attempt}, attempt
        return output, None, attempt
    return None, failure, attempt


def _log_refusal(state_dir: Path, reason: str, now_fn) -> None:
    row = {"ts": _now_iso(now_fn), "event": "run_refused", "reason": reason}
    with records_lock(state_dir):
        _append_jsonl(Path(state_dir) / "runs.jsonl", row)


def _bind_trigger_run_id(root: Path, state_dir: Path, *, department: str,
                         loop_id: str, fingerprint_hash: str,
                         signer) -> tuple[str, bool]:
    """Resolve one trigger to one canonical ``runrecord.new_run_id`` value.

    The trigger hash remains the idempotency key, but it is no longer also a
    second run-id allocator. A fenced binding gives concurrent/retried
    triggers the same canonical run id. Before creating a new binding, legacy
    deterministic graph-run directories are discovered and adopted so an
    upgrade cannot replay an already-seen trigger.
    """
    binding_key = _sha256_text(f"{loop_id}\x1f{fingerprint_hash}")
    binding_path = (Path(root) / "estate" / "run-control" / department
                    / "trigger-bindings" / f"{binding_key}.json")
    runs_root = state_dir / "graph_runs"
    with records_lock(state_dir):
        binding_preexisting = binding_path.exists()
        if binding_path.exists():
            try:
                binding = _strict_loads(binding_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RunnerRefused(
                    f"trigger_binding_integrity: {binding_path.name} is "
                    f"unreadable ({exc})") from exc
            expected = {
                "schema": "graph-trigger-binding/v1",
                "department": department,
                "loop_id": loop_id,
                "trigger_fingerprint_sha256": fingerprint_hash,
            }
            if not isinstance(binding, dict) or any(
                    binding.get(key) != value for key, value in expected.items()):
                raise RunnerRefused(
                    f"trigger_binding_integrity: {binding_path.name} identity "
                    "does not match this trigger")
            run_id = binding.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise RunnerRefused(
                    f"trigger_binding_integrity: {binding_path.name} lacks run_id")
            signature = binding.get("sig")
            try:
                verified = isinstance(signature, str) and signer.verify(
                    _canon_row_bytes({k: v for k, v in binding.items()
                                      if k != "sig"}), signature)
            except Exception as exc:
                raise RunnerRefused(
                    f"trigger_binding_integrity: {binding_path.name} "
                    f"signature is unverifiable ({type(exc).__name__})") from exc
            if not verified:
                raise RunnerRefused(
                    f"trigger_binding_integrity: {binding_path.name} "
                    "signature does not verify")
        else:
            run_id = None
            if runs_root.is_dir():
                for state_path in sorted(runs_root.glob("*/run_state.json")):
                    try:
                        prior = _strict_loads(
                            state_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if (isinstance(prior, dict)
                            and prior.get("department") == department
                            and prior.get("loop_id") == loop_id
                            and prior.get("trigger_fingerprint_sha256")
                            == fingerprint_hash
                            and isinstance(prior.get("run_id"), str)
                            and prior["run_id"]):
                        run_id = prior["run_id"]
                        break
            if run_id is None:
                for _ in range(16):
                    candidate = _new_run_id()
                    if not (runs_root / candidate).exists():
                        run_id = candidate
                        break
                if run_id is None:
                    raise RunnerRefused(
                        "run_identity: canonical run id collision budget exhausted")
            binding = {
                "schema": "graph-trigger-binding/v1",
                "department": department,
                "loop_id": loop_id,
                "trigger_fingerprint_sha256": fingerprint_hash,
                "run_id": run_id,
            }
            try:
                binding["sig"] = signer.sign(_canon_row_bytes(binding))
            except Exception as exc:
                raise RunnerRefused(
                    "trigger_binding_integrity: could not sign canonical "
                    f"run binding ({type(exc).__name__})") from exc
            _atomic_write_json(binding_path, binding)
        (runs_root / run_id).mkdir(parents=True, exist_ok=True)
    return run_id, binding_preexisting


def _new_run_id() -> str:
    """Delegate identity minting to the canonical runrecord module."""
    return _load("canonical_runrecord", "factory/runrecord.py").new_run_id()


def run_graph(dept_dir, *, trigger_fingerprint: str, signer=None,
              subgraph_id: str | None = None, root=None, env_base=None,
              now_fn=time.time, sleep_fn=time.sleep,
              receipt_ttl_s=None, crash_hook=None, export_hook=None) -> dict:
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

    # Idempotency + recovery: the full trigger hash is bound, under the records
    # fence, to one canonical runrecord.new_run_id value. A live run holds the
    # advisory lock. Terminal duplicate = no-op; wedged non-terminal (lock free
    # = prior process dead) = resume, never entombed.
    fingerprint_hash = _sha256_text(str(trigger_fingerprint))
    try:
        run_id, binding_preexisting = _bind_trigger_run_id(
            root, state_dir, department=dept_dir.name, loop_id=loop_id,
            fingerprint_hash=fingerprint_hash, signer=signer)
    except RunnerRefused as exc:
        _log_refusal(state_dir, str(exc), now_fn)
        raise
    run_dir = state_dir / "graph_runs" / run_id
    resumed_from: str | None = None
    fresh = not (run_dir / "run_state.json").exists()
    resumed_without_checkpoint = binding_preexisting and fresh
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
                # R5-C3: an EXISTING checkpoint must parse and carry full
                # identity — anything less would silently skip the re-pin
                # fence. Only a genuinely absent state file (crash before
                # the first persist) initializes fresh.
                try:
                    parsed = _strict_loads(
                        state_path.read_text(encoding="utf-8"))
                    if not isinstance(parsed, dict):
                        raise ValueError("run_state.json root must be an object")
                    existing = parsed
                except ValueError as exc:
                    message = (f"resume_integrity: run {run_id} state file is "
                               f"malformed ({exc}) — resume refused")
                    _log_refusal(state_dir, message, now_fn)
                    raise RunnerRefused(message) from exc
                required = ("department", "loop_id", "run_id",
                            "graph_hash", "release_hash")
                missing = sorted(k for k in required if not existing.get(k))
                if missing:
                    message = (f"resume_integrity: run {run_id} checkpoint "
                               f"lacks identity fields {missing} — resume "
                               f"refused")
                    _log_refusal(state_dir, message, now_fn)
                    raise RunnerRefused(message)
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
            if existing and (
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
            seen_edge_keys: set = set()
            for prior_row in prior_rows:
                # R6-S1: one-to-one — a second row for the same authenticated
                # edge would mean a double-mint. R6-S2: a row whose source
                # node lacks a signed checkpoint is not a legitimate crash
                # shape (node effects precede transition issuance), refuse.
                edge_key = (prior_row.get("from"), prior_row.get("edge"))
                if edge_key in seen_edge_keys:
                    message = (f"resume_integrity: duplicate transition rows "
                               f"for edge {edge_key} in run {run_id} — "
                               f"resume refused")
                    _log_refusal(state_dir, message, now_fn)
                    raise RunnerRefused(message)
                seen_edge_keys.add(edge_key)
                source_rec = (existing.get("nodes") or {}).get(
                    prior_row.get("from"))
                if (not isinstance(source_rec, dict)
                        or source_rec.get("decisions") is None):
                    message = (f"resume_integrity: orphaned transition — "
                               f"source node {prior_row.get('from')!r} in "
                               f"run {run_id} has no signed checkpoint — "
                               f"resume refused")
                    _log_refusal(state_dir, message, now_fn)
                    raise RunnerRefused(message)
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
            # R5-C1: receipts authorize transitions, but resume also trusts
            # ROUTING state — every persisted decision checkpoint must carry
            # a valid kernel signature before the frontier is rebuilt from
            # it. Tampered satisfied flags, redirected destinations, or
            # resurrected pending states die here, before any token mints.
            for check_nid, check_rec in (existing.get("nodes") or {}).items():
                if (not isinstance(check_rec, dict)
                        or check_rec.get("decisions") is None):
                    continue
                checkpoint_ok = step_receipts.verify_node_checkpoint(
                    signer, department=existing["department"],
                    graph_id=existing["loop_id"],
                    graph_hash=existing["graph_hash"],
                    release_hash=existing["release_hash"],
                    run_id=existing["run_id"], node_id=check_nid,
                    record=check_rec,
                    signature=check_rec.get("checkpoint_sig"))
                if not checkpoint_ok:
                    message = (
                        f"resume_integrity: node {check_nid} decision "
                        f"checkpoint in run {run_id} fails signature "
                        f"verification — resume refused")
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
        if resumed_from is None and not resumed_without_checkpoint:
            run.log("run_created", graph_hash=loaded["graph_hash"],
                    release_hash=loaded["release_hash"])
        else:
            run.log("run_resumed",
                    prior_state=resumed_from or "unpersisted",
                    graph_hash=loaded["graph_hash"],
                    release_hash=loaded["release_hash"])

        final_state = _execute_run(
            run, subgraph, nodes, edges, loaded=loaded, signer=signer,
            step_receipts=step_receipts, rungraph=rungraph, root=root,
            env_base=env_base, now_fn=now_fn, sleep_fn=sleep_fn,
            receipt_ttl_s=receipt_ttl_s, crash_hook=crash_hook)

        if final_state in ("escalated", "killed"):
            _bridge_escalation(run, final_state)

        try:
            _export_projection(dept_dir, state_dir, subgraph, loaded, signer,
                               now_fn=now_fn, export_hook=export_hook)
        except Exception as exc:
            # R5-C4: a signing failure must not leave the PRIOR signed
            # projection in place as if it were current. Quarantine it
            # (.stale keeps the evidence) and put an unmistakably-invalid
            # failure artifact at the canonical path — verify_projection
            # reports schema_mismatch on it — plus a durable finding.
            proj_path = state_dir / "receipts" / "execution-projection.json"
            with records_lock(state_dir):
                if proj_path.exists():
                    os.replace(proj_path,
                               proj_path.with_suffix(".json.stale"))
                _atomic_write_json(proj_path, {
                    "schema": "execution-projection-export-failed",
                    "run_id": run_id,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "ts": _now_iso(now_fn)})
                _append_jsonl(state_dir / "runs.jsonl",
                              {"ts": _now_iso(now_fn),
                               "event": "projection_export_failed",
                               "run_id": run_id,
                               "reason": f"{type(exc).__name__}: {exc}"})
        return {"run_id": run_id, "state": final_state, "duplicate": False,
                "resumed": resumed_from is not None or resumed_without_checkpoint}
    finally:
        run_lock.close()


def _bridge_escalation(run: _Run, final_state: str) -> None:
    """Runner->manager escalation bridge (report + escalate only, no new
    authority). A terminal escalated/killed run appends one durable,
    manager-readable record to department state; factory/manager.py senses it
    on the next cycle, raises a breach finding, and delivers it to the
    human-in-the-loop outbox. A record failure here propagates — records are
    hard rule 5; an escalation that cannot be recorded must never look
    handled. Duplicate rows from a crash-resume window are harmless: the
    manager replays them keyed by run_id and the outbox delivery is
    fingerprint-deduplicated."""
    row = {
        "ts": _now_iso(run.now_fn),
        "event": "graph_run_escalation",
        "department": run.dept_dir.name,
        "loop_id": run.loop_id,
        "run_id": run.run_id,
        "state": final_state,
        "termination_reason": run.record.get("termination_reason"),
        "marker": "open",
    }
    with records_lock(run.state_dir):
        _append_jsonl(run.state_dir / "graph_escalations.jsonl", row)
    run.log("escalation_bridged", state=final_state,
            termination_reason=row["termination_reason"])


def _execute_run(run: _Run, subgraph: dict, nodes: dict, edges: list, *,
                 loaded: dict, signer, step_receipts, rungraph, root,
                 env_base, now_fn, sleep_fn, receipt_ttl_s,
                 crash_hook=None) -> str:
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

    def _boundary(name: str) -> None:
        """Test seam (R5-C5): a hook that raises here models process death at
        the exact persistence boundary. Production passes no hook."""
        if crash_hook is not None:
            crash_hook(name)

    def _seal(node_id: str, rec: dict) -> None:
        """Sign the node's routing checkpoint (R5-C1). Every mutation of a
        decisions-bearing record re-seals before it persists; a signing
        failure here is a broken signing plane, exactly like token issuance."""
        nonlocal final_reason
        try:
            rec["checkpoint_sig"] = step_receipts.sign_node_checkpoint(
                signer, node_id=node_id, record=rec, **identity)
        except Exception as exc:
            run.log("gate_failure", node_id=node_id,
                    why="signing_plane:checkpoint",
                    reason=f"{type(exc).__name__}: {exc}")
            final_reason = "gate_failure:signing_plane"
            raise SigningPlaneBroken(str(exc)) from exc

    def _gated_transition(*, src: str, dst, kind: str, attempt: int,
                          output_hash: str, note: str | None = None,
                          failed_check: str | None = None,
                          edge_id: str | None = None) -> str | None:
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
                node_id=src, attempt=attempt, edge=edge_id, to=dst, kind=kind,
                ttl_s=receipt_ttl_s, **identity)
            check = step_receipts.verify_step_receipt(
                token, signer=signer, now=now_fn(), output_hash=output_hash,
                consumed=consumed, node_id=src, attempt=attempt, edge=edge_id,
                to=dst, kind=kind, **identity)
        except Exception as exc:
            run.log("gate_failure", node_id=src, why=f"signing_plane:{kind}",
                    reason=f"{type(exc).__name__}: {exc}")
            final_reason = "gate_failure:signing_plane"
            raise SigningPlaneBroken(str(exc)) from exc
        if not check.ok:
            run.log("transition_blocked", node_id=src, to=dst, kind=kind,
                    reason=check.reason)
            return check.reason
        _boundary("pre_row_persist")
        row = {"from": src, "to": dst, "kind": kind, "attempt": attempt,
               "step_receipt": token,
               "step_receipt_sha256": _sha256_text(token),
               "output_sha256": output_hash,
               "ts": _now_iso(now_fn)}
        if edge_id is not None:
            row["edge"] = edge_id
        if note is not None:
            row["note"] = note
        if failed_check is not None:
            row["failed_check"] = failed_check
        run.record["transitions"].append(row)
        run.persist()
        run.log("transition", **{k: v for k, v in row.items()
                                 if k != "step_receipt"})
        _boundary("post_row_persist")
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
        _seal(src, rec)
        run.persist()
        reason = _gated_transition(src=src, dst=None, kind=kind,
                                   attempt=attempt, output_hash=out_hash,
                                   note=why, failed_check=failed_check,
                                   edge_id=decision["edge"])
        if reason is None:
            decision["state"] = "fired"
            final_reason = why
            _seal(src, rec)
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
                                       note=decision.get("why"),
                                       edge_id=decision.get("edge"))
            if reason is not None:
                result = _fire_exit(node_id, attempt, out_hash,
                                    f"verification_failed:{kind}",
                                    failed_check=reason)
                continue
            decision["state"] = "fired"
            _seal(node_id, rec)
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
                _boundary("post_mark_fired")
                queued.add(dst)
                frontier.append(dst)
        return result

    # Row/fired reconciliation (R5-C2): a crash between the transition row
    # landing and the decision flipping to fired must not mint a SECOND token
    # on resume. Rows were already reverified before the checkpoint was
    # trusted — and routing (edge/to/kind) is INSIDE each token's binding
    # (R6-S1), so the row's edge label used for matching here is
    # token-authenticated, never a bare row label. An existing row for a
    # pending decision means the decision IS fired — adopt it, re-seal, mint
    # nothing. Exactly-once per edge.
    try:
        for adopt_nid, adopt_rec in run.record["nodes"].items():
            adopted = False
            for decision in adopt_rec.get("decisions") or []:
                if (decision.get("satisfied") is not True
                        or decision.get("state") != "pending"):
                    continue
                match = next(
                    (row for row in run.record["transitions"]
                     if row.get("from") == adopt_nid
                     and row.get("edge") == decision.get("edge")), None)
                if match is not None:
                    decision["state"] = "fired"
                    adopted = True
                    run.log("row_adopted", node_id=adopt_nid,
                            edge=decision.get("edge"), to=decision.get("to"),
                            kind=decision.get("kind"))
            if adopted:
                _seal(adopt_nid, adopt_rec)
                run.persist()
    except SigningPlaneBroken:
        run.record["termination_reason"] = final_reason
        run.advance("killed", "run_killed")
        return "killed"

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
                    env_base=env_base, sleep_fn=sleep_fn, signer=signer,
                    now_fn=now_fn, crash_hook=crash_hook)
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
                    _seal(node_id, rec)
                    run.persist()  # durable SIGNED checkpoint before firing
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
                    _seal(node_id, rec)
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
                       loaded: dict, signer, *, now_fn,
                       export_hook=None) -> None:
    """Export the department's SIGNED execution projection.

    The WHOLE transaction — scan, build, sign, atomic write — runs under the
    department records fence (review F2-RACE). The projection is a single
    shared, whole-department artifact, while each run holds only its OWN
    per-run lock, so a scan performed outside the fence can be overtaken: a
    slower run would publish a copy that predates a faster run's completion
    and silently erase that run's authoritative backing — which, since
    factory/rollup.py now derives graph-run rows from this file, quarantines
    the erased run's records as graph_identity_unbacked.

    Serializing the existing whole-file export was chosen over switching to
    append-only or per-run projections: the projection's signature covers
    the entire canonical body (structure AND every run's transition
    history), and dag_supervisor-style auditors already verify exactly that
    one artifact, so per-run files would fragment a verification surface
    that PR #11 deliberately made whole, and an append-only log would need
    its own compaction and last-writer rules to answer "what is the current
    projection". Holding the fence across scan+sign costs one HMAC per run
    export and gives the property outright: whichever run writes last also
    scanned last, so the published projection always covers every run
    persisted before it — and run_state persistence takes the same fence,
    so a run that has not yet appeared is a run that has not yet committed.
    """
    projection = _load("projection", "factory/projection.py")
    rungraph = _load("rungraph", "factory/rungraph.py")
    with records_lock(state_dir):
        runs = []
        runs_root = state_dir / "graph_runs"
        if runs_root.is_dir():
            for state_path in sorted(runs_root.glob("*/run_state.json")):
                try:
                    record = _strict_loads(
                        state_path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                if record.get("loop_id") != subgraph["id"]:
                    continue
                runs.append(
                    {"run_id": record.get("run_id"),
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
            graph_hash=loaded["graph_hash"],
            release_hash=loaded["release_hash"],
            factory_version=factory_version,
            nodes=subgraph.get("nodes", []), edges=subgraph.get("edges", []),
            runs=runs, generated_at=_now_iso(now_fn))
        signed = projection.sign_projection(body, signer)
        if export_hook is not None:
            export_hook()  # test seam: fires INSIDE the fence
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
