"""Step receipts: HMAC-signed transition tokens for the deterministic runner.

Before this class existed, a runner step was 'proven' by plain JSON on disk —
forgeable by anything that can write a file (the audit's step-receipt gap).
A step receipt binds one completed node execution:

    (department, graph_id, graph_hash, release_hash, run_id, node_id,
     attempt, output_hash)

and is signed/verified through the SAME hardened primitives as effect
receipts (kernel/receipts.py issue_receipt/verify_receipt) — this module
extends that pattern and must never weaken the effect classes. The kernel
signing key never reaches department processes (factory/launch.py scrubs it),
so a department cannot mint its own transitions.

Consumption discipline: a step receipt is single-use PERIOD — one
consumption, any successor. A fan-out node mints one token per transition.
Consumption is DURABLE (DurableNonceStore: fsync'd append-only jsonl, the
same pattern the lock service uses for its consumed/revoked ledgers), so a
runner restart cannot reopen replay. Cross-run reuse dies on the binding
(run_id); cross-release reuse dies on the binding (release_hash, graph_hash).

Canonical-JSON policy: output hashes are canonical (sorted keys, tight
separators) and REJECT non-finite numbers — NaN/Inf have no canonical JSON
form, so they can neither be hashed nor signed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import secrets
import sys

_KERNEL_DIR = pathlib.Path(__file__).resolve().parent


def _receipts():
    name = "step_receipts_base"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _KERNEL_DIR / "receipts.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ACTION_CLASS = "graph_step"
DEFAULT_TTL_S = 3600  # a transition token lives for one run window, not forever


def output_hash(output) -> str:
    """Canonical sha256 over the node's receipt JSON. Raises ValueError on
    non-finite numbers — no canonical form, nothing to sign."""
    canonical = json.dumps(output, sort_keys=True, separators=(",", ":"),
                           allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DurableNonceStore:
    """Append-only fsync'd consumption ledger, drop-in for verify_receipt's
    seen_nonces. Same discipline as the lock service's durable ledgers: the
    consumption persists BEFORE the caller proceeds, a reload keeps it, and a
    torn trailing line from a crashed write is skipped (that consumption never
    durably committed, so its transition never happened)."""

    def __init__(self, path):
        self._path = pathlib.Path(path)
        self._mem: set = set()
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self._mem.add(json.loads(line)["nonce"])
                except (ValueError, KeyError, TypeError):
                    continue

    def __contains__(self, nonce) -> bool:
        return nonce in self._mem

    def add(self, nonce) -> None:
        if nonce in self._mem:
            return
        self._mem.add(nonce)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"nonce": nonce}) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def _resolve_output_hash(output, output_hash_value):
    """Exactly one of (output, output_hash) — the binding carries only the
    hash either way, so hash-only callers (auditors reverifying from persisted
    rows) get the same attestation as body-holding callers (the runner)."""
    if (output is None) == (output_hash_value is None):
        raise ValueError("pass exactly one of output= or output_hash=")
    if output_hash_value is not None:
        return output_hash_value
    return output_hash(output)


def step_binding(*, department, graph_id, graph_hash, release_hash, run_id,
                 node_id, attempt, output=None, output_hash=None) -> dict:
    return {
        "department": department,
        "graph_id": graph_id,
        "graph_hash": graph_hash,
        "release_hash": release_hash,
        "run_id": run_id,
        "node_id": node_id,
        "attempt": int(attempt),
        "output_hash": _resolve_output_hash(output, output_hash),
    }


def issue_step_receipt(*, signer, now, department, graph_id, graph_hash,
                       release_hash, run_id, node_id, attempt, output=None,
                       output_hash=None, ttl_s=DEFAULT_TTL_S) -> str:
    receipts = _receipts()
    binding = step_binding(
        department=department, graph_id=graph_id, graph_hash=graph_hash,
        release_hash=release_hash, run_id=run_id, node_id=node_id,
        attempt=attempt, output=output, output_hash=output_hash)
    return receipts.issue_receipt(
        ACTION_CLASS, binding, ttl_s, signer, now, secrets.token_hex(16))


def verify_step_receipt(token, *, signer, now, consumed, department, graph_id,
                        graph_hash, release_hash, run_id, node_id, attempt,
                        output=None, output_hash=None):
    """Verify a transition token against the exact step identity + output
    (by body, or by its persisted canonical hash — same attestation).

    `consumed` is the durable consumption store (DurableNonceStore, or any
    object with __contains__/add). A successful verify CONSUMES the token:
    single-use, period. Returns kernel/receipts.py ReceiptCheck; any failure
    BLOCKS the transition.
    """
    receipts = _receipts()
    binding = step_binding(
        department=department, graph_id=graph_id, graph_hash=graph_hash,
        release_hash=release_hash, run_id=run_id, node_id=node_id,
        attempt=attempt, output=output, output_hash=output_hash)
    return receipts.verify_receipt(
        token, ACTION_CLASS, binding, signer=signer, now=now,
        seen_nonces=consumed)


def _checkpoint_material(*, department, graph_id, graph_hash, release_hash,
                         run_id, node_id, record) -> bytes:
    """Canonical bytes of one node's ROUTING checkpoint under the run
    identity: state, attempts, output hash, and the full edge-decision list
    (source, edge identity, destination, kind, predicate result, fired
    state). The signature field itself is never part of the material."""
    material = {
        "department": department,
        "graph_id": graph_id,
        "graph_hash": graph_hash,
        "release_hash": release_hash,
        "run_id": run_id,
        "node_id": node_id,
        "state": record.get("state"),
        "attempts": record.get("attempts"),
        "output_hash": record.get("output_hash"),
        "decisions": record.get("decisions"),
    }
    return json.dumps(material, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sign_node_checkpoint(signer, *, department, graph_id, graph_hash,
                         release_hash, run_id, node_id, record) -> str:
    """Sign a node's decision checkpoint. Receipts authorize TRANSITIONS;
    this authenticates the ROUTING STATE a resume would otherwise trust
    unsigned — the same kernel signer covers both planes."""
    return signer.sign(_checkpoint_material(
        department=department, graph_id=graph_id, graph_hash=graph_hash,
        release_hash=release_hash, run_id=run_id, node_id=node_id,
        record=record))


def verify_node_checkpoint(signer, *, department, graph_id, graph_hash,
                           release_hash, run_id, node_id, record,
                           signature) -> bool:
    """Verify a persisted decision checkpoint. Absent, malformed, or
    non-matching signatures are False — the caller must refuse resume."""
    if not isinstance(signature, str) or not signature:
        return False
    try:
        return signer.verify(_checkpoint_material(
            department=department, graph_id=graph_id, graph_hash=graph_hash,
            release_hash=release_hash, run_id=run_id, node_id=node_id,
            record=record), signature)
    except Exception:
        return False


def reverify_transition(row, *, record, signer, now):
    """Auditor-plane reverification from PERSISTED materials only: a run
    record (run_state.json — carries department/loop_id/graph_hash/
    release_hash/run_id) plus one transition row (carries the full signed
    token, the source node, the attempt, and the canonical output hash).

    Limitation of hash rebinding (accepted, round-4 C5 — verbatim): attests
    receipt authenticity and binding integrity under the recorded identity;
    does NOT prove output content, schema conformance, or predicate
    correctness — no body exists to recompute.

    Consumption is deliberately NOT touched: auditors verify, they never
    transition, so a throwaway nonce set is used. Verdict semantics: verify
    checks the signature and binding BEFORE expiry, so reason 'expired' on a
    historical token means authentic-but-stale (expected on late audits),
    while 'signature'/'binding' mean forgery or tampering.
    """
    return verify_step_receipt(
        row["step_receipt"], signer=signer, now=now,
        output_hash=row["output_sha256"], consumed=set(),
        department=record["department"], graph_id=record["loop_id"],
        graph_hash=record["graph_hash"], release_hash=record["release_hash"],
        run_id=record["run_id"], node_id=row["from"], attempt=row["attempt"])
