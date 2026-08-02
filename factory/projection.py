"""Signed execution projection: the runner's exported, auditor-verifiable view.

The runner (factory/runner.py) EXPORTS this; it never verifies its own export
— that separation is the point. An independent auditor (the department's
supervisory plane, like podcast's dag_supervisor) holds the kernel signer and
calls verify_projection without ever sharing execution authority.

Shape follows the receipt discipline dag_supervisor already verifies:
a declared canonical hash the auditor recomputes (dag_hash over nodes+edges),
plus an HMAC signature over the WHOLE canonical body (structure AND per-run
transition history), plus generated_at for staleness checks on the auditor
side. Versioned via `schema`; runner-agnostic by design — any future runner
that emits this shape stays auditable by the same plane.
"""
from __future__ import annotations

import hashlib
import json

SCHEMA = "execution-projection-v1"
TELEMETRY_SCHEMA_VERSION = 1


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def build_projection(*, department, graph_id, graph_hash, release_hash,
                     factory_version, nodes, edges, runs, generated_at) -> dict:
    """Assemble the unsigned projection body. `runs` rows carry the per-run
    transition history: from/to/kind/attempt/step_receipt_sha256/ts."""
    return {
        "schema": SCHEMA,
        "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
        "department": department,
        "graph_id": graph_id,
        "graph_hash": graph_hash,
        "release_hash": release_hash,
        "factory_version": factory_version,
        "generated_at": generated_at,
        "nodes": nodes,
        "edges": edges,
        "dag_hash": canonical_hash({"nodes": nodes, "edges": edges}),
        "runs": runs,
    }


def sign_projection(body: dict, signer) -> dict:
    """Return the body with an HMAC signature over its canonical form."""
    unsigned = {k: v for k, v in body.items() if k != "signature"}
    signed = dict(unsigned)
    signed["signature"] = signer.sign(_canonical(unsigned))
    return signed


def _finding(kind: str, detail: str, severity: str = "critical") -> dict:
    return {"kind": kind, "detail": detail, "severity": severity}


def verify_projection(projection, signer) -> list[dict]:
    """Auditor-plane verification. Deterministic findings, deny-by-default:
    an unverifiable projection is findings, never a pass."""
    if not isinstance(projection, dict):
        return [_finding("schema_mismatch", "projection root must be an object")]
    if projection.get("schema") != SCHEMA:
        return [_finding(
            "schema_mismatch",
            f"expected schema {SCHEMA!r}, got {projection.get('schema')!r}")]

    findings: list[dict] = []
    signature = projection.get("signature")
    unsigned = {k: v for k, v in projection.items() if k != "signature"}
    signature_ok = False
    if isinstance(signature, str) and signature:
        try:
            signature_ok = signer.verify(_canonical(unsigned), signature)
        except Exception:
            signature_ok = False
    if not signature_ok:
        findings.append(_finding(
            "bad_signature",
            "projection signature is absent, malformed, or does not verify"))

    try:
        recomputed = canonical_hash({"nodes": projection.get("nodes"),
                                     "edges": projection.get("edges")})
    except (TypeError, ValueError):
        recomputed = "unrecomputable"
    declared = projection.get("dag_hash")
    if recomputed != declared:
        findings.append(_finding(
            "dag_hash_mismatch",
            f"declared dag_hash {declared!r} does not match recomputed "
            f"{recomputed!r}"))
    return findings
