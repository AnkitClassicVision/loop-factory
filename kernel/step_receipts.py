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


def step_binding(*, department, graph_id, graph_hash, release_hash, run_id,
                 node_id, attempt, output) -> dict:
    return {
        "department": department,
        "graph_id": graph_id,
        "graph_hash": graph_hash,
        "release_hash": release_hash,
        "run_id": run_id,
        "node_id": node_id,
        "attempt": int(attempt),
        "output_hash": output_hash(output),
    }


def issue_step_receipt(*, signer, now, output, department, graph_id, graph_hash,
                       release_hash, run_id, node_id, attempt,
                       ttl_s=DEFAULT_TTL_S) -> str:
    receipts = _receipts()
    binding = step_binding(
        department=department, graph_id=graph_id, graph_hash=graph_hash,
        release_hash=release_hash, run_id=run_id, node_id=node_id,
        attempt=attempt, output=output)
    return receipts.issue_receipt(
        ACTION_CLASS, binding, ttl_s, signer, now, secrets.token_hex(16))


def verify_step_receipt(token, *, signer, now, output, consumed,
                        department, graph_id, graph_hash, release_hash,
                        run_id, node_id, attempt):
    """Verify a transition token against the exact step identity + output.

    `consumed` is the durable consumption store (DurableNonceStore, or any
    object with __contains__/add). A successful verify CONSUMES the token:
    single-use, period. Returns kernel/receipts.py ReceiptCheck; any failure
    BLOCKS the transition.
    """
    receipts = _receipts()
    binding = step_binding(
        department=department, graph_id=graph_id, graph_hash=graph_hash,
        release_hash=release_hash, run_id=run_id, node_id=node_id,
        attempt=attempt, output=output)
    return receipts.verify_receipt(
        token, ACTION_CLASS, binding, signer=signer, now=now,
        seen_nonces=consumed)
