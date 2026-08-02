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
SR = _load("runner_step_receipts", "kernel/step_receipts.py")
RUN = _load("runner", "factory/runner.py")

SIGNER = R.LocalSigner(key="test-key")


class _FlakyVerifySigner:
    """Signs correctly; the first `fail_first` verifies report False. Models a
    transient verification failure so the escalation path itself must gate."""

    def __init__(self, inner, fail_first=1):
        self._inner = inner
        self._fails_left = fail_first

    def sign(self, payload):
        return self._inner.sign(payload)

    def verify(self, payload, sig):
        if self._fails_left > 0:
            self._fails_left -= 1
            return False
        return self._inner.verify(payload, sig)


class _DeadVerifySigner:
    """Signs correctly; every verify fails — a broken signing plane."""

    def __init__(self, inner):
        self._inner = inner

    def sign(self, payload):
        return self._inner.sign(payload)

    def verify(self, payload, sig):
        return False


class _RaisingSignSigner:
    """sign() raises after the first `allow` calls — an HSM/key service that
    dies mid-run. Exceptions must terminate as killed, never escape."""

    def __init__(self, inner, allow=0):
        self._inner = inner
        self._allow = allow

    def sign(self, payload):
        if self._allow > 0:
            self._allow -= 1
            return self._inner.sign(payload)
        raise RuntimeError("signing service unavailable")

    def verify(self, payload, sig):
        return self._inner.verify(payload, sig)


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
# R1: a FAILED token verification must not reach a terminal state bare —
# the escalation itself is receipt-gated; only a broken signing plane may
# fall through, and that is a safety kill, not a bare escalation.
# --------------------------------------------------------------------------- #

def _state_json(dept, result):
    return json.loads(
        (dept / "state" / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))


def test_failed_verification_escalation_is_itself_receipt_gated(tmp_path):
    import time as time_mod
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    flaky = _FlakyVerifySigner(R.LocalSigner(key="test-key"), fail_first=1)
    result = RUN.run_graph(dept, trigger_fingerprint="flaky-1", signer=flaky,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "escalated"
    state = _state_json(dept, result)
    gated = [t for t in state["transitions"]
             if t["kind"] == "escalation" and t["to"] is None
             and str(t.get("note", "")).startswith("verification_failed")]
    assert gated, state["transitions"]
    # the escalation receipt itself must reverify with the real key
    check = SR.reverify_transition(gated[0], record=state,
                                   signer=R.LocalSigner(key="test-key"),
                                   now=time_mod.time())
    assert check.ok, check.reason
    assert str(state["termination_reason"]).startswith("verification_failed")


def test_dead_signing_plane_falls_to_safety_kill(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    dead = _DeadVerifySigner(R.LocalSigner(key="test-key"))
    result = RUN.run_graph(dept, trigger_fingerprint="dead-1", signer=dead,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "killed"
    rows = [json.loads(line) for line in
            (dept / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "gate_failure" for row in rows)
    state = _state_json(dept, result)
    assert str(state["termination_reason"]).startswith("gate_failure")


def test_on_fail_escalate_gate_failure_also_safety_kills(tmp_path):
    # Failure path variant: the node fails AND the escalation gate cannot
    # validate — a bare escalated terminal would be an unreceipted exit.
    dept = _make_dept(tmp_path, {"sense.py": FAIL_NODE, "record.py": RECORD_OK},
                      _two_node_manifest())
    dead = _DeadVerifySigner(R.LocalSigner(key="test-key"))
    result = RUN.run_graph(dept, trigger_fingerprint="dead-2", signer=dead,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "killed"


# --------------------------------------------------------------------------- #
# R2: every persisted transition row is independently reverifiable from
# (key service + run records) alone — no runner, no output bodies.
# --------------------------------------------------------------------------- #

def test_transitions_reverifiable_from_persisted_records_only(tmp_path):
    import time as time_mod
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path)
    state = _state_json(dept, result)
    assert state["department"] == "demo"  # identity is self-contained
    assert state["transitions"]
    for row in state["transitions"]:
        assert len(row["output_sha256"]) == 64
        check = SR.reverify_transition(row, record=state, signer=SIGNER,
                                       now=time_mod.time())
        assert check.ok, (row["from"], check.reason)


def test_shadow_kill_records_signed_termination_reason(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": VIOLATOR, "record.py": RECORD_OK},
                      _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "killed"
    assert _state_json(dept, result)["termination_reason"] == "shadow_violation"
    proj = json.loads((dept / "state" / "receipts" / "execution-projection.json")
                      .read_text(encoding="utf-8"))
    assert PJ.verify_projection(proj, SIGNER) == []  # reason is under the sig
    assert proj["runs"][0]["termination_reason"] == "shadow_violation"


# --------------------------------------------------------------------------- #
# R3: resume CONTINUES from the persisted frontier — completed nodes are not
# re-executed, and their tokens survive into the auditor-visible projection.
# --------------------------------------------------------------------------- #

def _mark_node(marker):
    return (f'import json, pathlib\n'
            f'state = pathlib.Path(__file__).resolve().parents[1] / "state"\n'
            f'state.mkdir(parents=True, exist_ok=True)\n'
            f'with (state / "markers.txt").open("a", encoding="utf-8") as fh:\n'
            f'    fh.write("{marker}\\n")\n'
            f'print(json.dumps({{"status": "ok"}}))\n')


FLAKY_N3 = """\
import json, pathlib, sys
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
m = state / "markers.txt"
with m.open("a", encoding="utf-8") as fh:
    fh.write("n3\\n")
if m.read_text(encoding="utf-8").splitlines().count("n3") == 1:
    sys.exit(9)
print(json.dumps({"status": "ok"}))
"""


def _three_node_manifest():
    n3 = _node("N3", "runtime/n3.py", max_retries=1)
    n3["failure_policy"]["backoff_s"] = 1  # forces a sleep_fn call on retry
    return {
        "schema_version": 2,
        "subgraphs": [{
            "id": "SG-RUN",
            "concept_refs": ["C1"],
            "entry": "N1",
            "nodes": [_node("N1", "runtime/n1.py"),
                      _node("N2", "runtime/n2.py"), n3],
            "edges": [
                {"from": "N1", "to": "N2", "kind": "normal",
                 "when": "receipt.status == 'ok'"},
                {"from": "N2", "to": "N3", "kind": "normal",
                 "when": "receipt.status == 'ok'"},
                {"from": "N3", "kind": "terminal",
                 "when": "receipt.status == 'ok'"},
            ],
        }],
    }


def test_resume_continues_from_frontier_not_entry(tmp_path):
    dept = _make_dept(tmp_path, {"n1.py": _mark_node("n1"),
                                 "n2.py": _mark_node("n2"),
                                 "n3.py": FLAKY_N3},
                      _three_node_manifest())

    def crash_sleep(seconds):
        raise RuntimeError("simulated crash during N3 retry backoff")

    with pytest.raises(RuntimeError, match="simulated crash"):
        RUN.run_graph(dept, trigger_fingerprint="cont-1", signer=SIGNER,
                      root=tmp_path, sleep_fn=crash_sleep)
    assert _markers(dept) == ["n1", "n2", "n3"]
    run_id = _run_id_for("cont-1")
    pre = json.loads((dept / "state" / "graph_runs" / run_id / "run_state.json")
                     .read_text(encoding="utf-8"))
    assert pre["state"] not in ("done", "failed", "escalated", "killed")
    pre_tokens = [t["step_receipt"] for t in pre["transitions"]]
    assert len(pre_tokens) == 2  # N1->N2, N2->N3 survived the crash

    second = RUN.run_graph(dept, trigger_fingerprint="cont-1", signer=SIGNER,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert second["resumed"] is True
    assert second["state"] == "done"
    # N1/N2 executed exactly once across both processes; only N3 re-ran
    assert _markers(dept) == ["n1", "n2", "n3", "n3"]
    state = _state_json(dept, second)
    tokens = [t["step_receipt"] for t in state["transitions"]]
    assert tokens[:2] == pre_tokens  # prior token material preserved
    assert [(t["from"], t["to"]) for t in state["transitions"]] == [
        ("N1", "N2"), ("N2", "N3"), ("N3", None)]
    proj = json.loads((dept / "state" / "receipts" / "execution-projection.json")
                      .read_text(encoding="utf-8"))
    proj_run = [r for r in proj["runs"] if r["run_id"] == run_id][0]
    assert [t["step_receipt"] for t in proj_run["transitions"]][:2] == pre_tokens


# --------------------------------------------------------------------------- #
# R4: a HELD .run.lock (live concurrent runner) no-ops cleanly.
# --------------------------------------------------------------------------- #

def _rewrite_state(dept, run_id, mutate):
    path = dept / "state" / "graph_runs" / run_id / "run_state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    mutate(state)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


# --------------------------------------------------------------------------- #
# C1: signing-plane exceptions terminate as killed + durable finding —
# they never escape the runner.
# --------------------------------------------------------------------------- #

def test_signing_exception_terminates_killed_with_finding(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    raising = _RaisingSignSigner(R.LocalSigner(key="test-key"))
    result = RUN.run_graph(dept, trigger_fingerprint="raise-1", signer=raising,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "killed"
    rows = [json.loads(line) for line in
            (dept / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "gate_failure" for row in rows)
    state = _state_json(dept, result)
    assert str(state["termination_reason"]).startswith("gate_failure")


# --------------------------------------------------------------------------- #
# C2: the receipt-bearing escalation row carries the CONCRETE failed check.
# --------------------------------------------------------------------------- #

def test_escalation_row_carries_the_failed_check(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    flaky = _FlakyVerifySigner(R.LocalSigner(key="test-key"), fail_first=1)
    result = RUN.run_graph(dept, trigger_fingerprint="flaky-2", signer=flaky,
                           root=tmp_path, sleep_fn=lambda s: None)
    assert result["state"] == "escalated"
    gated = [t for t in _transitions(dept, result)
             if str(t.get("note", "")).startswith("verification_failed")]
    assert gated and gated[0]["failed_check"] == "signature"


# --------------------------------------------------------------------------- #
# C3: durable per-edge completion checkpoint — resume trusts explicit
# decision state, reverified receipts, never row-shape inference.
# --------------------------------------------------------------------------- #

HANDLER_MARKING = """\
import json, pathlib
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
with (state / "markers.txt").open("a", encoding="utf-8") as fh:
    fh.write("handled\\n")
print(json.dumps({"status": "handled"}))
"""


def _refusal_manifest():
    return {
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


def test_resume_after_routed_failure_does_not_requeue_or_double_feed(tmp_path):
    # (a) crash after the refusal transition fired but before the handler ran:
    # the FAILED node must not re-execute; the handler is fed exactly once.
    dept = _make_dept(tmp_path, {"flaky.py": FAIL_NODE,
                                 "handler.py": HANDLER_MARKING},
                      _refusal_manifest())
    first = _run(dept, tmp_path, fingerprint="routed-1")
    assert first["state"] == "done"
    assert _markers(dept) == ["fail", "handled"]
    run_id = first["run_id"]

    def crash_before_handler(state):
        state["state"] = "running"
        del state["nodes"]["H1"]
        state["transitions"] = [t for t in state["transitions"]
                                if t["from"] != "H1"]
    _rewrite_state(dept, run_id, crash_before_handler)
    second = _run(dept, tmp_path, fingerprint="routed-1")
    assert second["resumed"] is True
    assert second["state"] == "done"
    assert _markers(dept) == ["fail", "handled", "handled"]  # N1 NOT re-run
    feeds = [t for t in _transitions(dept, second)
             if t["from"] == "N1" and t["to"] == "H1"]
    assert len(feeds) == 1  # no double-feed


def _fanout_manifest():
    return {
        "schema_version": 2,
        "subgraphs": [{
            "id": "SG-RUN",
            "concept_refs": ["C1"],
            "entry": "N1",
            "nodes": [_node("N1", "runtime/n1.py"),
                      _node("N2", "runtime/n2.py"),
                      _node("N3", "runtime/n3.py")],
            "edges": [
                {"from": "N1", "to": "N2", "kind": "normal", "when": "true"},
                {"from": "N1", "to": "N3", "kind": "normal", "when": "true"},
                {"from": "N2", "kind": "terminal", "when": "true"},
                {"from": "N3", "kind": "terminal", "when": "true"},
            ],
        }],
    }


def test_resume_fires_remaining_fanout_edges(tmp_path):
    # (b) crash after the FIRST outgoing transition of a fan-out: the second
    # satisfied edge must still fire on resume — not silently drop.
    dept = _make_dept(tmp_path, {"n1.py": _mark_node("n1"),
                                 "n2.py": _mark_node("n2"),
                                 "n3.py": _mark_node("n3")},
                      _fanout_manifest())
    first = _run(dept, tmp_path, fingerprint="fan-1")
    assert first["state"] == "done"
    run_id = first["run_id"]

    def crash_after_first_edge(state):
        state["state"] = "running"
        decisions = state["nodes"]["N1"]["decisions"]
        n3_edge = [d for d in decisions if d.get("to") == "N3"][0]
        n3_edge["state"] = "pending"
        del state["nodes"]["N3"]
        state["transitions"] = [t for t in state["transitions"]
                                if "N3" not in (t["from"], t["to"])]
    _rewrite_state(dept, run_id, crash_after_first_edge)
    second = _run(dept, tmp_path, fingerprint="fan-1")
    assert second["resumed"] is True
    assert second["state"] == "done"
    markers = _markers(dept)
    assert markers.count("n1") == 1  # fan-out source not re-executed
    assert markers.count("n2") == 1  # completed branch untouched
    assert markers.count("n3") == 2  # dropped branch re-fed and executed
    assert any(t["from"] == "N1" and t["to"] == "N3"
               for t in _transitions(dept, second))


def test_resume_concludes_persisted_failure_exit_without_new_rows(tmp_path):
    # (c) a fired failure exit is TERMINAL: resume concludes failed — it never
    # appends a second exit row nor mutates failed into escalated.
    manifest = _two_node_manifest()
    manifest["subgraphs"][0]["nodes"][0]["failure_policy"]["on_fail"] = "fail"
    dept = _make_dept(tmp_path, {"sense.py": FAIL_NODE, "record.py": RECORD_OK},
                      manifest)
    first = _run(dept, tmp_path, fingerprint="exit-1")
    assert first["state"] == "failed"
    rows_before = len(_transitions(dept, first))
    _rewrite_state(dept, first["run_id"],
                   lambda s: s.update(state="awaiting_receipt"))
    second = _run(dept, tmp_path, fingerprint="exit-1")
    assert second["resumed"] is True
    assert second["state"] == "failed"  # not escalated
    assert len(_transitions(dept, second)) == rows_before  # no new rows
    assert _markers(dept) == ["fail"]  # no re-execution
    assert _state_json(dept, second)["termination_reason"] == "on_fail_fail"


def test_tampered_record_refuses_resume(tmp_path):
    # (d) prior receipts are REVERIFIED before the frontier is trusted.
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    first = _run(dept, tmp_path, fingerprint="tamper-1")
    assert first["state"] == "done"

    def tamper(state):
        state["state"] = "running"
        token = state["transitions"][0]["step_receipt"]
        state["transitions"][0]["step_receipt"] = token[:-4] + "AAAA"
    _rewrite_state(dept, first["run_id"], tamper)
    with pytest.raises(RUN.RunnerRefused, match="resume_integrity"):
        _run(dept, tmp_path, fingerprint="tamper-1")
    assert _markers(dept) == ["sense"]  # nothing executed


# --------------------------------------------------------------------------- #
# C4: a re-pin between crash and resume refuses — preserved tokens must not
# be re-homed under a release they were never issued for.
# --------------------------------------------------------------------------- #

def test_repin_between_crash_and_resume_refuses(tmp_path):
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    first = _run(dept, tmp_path, fingerprint="repin-1")
    assert first["state"] == "done"
    before = _state_json(dept, first)
    _rewrite_state(dept, first["run_id"],
                   lambda s: s.update(state="running"))
    manifest = _two_node_manifest()
    manifest["_note"] = "process change after the crash"
    (dept / "subgraphs.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    h = REL.pin_release(dept, dept / "releases", source_ref="repin")
    REL.flip_current(dept / "releases", h)
    with pytest.raises(RUN.RunnerRefused, match="release_integrity"):
        _run(dept, tmp_path, fingerprint="repin-1")
    assert _markers(dept) == ["sense"]  # no execution
    after = _state_json(dept, first)
    assert after["transitions"] == before["transitions"]  # tokens untouched
    rows = [json.loads(line) for line in
            (dept / "state" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "run_refused"
               and "release_integrity" in row.get("reason", "") for row in rows)


def test_held_run_lock_noops_cleanly(tmp_path):
    import subprocess as subprocess_mod
    import sys as sys_mod
    dept = _make_dept(tmp_path, {"sense.py": SENSE_OK, "record.py": RECORD_OK},
                      _two_node_manifest())
    run_dir = dept / "state" / "graph_runs" / _run_id_for("held-1")
    run_dir.mkdir(parents=True)
    locker_src = ("import fcntl, sys, time\n"
                  "handle = open(sys.argv[1], 'a+')\n"
                  "fcntl.flock(handle.fileno(), fcntl.LOCK_EX)\n"
                  "print('locked', flush=True)\n"
                  "time.sleep(30)\n")
    locker = subprocess_mod.Popen(
        [sys_mod.executable, "-c", locker_src, str(run_dir / ".run.lock")],
        stdout=subprocess_mod.PIPE, text=True)
    try:
        assert locker.stdout.readline().strip() == "locked"
        result = _run(dept, tmp_path, fingerprint="held-1")
        assert result["duplicate"] is True
        assert result["state"] == "in_flight"
        assert _markers(dept) == []
    finally:
        locker.terminate()
        locker.wait()


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
