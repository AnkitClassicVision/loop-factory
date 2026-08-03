"""R1 (round 3, Option C) — runner-mediated appends unify execution identity.

Nodes never touch the canonical record streams: with OE_RECORD_SPOOL present
(injected post-scrub by the runner, like OE_DEPARTMENT) every appender writes
to a per-attempt spool. After the node exits — and BEFORE any transition
receipt mints — the runner validates each spooled row, stamps identity from
its OWN execution state (identity is assigned, never claimed), SIGNS each
promoted row, and appends under the records fence. No security tokens travel
to nodes at all. factory/rollup.py verifies promotion signatures at read
time and quarantines unsigned/invalid graph-identity claims as incidents,
which catches same-uid direct file writes.
"""
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

from factory import rollup, runrecord, scores
from kernel import capabilities, lock_service, receipts


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = _load("identity_kernel_receipts", "kernel/receipts.py")
REL = _load("identity_release", "factory/release.py")
RUN = _load("identity_runner", "factory/runner.py")
PJ = _load("identity_projection", "factory/projection.py")

SIGNER = R.LocalSigner(key="test-key")
SPOOL_ENV = "OE_RECORD_SPOOL"


def _read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _sign_row(row, *, signer=SIGNER, pid="p" * 32, attempt=1):
    """Replicate the runner's promotion stamp+sign for hand-built fixtures."""
    stamped = {**row, "promotion": {"schema": "promotion/v1", "id": pid,
                                    "attempt": attempt}}
    payload = json.dumps(stamped, sort_keys=True,
                         separators=(",", ":")).encode()
    stamped["promotion"] = {**stamped["promotion"],
                            "sig": signer.sign(payload)}
    return stamped


# --------------------------------------------------------------------------- #
# The env-name contract is shared across modules; drift would silently break
# the spool redirect, so the constant must agree everywhere it exists.
# --------------------------------------------------------------------------- #

def test_record_spool_env_name_agrees_across_modules():
    assert capabilities.RECORD_SPOOL_ENV == SPOOL_ENV
    assert RUN.RECORD_SPOOL_ENV == SPOOL_ENV
    assert runrecord.RECORD_SPOOL_ENV == SPOOL_ENV
    assert scores.RECORD_SPOOL_ENV == SPOOL_ENV


# --------------------------------------------------------------------------- #
# Appenders: with a spool present, canonical paths are unreachable
# --------------------------------------------------------------------------- #

def test_runrecord_spools_instead_of_canonical(tmp_path, monkeypatch):
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setenv(SPOOL_ENV, str(spool))
    canonical = tmp_path / "state"
    runrecord.emit_record(canonical, department="demo", node="n1", status="ok")
    assert not (canonical / "runs-v2.jsonl").exists()
    rows = _read_jsonl(spool / "runs-v2.jsonl")
    assert len(rows) == 1 and rows[0]["status"] == "ok"


def test_scores_spool_instead_of_canonical(tmp_path, monkeypatch):
    spool = tmp_path / "state" / "spooldir"
    spool.mkdir(parents=True)
    monkeypatch.setenv(SPOOL_ENV, str(spool))
    record = scores.build_score(
        name="qa", value=1.0, label="pass", explanation="ok", source="script",
        judge_model=None, config_version="v1",
        target_ref={"run_id": None, "step_id": None, "node": "n1",
                    "department": "demo"})
    scores.append_score(tmp_path / "state", record)
    assert not (tmp_path / "state" / "scores.jsonl").exists()
    assert len(_read_jsonl(spool / "scores.jsonl")) == 1


def test_model_telemetry_spools_instead_of_canonical(tmp_path, monkeypatch):
    spool = tmp_path / "state" / "spooldir"
    spool.mkdir(parents=True)
    monkeypatch.setenv(SPOOL_ENV, str(spool))
    service = lock_service.LockService(
        receipts.LocalSigner(key="test"),
        budget_ledger=tmp_path / "state" / "kernel" / "budget.jsonl",
        freq_ledger=tmp_path / "state" / "kernel" / "frequency.jsonl",
        nonce_ledger=tmp_path / "state" / "kernel" / "nonces.jsonl",
        telemetry_path=tmp_path / "state" / "telemetry.jsonl",
    )
    issued = service.request_model("sanitized", sanitized=True)
    service.call_model("sanitized", issued["receipt"], runner=lambda _p: "ok")
    assert not (tmp_path / "state" / "telemetry.jsonl").exists()
    rows = _read_jsonl(spool / "telemetry.jsonl")
    assert len(rows) == 1
    assert rows[0]["schema_version"] == "step-telemetry/v1"


# --------------------------------------------------------------------------- #
# Appenders outside the runner: unchanged, backward-compatible behavior
# --------------------------------------------------------------------------- #

def test_runrecord_graph_run_id_round_trips(tmp_path):
    runrecord.emit_record(
        tmp_path, department="demo", node="n1", status="ok",
        graph_run_id="SG-RUN-abc123")
    rows = _read_jsonl(tmp_path / "runs-v2.jsonl")
    assert rows[0]["graph_run_id"] == "SG-RUN-abc123"
    runrecord.validate_record(rows[0])


def test_runrecord_without_spool_stays_backward_compatible(tmp_path):
    runrecord.emit_record(tmp_path, department="demo", node="n1", status="ok")
    rows = _read_jsonl(tmp_path / "runs-v2.jsonl")
    assert "graph_run_id" not in rows[0]
    assert "promotion" not in rows[0]
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


def test_validate_record_accepts_promoted_rows(tmp_path):
    runrecord.emit_record(tmp_path, department="demo", node="n1", status="ok")
    row = _read_jsonl(tmp_path / "runs-v2.jsonl")[0]
    promoted = _sign_row({**row, "graph_run_id": "SG-RUN-x"})
    runrecord.validate_record(promoted)


def test_score_validation_accepts_promoted_rows():
    record = scores.build_score(
        name="qa", value=1.0, label="pass", explanation="ok", source="script",
        judge_model=None, config_version="v1",
        target_ref={"run_id": None, "step_id": None, "node": "n1",
                    "department": "demo", "graph_run_id": "SG-RUN-x"})
    scores.validate_score(_sign_row(record))


def test_model_telemetry_explicit_tag_without_spool(tmp_path):
    service = lock_service.LockService(
        receipts.LocalSigner(key="test"),
        budget_ledger=tmp_path / "state" / "kernel" / "budget.jsonl",
        freq_ledger=tmp_path / "state" / "kernel" / "frequency.jsonl",
        nonce_ledger=tmp_path / "state" / "kernel" / "nonces.jsonl",
        telemetry_path=tmp_path / "state" / "telemetry.jsonl",
    )
    issued = service.request_model("sanitized", sanitized=True)
    service.call_model(
        "sanitized", issued["receipt"], runner=lambda _p: "ok",
        node="n1", graph_run_id="SG-RUN-explicit")
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[0]
    assert row["loopfactory.graph_run_id"] == "SG-RUN-explicit"
    assert row["loopfactory.node"] == "n1"
    issued = service.request_model("sanitized", sanitized=True)
    service.call_model("sanitized", issued["receipt"], runner=lambda _p: "ok")
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[1]
    assert row["loopfactory.graph_run_id"] is None


# --------------------------------------------------------------------------- #
# rollup: join columns + read-time promotion-signature defense
# --------------------------------------------------------------------------- #

def test_rollup_schema_marker_bumped_for_new_columns():
    assert rollup.SCHEMA_VERSION == "rollup/v2"


def _rollup_state(tmp_path):
    state = tmp_path / "departments" / "demo" / "state"
    state.mkdir(parents=True)
    (state / "STATE.json").write_text(
        json.dumps({"epoch": 0, "status": "ok", "ok": True}), encoding="utf-8")
    return state


def _write_projection(state, *run_ids, tamper=False):
    """Write the department's SIGNED execution projection — the only source
    rollup trusts for graph-run identity (F2)."""
    body = PJ.build_projection(
        department="demo", graph_id="SG-RUN", graph_hash="graph-hash",
        release_hash="release-hash", factory_version={}, nodes=[], edges=[],
        runs=[{"run_id": run_id, "state": "done",
               "termination_reason": "terminal_edge", "transitions": []}
              for run_id in run_ids],
        generated_at="2026-08-02T00:00:00+00:00")
    signed = PJ.sign_projection(body, SIGNER)
    if tamper:  # mutate AFTER signing: the signature no longer verifies
        signed["runs"].append({"run_id": "injected", "state": "done",
                               "termination_reason": None, "transitions": []})
    path = state / "receipts" / "execution-projection.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(signed), encoding="utf-8")


def _v2_row(gid, run_id="wrapper-1"):
    return {
        "schema": "run-record/v2", "rev": 2, "run_id": run_id,
        "graph_run_id": gid, "department": "demo", "node": "n1", "epoch": 0,
        "ts": "2026-08-02T00:00:01+00:00", "attempt": 1, "round": None,
        "release": None, "trigger": None, "engine": None, "model": None,
        "auth_class": None, "usage": None, "cost": None, "duration_ms": None,
        "status": "ok", "errors": [], "artifacts": [], "receipts": [],
        "evaluator": None, "approval": None, "external_actions_taken": 0,
    }


def _telemetry_row(gid):
    return {
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
    }


def _score_row(gid):
    return {
        "gen_ai.evaluation.name": "qa", "gen_ai.evaluation.score.value": 1.0,
        "gen_ai.evaluation.score.label": "pass",
        "gen_ai.evaluation.explanation": "ok", "source": "script",
        "judge_model": None, "config_version": "v1",
        "target_ref": {"run_id": None, "step_id": None, "node": "n1",
                       "department": "demo", "graph_run_id": gid},
        "ts": "2026-08-02T00:00:03+00:00",
        "schema_version": "score-record/v1",
    }


def _write_row(path, row):
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def test_rollup_joins_signed_promoted_rows(tmp_path):
    state = _rollup_state(tmp_path)
    gid = "SG-RUN-" + "a" * 8
    _write_row(state / "runs.jsonl", {
        "ts": "2026-08-02T00:00:00+00:00", "event": "run_done",
        "run_id": gid, "graph_run_id": gid, "loop_id": "SG-RUN"})
    _write_row(state / "runs-v2.jsonl", _sign_row(_v2_row(gid), pid="a" * 32))
    _write_row(state / "telemetry.jsonl",
               _sign_row(_telemetry_row(gid), pid="b" * 32))
    _write_row(state / "scores.jsonl", _sign_row(_score_row(gid), pid="c" * 32))
    _write_projection(state, gid)

    result = rollup.rebuild(tmp_path, signer=SIGNER)
    assert result["complete"], result
    assert result["schema_version"] == "rollup/v2"
    bundle = rollup.graph_run_bundle(result["database"], gid)
    assert {row["run_id"] for row in bundle["run"]} == {gid, "wrapper-1"}
    assert [row["graph_run_id"] for row in bundle["step_telemetry"]] == [gid]
    assert [row["graph_run_id"] for row in bundle["score"]] == [gid]
    connection = sqlite3.connect(result["database"])
    try:
        marker = connection.execute(
            "SELECT schema_version FROM department").fetchone()[0]
    finally:
        connection.close()
    assert marker == "rollup/v2"


def test_rollup_quarantines_unsigned_graph_claims(tmp_path):
    """Read-time defense: a same-uid direct file write claiming graph
    identity carries no promotion signature — quarantined as an incident,
    excluded from the joined tables."""
    state = _rollup_state(tmp_path)
    gid = "SG-RUN-" + "b" * 8
    _write_row(state / "runs-v2.jsonl", _v2_row(gid, run_id="forged-1"))
    _write_row(state / "telemetry.jsonl", _telemetry_row(gid))
    _write_row(state / "scores.jsonl", _score_row(gid))

    result = rollup.rebuild(tmp_path, signer=SIGNER)
    assert result["complete"] is False
    bundle = rollup.graph_run_bundle(result["database"], gid)
    assert bundle["run"] == []
    assert bundle["step_telemetry"] == []
    assert bundle["score"] == []
    connection = sqlite3.connect(result["database"])
    try:
        codes = [row[0] for row in connection.execute(
            "SELECT code FROM incident")]
    finally:
        connection.close()
    quarantined = [c for c in codes if c.startswith("graph_identity_")]
    assert len(quarantined) == 3


def test_rollup_quarantines_tampered_promoted_rows(tmp_path):
    state = _rollup_state(tmp_path)
    gid = "SG-RUN-" + "c" * 8
    row = _sign_row(_v2_row(gid), pid="d" * 32)
    row["node"] = "n-tampered"  # post-signature mutation
    _write_row(state / "runs-v2.jsonl", row)
    result = rollup.rebuild(tmp_path, signer=SIGNER)
    assert result["complete"] is False
    assert rollup.graph_run_bundle(result["database"], gid)["run"] == []


def test_rollup_without_verifier_quarantines_graph_claims(tmp_path):
    """No signer resolvable -> graph claims are unverifiable -> deny by
    default, never silently trusted."""
    state = _rollup_state(tmp_path)
    gid = "SG-RUN-" + "e" * 8
    _write_row(state / "runs-v2.jsonl", _sign_row(_v2_row(gid)))
    result = rollup.rebuild(tmp_path)
    assert result["complete"] is False
    assert rollup.graph_run_bundle(result["database"], gid)["run"] == []


def test_rollup_ingests_unsigned_rows_without_graph_claims(tmp_path):
    """Legacy emitters (no graph identity) stay ingestible unsigned."""
    state = _rollup_state(tmp_path)
    row = _v2_row(None)
    del row["graph_run_id"]
    _write_row(state / "runs-v2.jsonl", row)
    result = rollup.rebuild(tmp_path, signer=SIGNER)
    assert result["complete"], result
    connection = sqlite3.connect(result["database"])
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM run WHERE run_id = 'wrapper-1'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 1


# --------------------------------------------------------------------------- #
# runner: spool injection, promotion stamping/signing, fail-closed spool
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
    "spool": os.environ.get("OE_RECORD_SPOOL"),
    "kernel_key_leaked": "OE_KERNEL_SIGNING_KEY" in os.environ,
}), encoding="utf-8")
print(json.dumps({"status": "ok"}))
"""


def test_runner_injects_spool_env_no_secrets(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": ENV_CAPTURE}, _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "done"
    captured = json.loads(
        (dept / "state" / "envcap.json").read_text(encoding="utf-8"))
    expected = (dept / "state" / "graph_runs" / result["run_id"] / "spool"
                / "N1-1")
    assert captured["spool"] == str(expected)
    assert captured["kernel_key_leaked"] is False


def test_runner_records_carry_graph_run_id(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": ENV_CAPTURE}, _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    rows = _read_jsonl(dept / "state" / "runs.jsonl")
    stamped = [r for r in rows if r.get("run_id") == result["run_id"]]
    assert stamped and all(
        r.get("graph_run_id") == result["run_id"] for r in stamped)


EMITTER_NODE = """\
import json, pathlib, sys
sys.path.insert(0, {root!r})
from factory import runrecord, scores
from kernel import lock_service, receipts

state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)

runrecord.emit_record(state, department="demo", node="N1", status="ok")

service = lock_service.LockService(
    receipts.LocalSigner(key="node-key"),
    budget_ledger=state / "kernel" / "budget.jsonl",
    freq_ledger=state / "kernel" / "frequency.jsonl",
    nonce_ledger=state / "kernel" / "nonces.jsonl",
    telemetry_path=state / "telemetry.jsonl",
)
issued = service.request_model("sanitized", sanitized=True)
service.call_model("sanitized", issued["receipt"], runner=lambda _p: "ok")

scores.append_score(state, scores.build_score(
    name="qa", value=1.0, label="pass", explanation="ok", source="script",
    judge_model=None, config_version="v1",
    target_ref={{"run_id": None, "step_id": None, "node": "N1",
                 "department": "demo"}}))

print(json.dumps({{"status": "ok"}}))
"""


def test_one_graph_execution_yields_single_joined_record_set(tmp_path):
    """ACCEPTANCE: one execution through factory/runner.py -> promoted,
    signed, runner-stamped rows across all four streams, joined by the one
    graph run_id in rollup.sqlite3."""
    dept = _make_dept(
        tmp_path, {"sense.py": EMITTER_NODE.format(root=str(ROOT))},
        _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "done"
    gid = result["run_id"]
    state = dept / "state"

    v2 = _read_jsonl(state / "runs-v2.jsonl")
    assert len(v2) == 1 and v2[0]["graph_run_id"] == gid
    assert v2[0]["promotion"]["sig"]
    telemetry = _read_jsonl(state / "telemetry.jsonl")
    assert len(telemetry) == 1
    assert telemetry[0]["loopfactory.graph_run_id"] == gid
    assert telemetry[0]["loopfactory.node"] == "N1"
    score_rows = _read_jsonl(state / "scores.jsonl")
    assert len(score_rows) == 1
    assert score_rows[0]["target_ref"]["graph_run_id"] == gid
    assert (RUN.run_control_dir(tmp_path, "demo", gid)
            / "promotions.jsonl").exists()

    (state / "STATE.json").write_text(
        json.dumps({"epoch": 0, "status": "ok", "ok": True}), encoding="utf-8")
    built = rollup.rebuild(tmp_path, signer=SIGNER)
    assert built["complete"], built
    bundle = rollup.graph_run_bundle(built["database"], gid)
    run_ids = {row["run_id"] for row in bundle["run"]}
    assert gid in run_ids
    assert v2[0]["run_id"] in run_ids
    assert [row["graph_run_id"] for row in bundle["step_telemetry"]] == [gid]
    assert [row["graph_run_id"] for row in bundle["score"]] == [gid]


FORGING_EMITTER = """\
import json, pathlib, sys
sys.path.insert(0, {root!r})
from factory import runrecord

state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
runrecord.emit_record(state, department="evil-dept", node="N-forged",
                      status="ok", graph_run_id="SG-RUN-forged")
print(json.dumps({{"status": "ok"}}))
"""


def test_promotion_stamps_runner_truth_over_forged_claims(tmp_path):
    """B1 regression (Option C): the node claims another department, node,
    and run in its emitted record — promotion assigns the runner's own
    execution identity; the canonical stream never carries the claim."""
    dept = _make_dept(
        tmp_path, {"sense.py": FORGING_EMITTER.format(root=str(ROOT))},
        _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "done"
    rows = _read_jsonl(dept / "state" / "runs-v2.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert row["graph_run_id"] == result["run_id"]
    assert row["department"] == "demo"
    assert row["node"] == "N1"
    raw = json.dumps(rows)
    assert "SG-RUN-forged" not in raw
    assert "evil-dept" not in raw and "N-forged" not in raw


DIRECT_WRITER = """\
import json, pathlib
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
row = {
    "schema": "run-record/v2", "rev": 2, "run_id": "direct-1",
    "graph_run_id": "SG-RUN-direct-forgery", "department": "demo",
    "node": "N1", "epoch": 0, "ts": "2026-08-02T00:00:01+00:00",
    "attempt": 1, "round": None, "release": None, "trigger": None,
    "engine": None, "model": None, "auth_class": None, "usage": None,
    "cost": None, "duration_ms": None, "status": "ok", "errors": [],
    "artifacts": [], "receipts": [], "evaluator": None, "approval": None,
    "external_actions_taken": 0,
}
with (state / "runs-v2.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, sort_keys=True) + "\\n")
print(json.dumps({"status": "ok"}))
"""


def test_direct_canonical_write_is_quarantined_at_read_time(tmp_path):
    """B1 regression (Option C): node code bypasses the appenders and writes
    a graph-claiming row straight into the canonical stream — rollup
    quarantines it as an incident (no promotion signature)."""
    dept = _make_dept(tmp_path, {"sense.py": DIRECT_WRITER},
                      _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "done"
    (dept / "state" / "STATE.json").write_text(
        json.dumps({"epoch": 0, "status": "ok", "ok": True}), encoding="utf-8")
    built = rollup.rebuild(tmp_path, signer=SIGNER)
    assert built["complete"] is False
    bundle = rollup.graph_run_bundle(built["database"],
                                     "SG-RUN-direct-forgery")
    assert bundle["run"] == []
    connection = sqlite3.connect(built["database"])
    try:
        codes = [row[0] for row in connection.execute(
            "SELECT code FROM incident")]
    finally:
        connection.close()
    assert any(code.startswith("graph_identity_") for code in codes)


MALFORMED_SPOOLER = """\
import json, os, pathlib
spool = pathlib.Path(os.environ["OE_RECORD_SPOOL"])
spool.mkdir(parents=True, exist_ok=True)
with (spool / "runs-v2.jsonl").open("a", encoding="utf-8") as fh:
    fh.write("this is not a record\\n")
print(json.dumps({"status": "ok"}))
"""


def test_malformed_spool_row_is_node_failure_no_promotion(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": MALFORMED_SPOOLER},
                      _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "escalated"  # on_fail=escalate, fail-closed
    run_state = json.loads(
        (dept / "state" / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))
    assert "spool" in run_state["nodes"]["N1"]["reason"]
    assert not (dept / "state" / "runs-v2.jsonl").exists()
    assert [(t["from"], t["to"], t["kind"]) for t in run_state["transitions"]] \
        == [("N1", None, "escalation")]


WRONG_STREAM_SPOOLER = """\
import json, os, pathlib
spool = pathlib.Path(os.environ["OE_RECORD_SPOOL"])
spool.mkdir(parents=True, exist_ok=True)
with (spool / "surprise.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"status": "ok"}) + "\\n")
print(json.dumps({"status": "ok"}))
"""


def test_unknown_spool_stream_is_node_failure(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": WRONG_STREAM_SPOOLER},
                      _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "escalated"
    run_state = json.loads(
        (dept / "state" / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))
    assert "spool" in run_state["nodes"]["N1"]["reason"]


SINGLE_EMITTER = """\
import json, pathlib, sys
sys.path.insert(0, {root!r})
from factory import runrecord
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
runrecord.emit_record(state, department="demo", node="N1", status="ok")
print(json.dumps({{"status": "ok"}}))
"""


def _crash_once_at(boundary):
    state = {"armed": True}

    def hook(name):
        if name == boundary and state["armed"]:
            state["armed"] = False
            raise RuntimeError(f"simulated crash at {boundary}")
    return hook


def test_crash_before_promotion_resumes_and_promotes_exactly_once(tmp_path):
    dept = _make_dept(
        tmp_path, {"sense.py": SINGLE_EMITTER.format(root=str(ROOT))},
        _one_node_manifest())
    with pytest.raises(RuntimeError, match="pre_promotion"):
        RUN.run_graph(dept, trigger_fingerprint="c1", signer=SIGNER,
                      root=tmp_path, sleep_fn=lambda s: None,
                      crash_hook=_crash_once_at("pre_promotion"))
    assert not (dept / "state" / "runs-v2.jsonl").exists()
    second = RUN.run_graph(dept, trigger_fingerprint="c1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert second["resumed"] is True and second["state"] == "done"
    rows = _read_jsonl(dept / "state" / "runs-v2.jsonl")
    assert len(rows) == 1
    assert rows[0]["graph_run_id"] == second["run_id"]


MARKING_EMITTER = """\
import json, pathlib, sys
sys.path.insert(0, {root!r})
from factory import runrecord
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
with (state / "execcount.txt").open("a", encoding="utf-8") as fh:
    fh.write("exec\\n")
runrecord.emit_record(state, department="demo", node="N1", status="ok")
print(json.dumps({{"status": "ok"}}))
"""


def _incident_codes(db_path):
    connection = sqlite3.connect(db_path)
    try:
        return [row[0] for row in connection.execute(
            "SELECT code FROM incident")]
    finally:
        connection.close()


def _wrapper_row_count(db_path, gid):
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM run WHERE graph_run_id = ? AND run_id != ?",
            (gid, gid)).fetchone()[0]
    finally:
        connection.close()


def test_crash_mid_promotion_completes_from_original_spool_exactly_once(
        tmp_path):
    """F3 regression: a crash between the canonical appends and the
    completion marker must NOT re-execute the node — re-execution produces a
    different body under the same promotion id (the probe's two-signed-bodies
    defect). Recovery completes the promotion from the ORIGINAL spool, so the
    re-appended rows are byte-identical and collapse to exactly one row."""
    dept = _make_dept(
        tmp_path, {"sense.py": MARKING_EMITTER.format(root=str(ROOT))},
        _one_node_manifest())
    with pytest.raises(RuntimeError, match="pre_promotion_marker"):
        RUN.run_graph(dept, trigger_fingerprint="c2", signer=SIGNER,
                      root=tmp_path, sleep_fn=lambda s: None,
                      crash_hook=_crash_once_at("pre_promotion_marker"))
    executions = (dept / "state" / "execcount.txt").read_text(
        encoding="utf-8").splitlines()
    assert len(executions) == 1
    second = RUN.run_graph(dept, trigger_fingerprint="c2", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert second["resumed"] is True and second["state"] == "done"
    # the node did NOT run a second time — promotion resumed from the spool
    assert len((dept / "state" / "execcount.txt").read_text(
        encoding="utf-8").splitlines()) == 1
    physical = _read_jsonl(dept / "state" / "runs-v2.jsonl")
    assert len(physical) == 2
    assert len({r["promotion"]["id"] for r in physical}) == 1
    # byte-identical bodies: no conflicting signed rows under one id
    assert len({json.dumps(r, sort_keys=True) for r in physical}) == 1
    (dept / "state" / "STATE.json").write_text(
        json.dumps({"epoch": 0, "status": "ok", "ok": True}), encoding="utf-8")
    built = rollup.rebuild(tmp_path, signer=SIGNER)
    assert _wrapper_row_count(built["database"], second["run_id"]) == 1
    assert not [c for c in _incident_codes(built["database"])
                if c.startswith("graph_identity_")]


def test_conflicting_promotion_bodies_are_quarantined(tmp_path):
    """F3 regression: two validly signed rows sharing one promotion id but
    differing in content is never last-writer-wins — both are quarantined."""
    state = _rollup_state(tmp_path)
    gid = "SG-RUN-" + "f" * 8
    first = _sign_row(_v2_row(gid, run_id="wrapper-1"), pid="f" * 32)
    second = _sign_row(_v2_row(gid, run_id="wrapper-2"), pid="f" * 32)
    _write_row(state / "runs-v2.jsonl", first)
    _write_row(state / "runs-v2.jsonl", second)
    _write_projection(state, gid)
    result = rollup.rebuild(tmp_path, signer=SIGNER)
    assert result["complete"] is False
    assert any(code.startswith("graph_identity_conflict")
               for code in _incident_codes(result["database"]))
    assert _wrapper_row_count(result["database"], gid) == 0


# --------------------------------------------------------------------------- #
# F1: the promotion marker is runner-owned and verified before any skip
# --------------------------------------------------------------------------- #

def test_promotion_marker_lives_outside_department_state(tmp_path):
    dept = _make_dept(
        tmp_path, {"sense.py": SINGLE_EMITTER.format(root=str(ROOT))},
        _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    control = RUN.run_control_dir(tmp_path, "demo", result["run_id"])
    assert (control / "promotions.jsonl").exists()
    # nothing marker-shaped remains anywhere the node can reach by its own
    # sanctioned paths (its state dir and the spool it was handed)
    assert list((dept / "state").rglob("promotions.jsonl")) == []
    assert control.resolve() not in (dept / "state").resolve().parents
    assert not str(control.resolve()).startswith(
        str((dept / "state").resolve()))


FORGED_MARKER_NODE = """\
import json, os, pathlib, sys
sys.path.insert(0, {root!r})
from factory import runrecord

state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
spool = pathlib.Path(os.environ["OE_RECORD_SPOOL"])

# The probe: claim the promotion already happened, from every location the
# node's own sanctioned paths can reach.
for target in (spool.parent.parent / "promotions.jsonl",
               spool.parent / "promotions.jsonl",
               state / "promotions.jsonl"):
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({{"node": "N1", "attempt": 1}}) + "\\n")

runrecord.emit_record(state, department="demo", node="N1", status="ok")
print(json.dumps({{"status": "ok"}}))
"""


def test_node_written_marker_does_not_strand_records(tmp_path):
    """F1 regression: the node forges promotion markers at every path it can
    reach. Promotion still happens — rows land canonically, not stranded."""
    dept = _make_dept(
        tmp_path, {"sense.py": FORGED_MARKER_NODE.format(root=str(ROOT))},
        _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "done"
    rows = _read_jsonl(dept / "state" / "runs-v2.jsonl")
    assert len(rows) == 1
    assert rows[0]["graph_run_id"] == result["run_id"]
    assert rows[0]["promotion"]["sig"]


def test_unverifiable_marker_repromotes_and_records_incident(tmp_path):
    """F1 regression: a marker whose signature does not verify is never
    honored as a skip — the runner re-promotes and records an incident."""
    import hashlib as _hashlib

    dept = _make_dept(
        tmp_path, {"sense.py": SINGLE_EMITTER.format(root=str(ROOT))},
        _one_node_manifest())
    run_id = "SG-RUN-" + _hashlib.sha256(b"t1").hexdigest()
    control = RUN.run_control_dir(tmp_path, "demo", run_id)
    control.mkdir(parents=True, exist_ok=True)
    with (control / "promotions.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "schema": "promotion-marker/v1", "department": "demo",
            "run_id": run_id, "node": "N1", "attempt": 1,
            "counts": {"runs-v2.jsonl": 1}, "row_ids": ["deadbeef"],
            "sig": "not-a-valid-signature"}) + "\n")
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "done"
    rows = _read_jsonl(dept / "state" / "runs-v2.jsonl")
    assert len(rows) == 1  # re-promoted, not silently skipped
    incidents = _read_jsonl(dept / "state" / "graph_incidents.jsonl")
    assert any(row.get("code") == "promotion_marker_unverifiable"
               for row in incidents)


# --------------------------------------------------------------------------- #
# F2: graph-run rows derive from the VERIFIED execution projection
# --------------------------------------------------------------------------- #

def test_unbacked_graph_claim_in_runs_jsonl_is_quarantined(tmp_path):
    """F2 regression: an unsigned direct graph claim written into
    runs.jsonl must not enter the victim's bundle — no projection backing,
    so it quarantines as an incident."""
    state = _rollup_state(tmp_path)
    victim = "SG-RUN-" + "9" * 8
    _write_row(state / "runs.jsonl", {
        "ts": "2026-08-02T00:00:00+00:00", "event": "run_done",
        "run_id": victim, "graph_run_id": victim, "loop_id": "SG-RUN"})
    result = rollup.rebuild(tmp_path, signer=SIGNER)
    assert result["complete"] is False
    assert rollup.graph_run_bundle(result["database"], victim)["run"] == []
    assert any(code.startswith("graph_identity_")
               for code in _incident_codes(result["database"]))


def test_graph_rows_backed_by_verified_projection_are_ingested(tmp_path):
    state = _rollup_state(tmp_path)
    gid = "SG-RUN-" + "7" * 8
    _write_row(state / "runs.jsonl", {
        "ts": "2026-08-02T00:00:00+00:00", "event": "run_done",
        "run_id": gid, "graph_run_id": gid, "loop_id": "SG-RUN"})
    _write_projection(state, gid)
    result = rollup.rebuild(tmp_path, signer=SIGNER)
    assert result["complete"], result
    assert [row["run_id"] for row in
            rollup.graph_run_bundle(result["database"], gid)["run"]] == [gid]


def test_tampered_projection_quarantines_graph_rows(tmp_path):
    state = _rollup_state(tmp_path)
    gid = "SG-RUN-" + "8" * 8
    _write_row(state / "runs.jsonl", {
        "ts": "2026-08-02T00:00:00+00:00", "event": "run_done",
        "run_id": gid, "graph_run_id": gid, "loop_id": "SG-RUN"})
    _write_projection(state, gid, tamper=True)
    result = rollup.rebuild(tmp_path, signer=SIGNER)
    assert result["complete"] is False
    assert rollup.graph_run_bundle(result["database"], gid)["run"] == []


# --------------------------------------------------------------------------- #
# F4 / F5: full telemetry validation and hostile spool entries
# --------------------------------------------------------------------------- #

BAD_TELEMETRY_SPOOLER = """\
import json, os, pathlib
spool = pathlib.Path(os.environ["OE_RECORD_SPOOL"])
spool.mkdir(parents=True, exist_ok=True)
row = {
    "schema_version": "step-telemetry/v1", "ts": "2026-08-02T00:00:00+00:00",
    "gen_ai.operation.name": "chat", "gen_ai.provider.name": None,
    "gen_ai.request.model": None, "gen_ai.response.model": None,
    "gen_ai.usage.input_tokens": -5, "gen_ai.usage.output_tokens": None,
    "gen_ai.response.finish_reasons": ["not-a-real-reason"],
    "duration_ms": 1, "error.type": None, "loopfactory.cost_usd": None,
    "loopfactory.auth.route": "nonsense-route", "loopfactory.engine": None,
    "loopfactory.price.schema_version": None,
    "loopfactory.price.effective_date": None,
    "loopfactory.department": "demo", "loopfactory.run_id": None,
    "loopfactory.step_id": None, "loopfactory.node": "N1",
    "loopfactory.telemetry.source": "legacy_null", "estimated": False,
}
with (spool / "telemetry.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(row) + "\\n")
print(json.dumps({"status": "ok"}))
"""


def test_invalid_telemetry_row_refuses_promotion(tmp_path):
    """F4: the full telemetry validator runs BEFORE signing — an invalid row
    never gets signed and rejected downstream."""
    dept = _make_dept(tmp_path, {"sense.py": BAD_TELEMETRY_SPOOLER},
                      _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "escalated"
    assert not (dept / "state" / "telemetry.jsonl").exists()
    run_state = json.loads(
        (dept / "state" / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))
    assert "spool" in run_state["nodes"]["N1"]["reason"]


VALID_V2_BODY = {
    "schema": "run-record/v2", "rev": 2, "run_id": "linked-1",
    "department": "demo", "node": "N1", "epoch": 0,
    "ts": "2026-08-02T00:00:01+00:00", "attempt": 1, "round": None,
    "release": None, "trigger": None, "engine": None, "model": None,
    "auth_class": None, "usage": None, "cost": None, "duration_ms": None,
    "status": "ok", "errors": [], "artifacts": [], "receipts": [],
    "evaluator": None, "approval": None, "external_actions_taken": 0,
}

SYMLINK_SPOOLER = """\
import json, os, pathlib
spool = pathlib.Path(os.environ["OE_RECORD_SPOOL"])
spool.mkdir(parents=True, exist_ok=True)
# the link target holds a fully VALID record, so only rejecting the
# non-regular spool entry itself can catch this
target = spool.parent / "elsewhere.jsonl"
target.write_text({body!r} + "\\n", encoding="utf-8")
(spool / "runs-v2.jsonl").symlink_to(target)
print(json.dumps({{"status": "ok"}}))
""".format(body=json.dumps(VALID_V2_BODY))


def test_symlinked_spool_entry_refuses_promotion(tmp_path):
    """F5: a spool entry that is not a regular file is refused."""
    dept = _make_dept(tmp_path, {"sense.py": SYMLINK_SPOOLER},
                      _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "escalated"
    run_state = json.loads(
        (dept / "state" / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))
    assert "spool" in run_state["nodes"]["N1"]["reason"]
    assert not (dept / "state" / "runs-v2.jsonl").exists()


OVERSIZE_SPOOLER = """\
import json, os, pathlib
spool = pathlib.Path(os.environ["OE_RECORD_SPOOL"])
spool.mkdir(parents=True, exist_ok=True)
# every line is a VALID record under the per-row cap, and the row count
# stays under the per-stream cap: only a FILE-size bound checked before the
# read can catch this
body = json.loads({body!r})
body["errors"] = ["x" * 50000]
line = json.dumps(body) + "\\n"
with (spool / "runs-v2.jsonl").open("w", encoding="utf-8") as fh:
    for _ in range(200):
        fh.write(line)
print(json.dumps({{"status": "ok"}}))
""".format(body=json.dumps(VALID_V2_BODY))


def test_oversize_spool_file_refuses_before_full_read(tmp_path):
    """F5: the size bound is checked BEFORE the file is read into memory."""
    dept = _make_dept(tmp_path, {"sense.py": OVERSIZE_SPOOLER},
                      _one_node_manifest())
    result = RUN.run_graph(dept, trigger_fingerprint="t1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "escalated"
    assert not (dept / "state" / "runs-v2.jsonl").exists()
