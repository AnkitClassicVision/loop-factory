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

Consumption discipline: a step receipt is single-use PER SUCCESSOR — a
fan-out node feeds each declared successor exactly once; feeding the same
successor twice is a replay. Cross-run reuse dies on the binding (run_id),
cross-release reuse dies on the binding (release_hash, graph_hash).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
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
    """Canonical sha256 over the node's receipt JSON."""
    canonical = json.dumps(output, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


class _SuccessorScopedNonces:
    """View over a shared consumed-set that namespaces nonce membership by the
    successor being fed, giving single-use-per-successor semantics on top of
    verify_receipt's unchanged replay check."""

    def __init__(self, backing, successor: str):
        self._backing = backing
        self._successor = successor

    def _key(self, nonce) -> str:
        return f"{self._successor}\x1f{nonce}"

    def __contains__(self, nonce) -> bool:
        return self._key(nonce) in self._backing

    def add(self, nonce) -> None:
        self._backing.add(self._key(nonce))


def verify_step_receipt(token, *, signer, now, output, consumed, successor,
                        department, graph_id, graph_hash, release_hash,
                        run_id, node_id, attempt):
    """Verify a transition token against the exact step identity + output.

    `consumed` is the shared consumption store (a set, or any object with
    __contains__/add — e.g. the kernel's DurableNonceSet); `successor` scopes
    consumption so each declared out-edge target is fed exactly once.
    Returns kernel/receipts.py ReceiptCheck; any failure BLOCKS the transition.
    """
    receipts = _receipts()
    binding = step_binding(
        department=department, graph_id=graph_id, graph_hash=graph_hash,
        release_hash=release_hash, run_id=run_id, node_id=node_id,
        attempt=attempt, output=output)
    return receipts.verify_receipt(
        token, ACTION_CLASS, binding, signer=signer, now=now,
        seen_nonces=_SuccessorScopedNonces(consumed, successor))
