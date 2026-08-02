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


def test_full_signed_tokens_persisted_and_durably_consumed(tmp_path):
    # B2: run records carry the FULL signed token (reverifiable after a
    # runner swap), each transition has its own token, and consumption is a
    # durable per-run ledger, not process memory.
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path)
    run_dir = dept / "state" / "graph_runs" / result["run_id"]
    state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
    tokens = [t["step_receipt"] for t in state["transitions"]]
    assert all(isinstance(tok, str) and "." in tok for tok in tokens)
    assert len(set(tokens)) == len(tokens)  # one fresh token per transition
    ledger = run_dir / "consumed_nonces.jsonl"
    assert ledger.exists()
    consumed = [json.loads(line)["nonce"] for line in
                ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(consumed) == len(tokens)


def test_run_lock_identity_uses_full_fingerprint_hash(tmp_path):
    import hashlib
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path, fingerprint="trigger-1")
    full = hashlib.sha256(b"trigger-1").hexdigest()
    assert result["run_id"] == f"SG-RUN-{full}"


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


def test_impl_edited_after_pin_refuses_release_integrity(tmp_path):
    # B1: the pin covers runtime artifacts, so a live impl whose bytes differ
    # from the pinned hash must never execute under that release_hash.
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    (dept / "runtime" / "sense.py").write_text(
        SENSE_OK + "\n# edited after pin\n", encoding="utf-8")
    with pytest.raises(RUN.RunnerRefused, match="release_integrity"):
        _run(dept, tmp_path)
    assert _markers(dept) == []
    rows = [json.loads(line) for line in
            (dept / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "run_refused"
               and "release_integrity" in row.get("reason", "") for row in rows)


def test_impl_missing_from_live_tree_refuses_release_integrity(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    (dept / "runtime" / "record.py").unlink()
    with pytest.raises(RUN.RunnerRefused, match="release_integrity"):
        _run(dept, tmp_path)


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


def _transitions(dept, result):
    state = json.loads(
        (dept / "state" / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))
    return state["transitions"]


def test_escalation_terminal_is_receipt_gated(tmp_path):
    # B3: on_fail=escalate must not reach a terminal run state without a
    # validated token — the runner's failure record earns a signed receipt
    # and the escalation is recorded as a receipt-bearing transition.
    dept = _make_dept(tmp_path, {"sense.py": FAIL_NODE, "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "escalated"
    rows = _transitions(dept, result)
    gated = [t for t in rows if t["from"] == "N1" and t["to"] is None
             and t["kind"] == "escalation"]
    assert gated and "." in gated[0]["step_receipt"]
    assert len(gated[0]["step_receipt_sha256"]) == 64


def test_fail_terminal_is_receipt_gated(tmp_path):
    manifest = _two_node_manifest()
    manifest["subgraphs"][0]["nodes"][0]["failure_policy"]["on_fail"] = "fail"
    dept = _make_dept(tmp_path, {"sense.py": FAIL_NODE, "record.py": RECORD_OK},
                      manifest)
    result = _run(dept, tmp_path)
    assert result["state"] == "failed"
    rows = _transitions(dept, result)
    gated = [t for t in rows if t["from"] == "N1" and t["to"] is None
             and t["kind"] == "failure"]
    assert gated and "." in gated[0]["step_receipt"]


def test_no_edge_satisfied_escalation_is_receipt_gated(tmp_path):
    manifest = _two_node_manifest()
    # both N1 out-edges require a status N1 never emits
    manifest["subgraphs"][0]["edges"][0]["when"] = "receipt.status == 'never'"
    manifest["subgraphs"][0]["edges"][1]["when"] = "receipt.status == 'never2'"
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      manifest)
    result = _run(dept, tmp_path)
    assert result["state"] == "escalated"
    rows = _transitions(dept, result)
    assert any(t["kind"] == "escalation" and t["to"] is None
               and "." in t["step_receipt"] for t in rows)


def test_non_finite_node_output_blocks(tmp_path):
    nonfinite = ('import json\n'
                 'print(\'{"status": "ok", "n": Infinity}\')\n')
    dept = _make_dept(tmp_path, {"sense.py": nonfinite, "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "escalated"  # unparseable under canonical policy


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


KILL_DURING_NODE = """\
import json, pathlib
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
with (state / "markers.txt").open("a", encoding="utf-8") as fh:
    fh.write("sense\\n")
(state / "KILL").write_text("stop\\n", encoding="utf-8")
print(json.dumps({"status": "ok", "delivered_count": 0}))
"""


def test_kill_raised_during_node_prevents_any_transition(tmp_path):
    # The kill switch is polled again AFTER node completion, BEFORE any
    # transition: a kill raised mid-node stops the graph walk cold.
    dept = _make_dept(tmp_path, {"sense.py": KILL_DURING_NODE,
                                 "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "killed"
    assert _markers(dept) == ["sense"]  # N1 ran, N2 never did
    assert _transitions(dept, result) == []


# --------------------------------------------------------------------------- #
# Wedged-run recovery (B5) — the idempotency lock must not entomb a crash
# --------------------------------------------------------------------------- #

def _run_id_for(fingerprint):
    import hashlib
    return "SG-RUN-" + hashlib.sha256(fingerprint.encode()).hexdigest()


def test_wedged_nonterminal_run_is_resumed_not_noop(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    run_dir = dept / "state" / "graph_runs" / _run_id_for("wedge-1")
    run_dir.mkdir(parents=True)
    (run_dir / "run_state.json").write_text(
        json.dumps({"schema": "graph-run-v1", "state": "awaiting_receipt"}),
        encoding="utf-8")  # a crash left the run mid-flight
    result = _run(dept, tmp_path, fingerprint="wedge-1")
    assert result["resumed"] is True
    assert result["duplicate"] is False
    assert result["state"] == "done"
    assert _markers(dept) == ["sense"]  # it actually re-executed
    rows = [json.loads(line) for line in
            (dept / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "run_resumed" for row in rows)


def test_crash_before_first_state_persist_recovers(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    run_dir = dept / "state" / "graph_runs" / _run_id_for("wedge-2")
    run_dir.mkdir(parents=True)  # crash between mkdir and first persist
    result = _run(dept, tmp_path, fingerprint="wedge-2")
    assert result["resumed"] is True
    assert result["state"] == "done"


def test_terminal_run_still_noops_on_duplicate(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    first = _run(dept, tmp_path, fingerprint="t-done")
    assert first["state"] == "done"
    second = _run(dept, tmp_path, fingerprint="t-done")
    assert second["duplicate"] is True
    assert _markers(dept) == ["sense"]


def test_pathological_predicate_graph_refused_cleanly_no_wedge(tmp_path):
    manifest = _two_node_manifest()
    depth = 64
    manifest["subgraphs"][0]["edges"][0]["when"] = (
        "(" * depth + "true" + ")" * depth)
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      manifest)
    with pytest.raises(RUN.RunnerRefused, match="depth"):
        _run(dept, tmp_path, fingerprint="patho")
    assert _markers(dept) == []
    assert not (dept / "state" / "graph_runs").exists()  # nothing wedged


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
