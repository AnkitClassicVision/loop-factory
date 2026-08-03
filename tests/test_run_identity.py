"""R1 — unified execution identity across the four record streams.

One logical graph execution is joinable by a single correlation key: the
graph runner's run_id. Identity travels ONLY as a runner-minted SIGNED
context token (OE_GRAPH_CONTEXT, kernel/graph_context.py) bound to
(department, run_id, node, attempt) with a TTL — raw env strings are never
trusted for identity (cross-review B1: plain env identity was spoofable).
Appenders take identity from the token payload, refuse malformed/expired
tokens, refuse signature failures wherever the kernel key is resolvable, and
enforce node attribution; in the keyless confined node the runner re-verifies
every appended identity claim before any transition fires.
"""
import base64
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

from factory import rollup, runrecord, scores
from kernel import capabilities, graph_context, lock_service, receipts


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = _load("identity_kernel_receipts", "kernel/receipts.py")
REL = _load("identity_release", "factory/release.py")
RUN = _load("identity_runner", "factory/runner.py")

SIGNER = R.LocalSigner(key="test-key")
KERNEL_KEY = "kernel-test-key"
CTX_ENV = "OE_GRAPH_CONTEXT"


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _mint(run_id="SG-RUN-ctx", node="n1", *, key=KERNEL_KEY, attempt=1,
          ttl_s=3600, department="demo"):
    return graph_context.issue_context(
        signer=receipts.LocalSigner(key=key), now=time.time(),
        department=department, run_id=run_id, node=node, attempt=attempt,
        ttl_s=ttl_s)


def _install(monkeypatch, token, *, kernel_key=KERNEL_KEY):
    monkeypatch.setenv(CTX_ENV, token)
    if kernel_key is None:
        monkeypatch.delenv("OE_KERNEL_SIGNING_KEY", raising=False)
    else:
        monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", kernel_key)


def _tamper(token, **overrides):
    """Rewrite the payload, keep the (now wrong) signature."""
    encoded, sig = token.rsplit(".", 1)
    padding = "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
    payload.update(overrides)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode() + "." + sig


# --------------------------------------------------------------------------- #
# The env-name contract is shared across modules; drift would silently break
# the identity chain, so the constant must agree everywhere it exists.
# --------------------------------------------------------------------------- #

def test_graph_context_env_name_agrees_across_modules():
    assert graph_context.GRAPH_CONTEXT_ENV == CTX_ENV
    assert capabilities.GRAPH_CONTEXT_ENV == CTX_ENV
    assert RUN.GRAPH_CONTEXT_ENV == CTX_ENV


# --------------------------------------------------------------------------- #
# graph_context token primitives
# --------------------------------------------------------------------------- #

def test_context_round_trips_and_verifies():
    token = _mint()
    signer = receipts.LocalSigner(key=KERNEL_KEY)
    payload = graph_context.verify_context(token, signer=signer, now=time.time())
    assert payload["run_id"] == "SG-RUN-ctx"
    assert payload["node"] == "n1"
    assert payload["department"] == "demo"
    assert payload["attempt"] == 1


def test_context_rejects_forged_signature_and_tampered_payload():
    signer = receipts.LocalSigner(key=KERNEL_KEY)
    with pytest.raises(graph_context.ContextInvalid, match="signature"):
        graph_context.verify_context(
            _mint(key="wrong-key"), signer=signer, now=time.time())
    with pytest.raises(graph_context.ContextInvalid, match="signature"):
        graph_context.verify_context(
            _tamper(_mint(), run_id="SG-RUN-forged"), signer=signer,
            now=time.time())


def test_context_rejects_expired_and_malformed():
    expired = _tamper(_mint(), exp=time.time() - 10)
    with pytest.raises(graph_context.ContextInvalid, match="expired"):
        graph_context.parse_context(expired, now=time.time())
    with pytest.raises(graph_context.ContextInvalid, match="malformed"):
        graph_context.parse_context("not-a-token", now=time.time())


# --------------------------------------------------------------------------- #
# runrecord (#13 stream): identity from the verified token only
# --------------------------------------------------------------------------- #

def test_runrecord_graph_run_id_round_trips(tmp_path):
    runrecord.emit_record(
        tmp_path, department="demo", node="n1", status="ok",
        graph_run_id="SG-RUN-abc123")
    rows = _read_jsonl(tmp_path / "runs-v2.jsonl")
    assert rows[0]["graph_run_id"] == "SG-RUN-abc123"
    runrecord.validate_record(rows[0])


def test_runrecord_without_context_stays_backward_compatible(tmp_path):
    """Existing podcast emitters keep working: no token, no field, no change."""
    runrecord.emit_record(tmp_path, department="demo", node="n1", status="ok")
    rows = _read_jsonl(tmp_path / "runs-v2.jsonl")
    assert "graph_run_id" not in rows[0]
    runrecord.validate_record(rows[0])


def test_runrecord_rejects_unsafe_graph_run_id(tmp_path):
    with pytest.raises(ValueError):
        runrecord.emit_record(
            tmp_path, department="demo", node="n1", status="ok",
            graph_run_id="bad value with spaces")
    with pytest.raises(ValueError):
        runrecord.emit_record(
            tmp_path, department="demo", node="n1", status="ok",
            graph_run_id=7)
    assert not (tmp_path / "runs-v2.jsonl").exists()


def test_runrecord_under_runner_requires_graph_run_id(tmp_path, monkeypatch):
    _install(monkeypatch, _mint())
    record = runrecord.build_record(
        schema=runrecord.SCHEMA, rev=2, run_id="wrapper-1", department="demo",
        node="n1", epoch=0, ts="2026-08-02T00:00:00+00:00", attempt=1,
        round=None, release=None, trigger=None, engine=None, model=None,
        auth_class=None, usage=None, cost=None, duration_ms=None, status="ok",
        errors=[], artifacts=[], receipts=[], evaluator=None, approval=None,
        external_actions_taken=0)
    with pytest.raises(ValueError, match="graph_run_id"):
        runrecord.append_record(tmp_path, record)
    assert not (tmp_path / "runs-v2.jsonl").exists()


def test_runrecord_rejects_mismatched_graph_run_id(tmp_path, monkeypatch):
    _install(monkeypatch, _mint(run_id="SG-RUN-truth"))
    with pytest.raises(ValueError, match="graph_run_id"):
        runrecord.emit_record(
            tmp_path, department="demo", node="n1", status="ok",
            graph_run_id="SG-RUN-forged")
    assert not (tmp_path / "runs-v2.jsonl").exists()


def test_emit_record_defaults_identity_from_context(tmp_path, monkeypatch):
    _install(monkeypatch, _mint(run_id="SG-RUN-token"))
    runrecord.emit_record(tmp_path, department="demo", node="n1", status="ok")
    rows = _read_jsonl(tmp_path / "runs-v2.jsonl")
    assert rows[0]["graph_run_id"] == "SG-RUN-token"


def test_runrecord_refuses_forged_or_tampered_token(tmp_path, monkeypatch):
    """B1 regression: a rewritten env identity refuses at append time
    wherever the kernel key is resolvable."""
    _install(monkeypatch, _mint(key="node-forged-key"))
    with pytest.raises(ValueError, match="signature"):
        runrecord.emit_record(tmp_path, department="demo", node="n1",
                              status="ok")
    _install(monkeypatch, _tamper(_mint(), run_id="SG-RUN-forged"))
    with pytest.raises(ValueError, match="signature"):
        runrecord.emit_record(tmp_path, department="demo", node="n1",
                              status="ok")
    assert not (tmp_path / "runs-v2.jsonl").exists()


def test_runrecord_refuses_expired_token(tmp_path, monkeypatch):
    _install(monkeypatch, _tamper(_mint(), exp=time.time() - 10))
    with pytest.raises(ValueError, match="expired"):
        runrecord.emit_record(tmp_path, department="demo", node="n1",
                              status="ok")
    assert not (tmp_path / "runs-v2.jsonl").exists()


def test_runrecord_enforces_node_attribution(tmp_path, monkeypatch):
    """B1 regression: a record claiming a different node than the verified
    token's node refuses — the attribution hole is closed."""
    _install(monkeypatch, _mint(node="n1"))
    with pytest.raises(ValueError, match="node"):
        runrecord.emit_record(tmp_path, department="demo", node="n2",
                              status="ok")
    assert not (tmp_path / "runs-v2.jsonl").exists()


def test_runrecord_keyless_plane_takes_identity_from_payload(
        tmp_path, monkeypatch):
    """Confined node: no kernel key, so the signature CANNOT be checked
    in-process (symmetric verify = mint). Identity still comes only from the
    token payload, and the runner re-verifies every appended claim before a
    transition fires (see test_runner_fails_node_on_forged_identity_claim)."""
    _install(monkeypatch, _mint(run_id="SG-RUN-keyless"), kernel_key=None)
    runrecord.emit_record(tmp_path, department="demo", node="n1", status="ok")
    rows = _read_jsonl(tmp_path / "runs-v2.jsonl")
    assert rows[0]["graph_run_id"] == "SG-RUN-keyless"


# --------------------------------------------------------------------------- #
# kernel telemetry (#12 stream)
# --------------------------------------------------------------------------- #

def _service(tmp_path):
    return lock_service.LockService(
        receipts.LocalSigner(key="test"),
        budget_ledger=tmp_path / "state" / "kernel" / "budget.jsonl",
        freq_ledger=tmp_path / "state" / "kernel" / "frequency.jsonl",
        nonce_ledger=tmp_path / "state" / "kernel" / "nonces.jsonl",
        telemetry_path=tmp_path / "state" / "telemetry.jsonl",
    )


def test_model_telemetry_row_carries_explicit_graph_run_id(tmp_path):
    service = _service(tmp_path)
    issued = service.request_model("sanitized", sanitized=True)
    service.call_model(
        "sanitized", issued["receipt"], runner=lambda _p: "ok",
        node="n1", graph_run_id="SG-RUN-explicit")
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[0]
    assert row["loopfactory.graph_run_id"] == "SG-RUN-explicit"
    assert row["loopfactory.node"] == "n1"


def test_model_telemetry_defaults_identity_from_context(tmp_path, monkeypatch):
    _install(monkeypatch, _mint(run_id="SG-RUN-token", node="n-token"))
    service = _service(tmp_path)
    issued = service.request_model("sanitized", sanitized=True)
    service.call_model("sanitized", issued["receipt"], runner=lambda _p: "ok")
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[0]
    assert row["loopfactory.graph_run_id"] == "SG-RUN-token"
    assert row["loopfactory.node"] == "n-token"


def test_model_telemetry_mismatched_graph_run_id_fails_closed(
        tmp_path, monkeypatch):
    _install(monkeypatch, _mint(run_id="SG-RUN-truth"))
    service = _service(tmp_path)
    issued = service.request_model("sanitized", sanitized=True)
    invoked = []

    def runner(_prompt):
        invoked.append(True)
        return "never"

    with pytest.raises(lock_service.LockServiceDown):
        service.call_model(
            "sanitized", issued["receipt"], runner=runner,
            graph_run_id="SG-RUN-forged")
    assert invoked == []  # refused BEFORE provider invocation
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[0]
    assert row["error.type"] == "ValueError"
    # the runner-minted identity is authoritative in the recorded row
    assert row["loopfactory.graph_run_id"] == "SG-RUN-truth"


def test_model_telemetry_mismatched_node_fails_closed(tmp_path, monkeypatch):
    """B1 regression: explicit node must match the token's node."""
    _install(monkeypatch, _mint(node="n-token"))
    service = _service(tmp_path)
    issued = service.request_model("sanitized", sanitized=True)
    invoked = []
    with pytest.raises(lock_service.LockServiceDown):
        service.call_model(
            "sanitized", issued["receipt"],
            runner=lambda _p: invoked.append(True) or "never", node="n-forged")
    assert invoked == []
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[0]
    assert row["error.type"] == "ValueError"


def test_model_telemetry_forged_token_fails_closed(tmp_path, monkeypatch):
    _install(monkeypatch, _tamper(_mint(), run_id="SG-RUN-forged"))
    service = _service(tmp_path)
    issued = service.request_model("sanitized", sanitized=True)
    with pytest.raises(lock_service.LockServiceDown):
        service.call_model("sanitized", issued["receipt"],
                           runner=lambda _p: "never")
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[0]
    assert row["error.type"] == "ContextInvalid"


def test_model_telemetry_without_context_stays_null(tmp_path):
    service = _service(tmp_path)
    issued = service.request_model("sanitized", sanitized=True)
    service.call_model("sanitized", issued["receipt"], runner=lambda _p: "ok")
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[0]
    assert row["loopfactory.graph_run_id"] is None


# --------------------------------------------------------------------------- #
# scores (#12 stream)
# --------------------------------------------------------------------------- #

def _score_kwargs(target_ref):
    return dict(
        name="qa", value=1.0, label="pass", explanation="ok", source="script",
        judge_model=None, config_version="v1", target_ref=target_ref)


def test_score_target_ref_accepts_graph_run_id(tmp_path):
    record = scores.build_score(**_score_kwargs({
        "run_id": None, "step_id": None, "node": "n1", "department": "demo",
        "graph_run_id": "SG-RUN-abc"}))
    scores.append_score(tmp_path, record)
    row = _read_jsonl(tmp_path / "scores.jsonl")[0]
    assert row["target_ref"]["graph_run_id"] == "SG-RUN-abc"


def test_score_target_ref_without_graph_run_id_stays_backward_compatible(
        tmp_path):
    record = scores.build_score(**_score_kwargs({
        "run_id": None, "step_id": None, "node": "n1", "department": "demo"}))
    scores.append_score(tmp_path, record)
    assert "graph_run_id" not in _read_jsonl(tmp_path / "scores.jsonl")[0]["target_ref"]


def test_build_score_defaults_graph_run_id_from_context(monkeypatch):
    _install(monkeypatch, _mint(run_id="SG-RUN-token"))
    record = scores.build_score(**_score_kwargs({
        "run_id": None, "step_id": None, "node": "n1", "department": "demo"}))
    assert record["target_ref"]["graph_run_id"] == "SG-RUN-token"


def test_append_score_under_runner_requires_matching_graph_run_id(
        tmp_path, monkeypatch):
    record = scores.build_score(**_score_kwargs({
        "run_id": None, "step_id": None, "node": "n1", "department": "demo",
        "graph_run_id": "SG-RUN-forged"}))
    _install(monkeypatch, _mint(run_id="SG-RUN-truth"))
    with pytest.raises(ValueError, match="graph_run_id"):
        scores.append_score(tmp_path, record)
    bare = scores.validate_score({**record, "target_ref": {
        "run_id": None, "step_id": None, "node": "n1", "department": "demo"}})
    with pytest.raises(ValueError, match="graph_run_id"):
        scores.append_score(tmp_path, bare)
    assert not (tmp_path / "scores.jsonl").exists()


def test_append_score_refuses_forged_token(tmp_path, monkeypatch):
    _install(monkeypatch, _mint(key="node-forged-key"))
    with pytest.raises(ValueError, match="signature"):
        scores.append_score(tmp_path, scores.build_score(**_score_kwargs({
            "run_id": None, "step_id": None, "node": "n1",
            "department": "demo"})))
    assert not (tmp_path / "scores.jsonl").exists()


def test_score_target_node_may_differ_from_context_node(tmp_path, monkeypatch):
    """target_ref.node is the SUBJECT of the score, not the emitter — a judge
    node scoring another node's output is legitimate, so only graph_run_id is
    enforced here."""
    _install(monkeypatch, _mint(run_id="SG-RUN-token", node="judge"))
    record = scores.build_score(**_score_kwargs({
        "run_id": None, "step_id": None, "node": "judged-node",
        "department": "demo"}))
    scores.append_score(tmp_path, record)
    row = _read_jsonl(tmp_path / "scores.jsonl")[0]
    assert row["target_ref"]["node"] == "judged-node"
    assert row["target_ref"]["graph_run_id"] == "SG-RUN-token"


# --------------------------------------------------------------------------- #
# rollup (#12 store): join key + bumped schema marker
# --------------------------------------------------------------------------- #

def test_rollup_schema_marker_bumped_for_new_columns():
    assert rollup.SCHEMA_VERSION == "rollup/v2"


def test_rollup_carries_graph_run_id_columns(tmp_path):
    state = tmp_path / "departments" / "demo" / "state"
    state.mkdir(parents=True)
    (state / "STATE.json").write_text(
        json.dumps({"epoch": 0, "status": "ok", "ok": True}), encoding="utf-8")
    gid = "SG-RUN-" + "a" * 8
    (state / "runs.jsonl").write_text(json.dumps({
        "ts": "2026-08-02T00:00:00+00:00", "event": "run_done",
        "run_id": gid, "graph_run_id": gid, "loop_id": "SG-RUN",
    }) + "\n", encoding="utf-8")
    (state / "runs-v2.jsonl").write_text(json.dumps({
        "schema": "run-record/v2", "rev": 2, "run_id": "wrapper-1",
        "graph_run_id": gid, "department": "demo", "node": "n1", "epoch": 0,
        "ts": "2026-08-02T00:00:01+00:00", "attempt": 1, "round": None,
        "release": None, "trigger": None, "engine": None, "model": None,
        "auth_class": None, "usage": None, "cost": None, "duration_ms": None,
        "status": "ok", "errors": [], "artifacts": [], "receipts": [],
        "evaluator": None, "approval": None, "external_actions_taken": 0,
    }) + "\n", encoding="utf-8")
    (state / "telemetry.jsonl").write_text(json.dumps({
        "schema_version": "step-telemetry/v1",
        "ts": "2026-08-02T00:00:02+00:00", "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": None, "gen_ai.request.model": None,
        "gen_ai.response.model": None, "gen_ai.usage.input_tokens": None,
        "gen_ai.usage.output_tokens": None,
        "gen_ai.response.finish_reasons": None, "duration_ms": 1,
        "error.type": None, "loopfactory.cost_usd": None,
        "loopfactory.auth.route": "blocked", "loopfactory.engine": None,
        "loopfactory.price.schema_version": None,
        "loopfactory.price.effective_date": None,
        "loopfactory.department": "demo", "loopfactory.run_id": None,
        "loopfactory.graph_run_id": gid, "loopfactory.step_id": None,
        "loopfactory.node": "n1", "loopfactory.telemetry.source": "legacy_null",
        "estimated": False,
    }) + "\n", encoding="utf-8")
    (state / "scores.jsonl").write_text(json.dumps({
        "gen_ai.evaluation.name": "qa", "gen_ai.evaluation.score.value": 1.0,
        "gen_ai.evaluation.score.label": "pass",
        "gen_ai.evaluation.explanation": "ok", "source": "script",
        "judge_model": None, "config_version": "v1",
        "target_ref": {"run_id": None, "step_id": None, "node": "n1",
                       "department": "demo", "graph_run_id": gid},
        "ts": "2026-08-02T00:00:03+00:00",
        "schema_version": "score-record/v1",
    }) + "\n", encoding="utf-8")

    result = rollup.rebuild(tmp_path)
    assert result["complete"], result
    assert result["schema_version"] == "rollup/v2"
    bundle = rollup.graph_run_bundle(result["database"], gid)
    assert {row["run_id"] for row in bundle["run"]} == {gid, "wrapper-1"}
    assert len(bundle["step_telemetry"]) == 1
    assert bundle["step_telemetry"][0]["graph_run_id"] == gid
    assert len(bundle["score"]) == 1
    assert bundle["score"][0]["graph_run_id"] == gid
    connection = sqlite3.connect(bundle and result["database"])
    try:
        marker = connection.execute(
            "SELECT schema_version FROM department").fetchone()[0]
    finally:
        connection.close()
    assert marker == "rollup/v2"


# --------------------------------------------------------------------------- #
# runner: a SIGNED context is injected into every node process
# --------------------------------------------------------------------------- #

def _node(node_id, impl, on_fail="escalate", max_retries=0):
    return {
        "id": node_id,
        "impl": impl,
        "runtime_mode": "script",
        "action_class": "observe",
        "inputs": {"type": "object"},
        "outputs": {"type": "object", "required": ["status"],
                    "properties": {"status": {"type": "string"}}},
        "receipt_schema": {"type": "object", "required": ["status"],
                           "properties": {"status": {"type": "string"}}},
        "failure_policy": {"max_retries": max_retries, "backoff_s": 0,
                           "on_fail": on_fail},
        "concept_ref": "C1",
        "interview_ref": "Q1",
    }


def _one_node_manifest():
    return {
        "schema_version": 2,
        "subgraphs": [{
            "id": "SG-RUN",
            "concept_refs": ["C1"],
            "entry": "N1",
            "nodes": [_node("N1", "runtime/sense.py")],
            "edges": [{"from": "N1", "kind": "terminal",
                       "when": "receipt.status == 'ok'"}],
        }],
    }


def _make_dept(tmp_path, scripts, manifest):
    dept = tmp_path / "departments" / "demo"
    (dept / "runtime").mkdir(parents=True)
    (dept / "charter.yaml").write_text(
        "department: demo\nowner: test-owner\nautonomy_state: shadow\n"
        "immutable_safety_invariants:\n  heal_may_not_modify: [x]\n",
        encoding="utf-8")
    for name, src in scripts.items():
        (dept / "runtime" / name).write_text(src, encoding="utf-8")
    (dept / "subgraphs.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    h = REL.pin_release(dept, dept / "releases", source_ref="testsha")
    REL.flip_current(dept / "releases", h)
    return dept


ENV_CAPTURE = """\
import base64, json, os, pathlib
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
token = os.environ.get("OE_GRAPH_CONTEXT", "")
encoded = token.rsplit(".", 1)[0]
payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
(state / "envcap.json").write_text(json.dumps({
    "payload": payload,
    "legacy_run_env": os.environ.get("OE_GRAPH_RUN_ID"),
    "kernel_key_leaked": "OE_KERNEL_SIGNING_KEY" in os.environ,
}), encoding="utf-8")
print(json.dumps({"status": "ok"}))
"""


def test_runner_injects_signed_context_into_node_env(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": ENV_CAPTURE}, _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "done"
    captured = json.loads(
        (dept / "state" / "envcap.json").read_text(encoding="utf-8"))
    payload = captured["payload"]
    assert payload["schema"] == graph_context.SCHEMA
    assert payload["run_id"] == result["run_id"]
    assert payload["node"] == "N1"
    assert payload["department"] == "demo"
    assert payload["attempt"] == 1
    # raw identity env strings are gone, and the signing key never leaks
    assert captured["legacy_run_env"] is None
    assert captured["kernel_key_leaked"] is False


def test_runner_context_verifies_under_the_runner_signer(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": ENV_CAPTURE}, _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    tokens = [row["graph_context"] for row in
              _read_jsonl(dept / "state" / "runs.jsonl")
              if row.get("event") == "node_context_issued"]
    assert tokens
    payload = graph_context.verify_context(
        tokens[0], signer=SIGNER, now=time.time())
    assert payload["run_id"] == result["run_id"]


def test_runner_records_carry_graph_run_id(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": ENV_CAPTURE}, _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    rows = _read_jsonl(dept / "state" / "runs.jsonl")
    stamped = [r for r in rows if r.get("run_id") == result["run_id"]]
    assert stamped and all(
        r.get("graph_run_id") == result["run_id"] for r in stamped)


FORGING_NODE = """\
import base64, json, os, pathlib, sys
sys.path.insert(0, {root!r})
from factory import runrecord

state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)

# The spoof probe: rewrite our own env identity. We cannot re-sign (no kernel
# key in here), so we tamper the payload and keep a junk signature — the
# keyless appender can only parse, so the forged row lands...
token = os.environ["OE_GRAPH_CONTEXT"]
encoded = token.rsplit(".", 1)[0]
payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
payload["node"] = "N-forged"
raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
forged = base64.urlsafe_b64encode(raw).rstrip(b"=").decode() + ".AAAA"
os.environ["OE_GRAPH_CONTEXT"] = forged

runrecord.emit_record(state, department="demo", node="N-forged", status="ok")
print(json.dumps({{"status": "ok"}}))
"""


def test_runner_fails_node_on_forged_identity_claim(tmp_path):
    """B1 regression (keyless plane): the node forges its context, the row
    lands, and the RUNNER — the trusted verifier — fails the node before any
    transition fires. The forgery escalates instead of advancing."""
    dept = _make_dept(
        tmp_path, {"sense.py": FORGING_NODE.format(root=str(ROOT))},
        _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "escalated"
    run_state = json.loads(
        (dept / "state" / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))
    assert "identity" in run_state["nodes"]["N1"]["reason"]
    # the ONLY receipt-bearing row is the escalation exit — nothing advanced
    assert [(t["from"], t["to"], t["kind"]) for t in run_state["transitions"]] \
        == [("N1", None, "escalation")]


# --------------------------------------------------------------------------- #
# ACCEPTANCE: one synthetic graph execution -> one joined record set across
# runs.jsonl, runs-v2.jsonl, telemetry.jsonl, scores.jsonl, queryable by the
# single graph run_id in rollup.sqlite3.
# --------------------------------------------------------------------------- #

EMITTER_NODE = """\
import json, pathlib, sys
sys.path.insert(0, {root!r})
from factory import runrecord, scores
from kernel import lock_service, receipts

state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)

# 1) #13 wrapper summary — identity comes from the runner-signed context
runrecord.emit_record(state, department="demo", node="N1", status="ok")

# 2) #12 telemetry — identity comes from the runner-signed context
service = lock_service.LockService(
    receipts.LocalSigner(key="node-key"),
    budget_ledger=state / "kernel" / "budget.jsonl",
    freq_ledger=state / "kernel" / "frequency.jsonl",
    nonce_ledger=state / "kernel" / "nonces.jsonl",
    telemetry_path=state / "telemetry.jsonl",
)
issued = service.request_model("sanitized", sanitized=True)
service.call_model("sanitized", issued["receipt"], runner=lambda _p: "ok")

# 3) #12 score — identity comes from the runner-signed context
scores.append_score(state, scores.build_score(
    name="qa", value=1.0, label="pass", explanation="ok", source="script",
    judge_model=None, config_version="v1",
    target_ref={{"run_id": None, "step_id": None, "node": "N1",
                 "department": "demo"}}))

print(json.dumps({{"status": "ok"}}))
"""


def test_one_graph_execution_yields_single_joined_record_set(tmp_path):
    dept = _make_dept(
        tmp_path, {"sense.py": EMITTER_NODE.format(root=str(ROOT))},
        _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "done"
    gid = result["run_id"]
    state = dept / "state"

    # every stream carries the SAME graph run id
    v2 = _read_jsonl(state / "runs-v2.jsonl")
    assert len(v2) == 1 and v2[0]["graph_run_id"] == gid
    telemetry = _read_jsonl(state / "telemetry.jsonl")
    assert len(telemetry) == 1
    assert telemetry[0]["loopfactory.graph_run_id"] == gid
    assert telemetry[0]["loopfactory.node"] == "N1"
    score_rows = _read_jsonl(state / "scores.jsonl")
    assert len(score_rows) == 1
    assert score_rows[0]["target_ref"]["graph_run_id"] == gid
    graph_rows = [r for r in _read_jsonl(state / "runs.jsonl")
                  if r.get("graph_run_id") == gid]
    assert graph_rows

    # the rollup joins all four streams on that one key
    (state / "STATE.json").write_text(
        json.dumps({"epoch": 0, "status": "ok", "ok": True}), encoding="utf-8")
    built = rollup.rebuild(tmp_path)
    assert built["complete"], built
    bundle = rollup.graph_run_bundle(built["database"], gid)
    run_ids = {row["run_id"] for row in bundle["run"]}
    assert gid in run_ids  # the graph run itself (runs.jsonl)
    assert v2[0]["run_id"] in run_ids  # the wrapper summary (runs-v2.jsonl)
    assert all(row["graph_run_id"] == gid or row["run_id"] == gid
               for row in bundle["run"])
    assert [row["graph_run_id"] for row in bundle["step_telemetry"]] == [gid]
    assert [row["graph_run_id"] for row in bundle["score"]] == [gid]
