"""R1 — unified execution identity across the four record streams.

One logical graph execution must be joinable by a single correlation key: the
graph runner's run_id. The runner injects it into every node process as
OE_GRAPH_RUN_ID (plus OE_GRAPH_NODE_ID); runrecord/scores/model-telemetry
emitters pick it up (explicitly or from the env) and rollup joins on it.
Missing or mismatched identity where required is a fail-closed ValueError,
never a silent null.
"""
import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from factory import rollup, runrecord, scores
from kernel import capabilities, lock_service, receipts
from kernel.gateways import model as model_gateway


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
GRAPH_ENV = "OE_GRAPH_RUN_ID"
NODE_ENV = "OE_GRAPH_NODE_ID"


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --------------------------------------------------------------------------- #
# The env-name contract is shared across modules; drift would silently break
# the identity chain, so the constants must agree everywhere they exist.
# --------------------------------------------------------------------------- #

def test_graph_env_names_agree_across_modules():
    assert capabilities.GRAPH_RUN_ID_ENV == GRAPH_ENV
    assert capabilities.GRAPH_NODE_ID_ENV == NODE_ENV
    assert runrecord.GRAPH_RUN_ID_ENV == GRAPH_ENV
    assert scores.GRAPH_RUN_ID_ENV == GRAPH_ENV
    assert model_gateway.GRAPH_RUN_ID_ENV == GRAPH_ENV
    assert model_gateway.GRAPH_NODE_ID_ENV == NODE_ENV
    assert RUN.GRAPH_RUN_ID_ENV == GRAPH_ENV
    assert RUN.GRAPH_NODE_ID_ENV == NODE_ENV


# --------------------------------------------------------------------------- #
# runrecord (#13 stream): optional graph_run_id, required under the runner
# --------------------------------------------------------------------------- #

def test_runrecord_graph_run_id_round_trips(tmp_path):
    runrecord.emit_record(
        tmp_path, department="demo", node="n1", status="ok",
        graph_run_id="SG-RUN-abc123")
    rows = _read_jsonl(tmp_path / "runs-v2.jsonl")
    assert rows[0]["graph_run_id"] == "SG-RUN-abc123"
    runrecord.validate_record(rows[0])  # derived field re-validates


def test_runrecord_without_graph_run_id_stays_backward_compatible(tmp_path):
    """Existing podcast emitters keep working: no env, no field, no change."""
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
    monkeypatch.setenv(GRAPH_ENV, "SG-RUN-required")
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


def test_runrecord_under_runner_rejects_mismatched_graph_run_id(
        tmp_path, monkeypatch):
    monkeypatch.setenv(GRAPH_ENV, "SG-RUN-truth")
    with pytest.raises(ValueError, match="graph_run_id"):
        runrecord.emit_record(
            tmp_path, department="demo", node="n1", status="ok",
            graph_run_id="SG-RUN-forged")
    assert not (tmp_path / "runs-v2.jsonl").exists()


def test_emit_record_defaults_graph_run_id_from_runner_env(
        tmp_path, monkeypatch):
    monkeypatch.setenv(GRAPH_ENV, "SG-RUN-env")
    runrecord.emit_record(tmp_path, department="demo", node="n1", status="ok")
    rows = _read_jsonl(tmp_path / "runs-v2.jsonl")
    assert rows[0]["graph_run_id"] == "SG-RUN-env"


# --------------------------------------------------------------------------- #
# kernel telemetry (#12 stream): rows gain graph_run_id + node under a runner
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


def test_model_telemetry_defaults_identity_from_runner_env(
        tmp_path, monkeypatch):
    monkeypatch.setenv(GRAPH_ENV, "SG-RUN-env")
    monkeypatch.setenv(NODE_ENV, "n-env")
    service = _service(tmp_path)
    issued = service.request_model("sanitized", sanitized=True)
    service.call_model("sanitized", issued["receipt"], runner=lambda _p: "ok")
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[0]
    assert row["loopfactory.graph_run_id"] == "SG-RUN-env"
    assert row["loopfactory.node"] == "n-env"


def test_model_telemetry_mismatched_graph_run_id_fails_closed(
        tmp_path, monkeypatch):
    monkeypatch.setenv(GRAPH_ENV, "SG-RUN-truth")
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
    # the runner-injected identity is authoritative in the recorded row
    assert row["loopfactory.graph_run_id"] == "SG-RUN-truth"


def test_model_telemetry_without_runner_env_stays_null(tmp_path):
    service = _service(tmp_path)
    issued = service.request_model("sanitized", sanitized=True)
    service.call_model("sanitized", issued["receipt"], runner=lambda _p: "ok")
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[0]
    assert row["loopfactory.graph_run_id"] is None


# --------------------------------------------------------------------------- #
# scores (#12 stream): target_ref gains graph_run_id
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


def test_build_score_defaults_graph_run_id_from_runner_env(monkeypatch):
    monkeypatch.setenv(GRAPH_ENV, "SG-RUN-env")
    record = scores.build_score(**_score_kwargs({
        "run_id": None, "step_id": None, "node": "n1", "department": "demo"}))
    assert record["target_ref"]["graph_run_id"] == "SG-RUN-env"


def test_append_score_under_runner_requires_matching_graph_run_id(
        tmp_path, monkeypatch):
    record = scores.build_score(**_score_kwargs({
        "run_id": None, "step_id": None, "node": "n1", "department": "demo",
        "graph_run_id": "SG-RUN-forged"}))
    monkeypatch.setenv(GRAPH_ENV, "SG-RUN-truth")
    with pytest.raises(ValueError, match="graph_run_id"):
        scores.append_score(tmp_path, record)
    bare = scores.validate_score({**record, "target_ref": {
        "run_id": None, "step_id": None, "node": "n1", "department": "demo"}})
    with pytest.raises(ValueError, match="graph_run_id"):
        scores.append_score(tmp_path, bare)
    assert not (tmp_path / "scores.jsonl").exists()


# --------------------------------------------------------------------------- #
# rollup (#12 store): the join key lands in run/step_telemetry/score tables
# --------------------------------------------------------------------------- #

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
    bundle = rollup.graph_run_bundle(result["database"], gid)
    assert {row["run_id"] for row in bundle["run"]} == {gid, "wrapper-1"}
    assert len(bundle["step_telemetry"]) == 1
    assert bundle["step_telemetry"][0]["graph_run_id"] == gid
    assert len(bundle["score"]) == 1
    assert bundle["score"][0]["graph_run_id"] == gid


# --------------------------------------------------------------------------- #
# runner: identity is injected into every node process env
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
import json, os, pathlib
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
(state / "envcap.json").write_text(json.dumps({
    "graph_run_id": os.environ.get("OE_GRAPH_RUN_ID"),
    "node_id": os.environ.get("OE_GRAPH_NODE_ID"),
}), encoding="utf-8")
print(json.dumps({"status": "ok"}))
"""


def test_runner_injects_graph_identity_into_node_env(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": ENV_CAPTURE}, _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "done"
    captured = json.loads(
        (dept / "state" / "envcap.json").read_text(encoding="utf-8"))
    assert captured == {"graph_run_id": result["run_id"], "node_id": "N1"}


def test_runner_records_carry_graph_run_id(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": ENV_CAPTURE}, _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    rows = _read_jsonl(dept / "state" / "runs.jsonl")
    stamped = [r for r in rows if r.get("run_id") == result["run_id"]]
    assert stamped and all(
        r.get("graph_run_id") == result["run_id"] for r in stamped)


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

# 1) #13 wrapper summary — identity comes from the runner-injected env
runrecord.emit_record(state, department="demo", node="N1", status="ok")

# 2) #12 telemetry — identity comes from the runner-injected env
service = lock_service.LockService(
    receipts.LocalSigner(key="node-key"),
    budget_ledger=state / "kernel" / "budget.jsonl",
    freq_ledger=state / "kernel" / "frequency.jsonl",
    nonce_ledger=state / "kernel" / "nonces.jsonl",
    telemetry_path=state / "telemetry.jsonl",
)
issued = service.request_model("sanitized", sanitized=True)
service.call_model("sanitized", issued["receipt"], runner=lambda _p: "ok")

# 3) #12 score — identity comes from the runner-injected env
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
