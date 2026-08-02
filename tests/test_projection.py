"""Signed execution projection: what an INDEPENDENT auditor verifies.

The runner exports a versioned, canonically-hashed, HMAC-signed projection of
(nodes, edges, per-run transition history). An auditor holding the kernel key
verifies it without sharing execution authority — the shape mirrors the
receipt the podcast dag_supervisor already verifies (declared hash +
recomputable canonical hash + staleness), plus a signature. The runner never
audits its own projection; verify_projection exists for the auditor plane.
"""
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = _load("projection_kernel_receipts", "kernel/receipts.py")
PJ = _load("projection", "factory/projection.py")


NODES = [{"id": "N1", "impl": "runtime/sense.py", "action_class": "observe"},
         {"id": "N2", "impl": "runtime/record.py", "action_class": "observe"}]
EDGES = [{"from": "N1", "to": "N2", "kind": "normal", "when": "true"},
         {"from": "N2", "kind": "terminal", "when": "true"}]
RUNS = [{
    "run_id": "SG-RUN-abc",
    "state": "done",
    "transitions": [
        {"from": "N1", "to": "N2", "kind": "normal", "attempt": 1,
         "step_receipt_sha256": "c" * 64, "ts": "2026-08-02T00:00:00+00:00"},
    ],
}]


def _projection(key="k"):
    body = PJ.build_projection(
        department="demo", graph_id="SG-RUN", graph_hash="a" * 64,
        release_hash="feedfeedfeedfeed",
        factory_version={"graph_schema_version": 2, "runner_version": "2.0.0",
                         "telemetry_schema_version": 1,
                         "template_set_hash": "b" * 16},
        nodes=NODES, edges=EDGES, runs=RUNS,
        generated_at="2026-08-02T00:00:01+00:00")
    return PJ.sign_projection(body, R.LocalSigner(key=key))


def test_signed_projection_verifies_clean():
    assert PJ.verify_projection(_projection(), R.LocalSigner(key="k")) == []


def test_projection_declares_versioned_schema_and_transitions():
    proj = _projection()
    assert proj["schema"] == PJ.SCHEMA
    assert proj["runs"][0]["transitions"][0]["step_receipt_sha256"] == "c" * 64


def test_tampered_structure_detected():
    proj = _projection()
    proj["nodes"].append({"id": "N3", "impl": "runtime/evil.py"})
    findings = PJ.verify_projection(proj, R.LocalSigner(key="k"))
    kinds = {f["kind"] for f in findings}
    assert "dag_hash_mismatch" in kinds
    assert "bad_signature" in kinds


def test_tampered_transition_history_detected():
    proj = _projection()
    proj["runs"][0]["transitions"][0]["step_receipt_sha256"] = "d" * 64
    findings = PJ.verify_projection(proj, R.LocalSigner(key="k"))
    assert any(f["kind"] == "bad_signature" for f in findings)


def test_wrong_key_detected():
    findings = PJ.verify_projection(_projection(key="attacker"),
                                    R.LocalSigner(key="k"))
    assert any(f["kind"] == "bad_signature" for f in findings)


def test_schema_mismatch_detected():
    proj = _projection()
    proj["schema"] = "something-else"
    findings = PJ.verify_projection(proj, R.LocalSigner(key="k"))
    assert any(f["kind"] == "schema_mismatch" for f in findings)


def test_unsigned_projection_blocks():
    body = dict(_projection())
    del body["signature"]
    findings = PJ.verify_projection(body, R.LocalSigner(key="k"))
    assert any(f["kind"] == "bad_signature" for f in findings)


def test_non_finite_values_cannot_be_hashed_or_signed():
    import pytest
    with pytest.raises(ValueError):
        PJ.build_projection(
            department="demo", graph_id="SG-RUN", graph_hash="a" * 64,
            release_hash="feedfeedfeedfeed", factory_version={},
            nodes=[{"id": "N1", "weight": float("nan")}], edges=[], runs=[],
            generated_at="2026-08-02T00:00:01+00:00")
    with pytest.raises(ValueError):
        PJ.sign_projection({"schema": PJ.SCHEMA, "x": float("inf")},
                           R.LocalSigner(key="k"))
