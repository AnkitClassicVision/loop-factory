"""Deterministic runner: release-pinned graph, receipt-gated transitions,
run state machine, idempotent triggers, signed projection export.

Every transition requires a VALID SIGNED STEP RECEIPT (kernel/step_receipts):
valid predecessor receipt + output-schema conformance + satisfied edge
predicate + graph/release agreement. Deny-by-default throughout: no release,
drifted graph, invalid graph, unevaluable predicate, or shadow violation all
BLOCK — never allow-on-failure. Effects stay behind kernel gateways; nodes run
through factory/launch.py with no ambient credentials.
"""
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = _load("runner_kernel_receipts", "kernel/receipts.py")
REL = _load("runner_release", "factory/release.py")
PJ = _load("runner_projection", "factory/projection.py")
RUN = _load("runner", "factory/runner.py")

SIGNER = R.LocalSigner(key="test-key")


SENSE_OK = """\
import json, pathlib
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
with (state / "markers.txt").open("a", encoding="utf-8") as fh:
    fh.write("sense\\n")
print(json.dumps({"status": "ok", "delivered_count": 0}))
"""

RECORD_OK = """\
import json
print(json.dumps({"status": "ok"}))
"""

FAIL_NODE = """\
import pathlib, sys
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
with (state / "markers.txt").open("a", encoding="utf-8") as fh:
    fh.write("fail\\n")
sys.exit(3)
"""

HANDLER = """\
import json
print(json.dumps({"status": "handled"}))
"""

VIOLATOR = """\
import json
print(json.dumps({"status": "ok", "external_actions_taken": 1}))
"""

BAD_SHAPE = """\
import json
print(json.dumps({"nope": True}))
"""


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


def _two_node_manifest():
    return {
        "schema_version": 2,
        "subgraphs": [{
            "id": "SG-RUN",
            "concept_refs": ["C1"],
            "entry": "N1",
            "nodes": [_node("N1", "runtime/sense.py"),
                      _node("N2", "runtime/record.py")],
            "edges": [
                {"from": "N1", "to": "N2", "kind": "normal",
                 "when": "receipt.status == 'ok'"},
                {"from": "N1", "kind": "escalation",
                 "when": "receipt.status != 'ok'"},
                {"from": "N2", "kind": "terminal", "when": "true"},
            ],
        }],
    }


def _make_dept(tmp_path, scripts, manifest, pin=True):
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
    if pin:
        h = REL.pin_release(dept, dept / "releases", source_ref="testsha")
        REL.flip_current(dept / "releases", h)
    return dept


def _run(dept, tmp_path, fingerprint="trigger-1", **kwargs):
    return RUN.run_graph(
        dept, trigger_fingerprint=fingerprint, signer=SIGNER,
        root=tmp_path, sleep_fn=lambda s: None, **kwargs)


def _markers(dept):
    path = dept / "state" / "markers.txt"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

def test_run_completes_and_records(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "done"
    assert _markers(dept) == ["sense"]
    state = json.loads(
        (dept / "state" / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))
    assert state["state"] == "done"
    assert state["nodes"]["N1"]["state"] == "done"
    assert state["nodes"]["N2"]["state"] == "done"
    transitions = state["transitions"]
    assert [(t["from"], t["to"]) for t in transitions] == [("N1", "N2"), ("N2", None)]
    assert all(len(t["step_receipt_sha256"]) == 64 for t in transitions)
    runs_rows = [json.loads(line) for line in
                 (dept / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "run_done" for row in runs_rows)


def test_projection_exported_and_auditor_verifies(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path)
    proj = json.loads((dept / "state" / "receipts" / "execution-projection.json")
                      .read_text(encoding="utf-8"))
    assert PJ.verify_projection(proj, SIGNER) == []
    assert proj["runs"][0]["run_id"] == result["run_id"]
    assert proj["runs"][0]["transitions"]


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #

def test_duplicate_trigger_is_recorded_noop(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    first = _run(dept, tmp_path, fingerprint="same-trigger")
    second = _run(dept, tmp_path, fingerprint="same-trigger")
    assert second["duplicate"] is True
    assert second["run_id"] == first["run_id"]
    assert _markers(dept) == ["sense"]  # no second execution
    rows = [json.loads(line) for line in
            (dept / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "duplicate_trigger_noop" for row in rows)


def test_distinct_triggers_run_separately(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    a = _run(dept, tmp_path, fingerprint="t1")
    b = _run(dept, tmp_path, fingerprint="t2")
    assert a["run_id"] != b["run_id"]
    assert _markers(dept) == ["sense", "sense"]


# --------------------------------------------------------------------------- #
# Deny-by-default loading
# --------------------------------------------------------------------------- #

def test_no_release_refuses(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest(), pin=False)
    with pytest.raises(RUN.RunnerRefused, match="release"):
        _run(dept, tmp_path)


def test_graph_drift_refuses(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    manifest = _two_node_manifest()
    manifest["subgraphs"][0]["edges"][0]["when"] = "true"  # post-pin edit
    (dept / "subgraphs.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RUN.RunnerRefused, match="drift"):
        _run(dept, tmp_path)
    assert _markers(dept) == []


def test_v1_manifest_refuses_runner_is_optional(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK},
                      {"subgraphs": [{"id": "SG-A", "nodes": []}]})
    with pytest.raises(RUN.RunnerRefused, match="schema_version"):
        _run(dept, tmp_path)


def test_invalid_v2_graph_refuses_before_any_execution(tmp_path):
    manifest = _two_node_manifest()
    manifest["subgraphs"][0]["nodes"].append(_node("N3", "runtime/orphan.py"))
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK,
                                 "orphan.py": RECORD_OK}, manifest)
    with pytest.raises(RUN.RunnerRefused, match="unreachable"):
        _run(dept, tmp_path)
    assert _markers(dept) == []


# --------------------------------------------------------------------------- #
# Failure policy + refusal routing
# --------------------------------------------------------------------------- #

def test_retries_then_escalates(tmp_path):
    manifest = _two_node_manifest()
    manifest["subgraphs"][0]["nodes"][0] = _node(
        "N1", "runtime/sense.py", max_retries=1)
    dept = _make_dept(tmp_path, {"sense.py": FAIL_NODE, "record.py": RECORD_OK},
                      manifest)
    result = _run(dept, tmp_path)
    assert result["state"] == "escalated"
    assert _markers(dept) == ["fail", "fail"]  # 1 try + 1 retry
    state = json.loads(
        (dept / "state" / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))
    assert state["nodes"]["N1"]["attempts"] == 2  # recorded == launched


def test_refusal_edge_routes_failure_to_handler(tmp_path):
    manifest = {
        "schema_version": 2,
        "subgraphs": [{
            "id": "SG-RUN",
            "concept_refs": ["C1"],
            "entry": "N1",
            "nodes": [_node("N1", "runtime/flaky.py", on_fail="H1"),
                      _node("H1", "runtime/handler.py")],
            "edges": [
                {"from": "N1", "to": "H1", "kind": "refusal",
                 "when": "receipt.status == 'node_failed'"},
                {"from": "N1", "kind": "terminal",
                 "when": "receipt.status == 'ok'"},
                {"from": "H1", "kind": "terminal", "when": "true"},
            ],
        }],
    }
    dept = _make_dept(tmp_path, {"flaky.py": FAIL_NODE, "handler.py": HANDLER},
                      manifest)
    result = _run(dept, tmp_path)
    assert result["state"] == "done"
    state = json.loads(
        (dept / "state" / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))
    assert state["nodes"]["H1"]["state"] == "done"
    kinds = [t["kind"] for t in state["transitions"]]
    assert "refusal" in kinds


def test_output_contract_violation_blocks(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": BAD_SHAPE, "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "escalated"


# --------------------------------------------------------------------------- #
# Shadow enforcement + kill
# --------------------------------------------------------------------------- #

def test_external_action_in_shadow_kills_run(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": VIOLATOR, "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "killed"
    rows = [json.loads(line) for line in
            (dept / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "shadow_violation" for row in rows)


def test_kill_file_kills_run(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    (dept / "state").mkdir(exist_ok=True)
    (dept / "state" / "KILL").write_text("stop\n", encoding="utf-8")
    result = _run(dept, tmp_path)
    assert result["state"] == "killed"
    assert _markers(dept) == []


# --------------------------------------------------------------------------- #
# Run state machine
# --------------------------------------------------------------------------- #

def test_terminal_states_cannot_advance():
    machine = RUN.RunStateMachine()
    machine.advance("running")
    machine.advance("done")
    with pytest.raises(RUN.StateError):
        machine.advance("running")


def test_illegal_transition_rejected():
    machine = RUN.RunStateMachine()
    with pytest.raises(RUN.StateError):
        machine.advance("done")  # pending cannot jump straight to done
