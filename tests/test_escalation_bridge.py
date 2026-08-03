"""R2 — runner-to-manager escalation bridge.

A terminal graph `escalated` (or `killed`) state must never vanish into the
void: the runner appends a durable, manager-readable escalation record to
department state; the manager's next cycle senses it, raises a breach
finding, and delivers exactly one human-in-the-loop outbox item. Report +
escalate only — the bridge grants no new authority and no graph transition
advances past the failure.
"""
import importlib.util
import json
from pathlib import Path

from factory import human_in_the_loop as hil
from factory import manager


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = _load("bridge_kernel_receipts", "kernel/receipts.py")
REL = _load("bridge_release", "factory/release.py")
RUN = _load("bridge_runner", "factory/runner.py")

SIGNER = R.LocalSigner(key="test-key")

ESCALATING_NODE = """\
import json
print(json.dumps({"status": "broken"}))
"""

VIOLATOR_NODE = """\
import json
print(json.dumps({"status": "ok", "external_actions_taken": 1}))
"""

OK_NODE = """\
import json
print(json.dumps({"status": "ok"}))
"""

RECORD_NODE = """\
import json, pathlib
state = pathlib.Path(__file__).resolve().parents[1] / "state"
state.mkdir(parents=True, exist_ok=True)
with (state / "markers.txt").open("a", encoding="utf-8") as fh:
    fh.write("record\\n")
print(json.dumps({"status": "ok"}))
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


def _run(dept, tmp_path, fingerprint="trigger-1"):
    return RUN.run_graph(dept, trigger_fingerprint=fingerprint, signer=SIGNER,
                         root=tmp_path, sleep_fn=lambda s: None)


def _read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# --------------------------------------------------------------------------- #
# The runner writes the bridge record on terminal escalated/killed only
# --------------------------------------------------------------------------- #

def test_escalated_run_writes_bridge_record(tmp_path):
    dept = _make_dept(
        tmp_path, {"sense.py": ESCALATING_NODE, "record.py": RECORD_NODE},
        _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "escalated"
    rows = _read_jsonl(dept / "state" / "graph_escalations.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == result["run_id"]
    assert row["graph_run_id"] == result["run_id"]
    assert row["state"] == "escalated"
    assert row["department"] == "demo"
    assert row["loop_id"] == "SG-RUN"
    assert row["termination_reason"] == "escalation_edge"


def test_killed_run_writes_bridge_record(tmp_path):
    dept = _make_dept(
        tmp_path, {"sense.py": VIOLATOR_NODE, "record.py": RECORD_NODE},
        _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "killed"
    rows = _read_jsonl(dept / "state" / "graph_escalations.jsonl")
    assert len(rows) == 1
    assert rows[0]["state"] == "killed"
    assert rows[0]["termination_reason"] == "shadow_violation"


def test_done_run_writes_no_bridge_record(tmp_path):
    dept = _make_dept(
        tmp_path, {"sense.py": OK_NODE, "record.py": RECORD_NODE},
        _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "done"
    assert not (dept / "state" / "graph_escalations.jsonl").exists()


# --------------------------------------------------------------------------- #
# The manager senses the bridge record and raises a breach finding
# --------------------------------------------------------------------------- #

def test_sense_graph_escalations_replays_open_and_resolved(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    path = state / "graph_escalations.jsonl"
    rows = [
        {"run_id": "run-a", "state": "escalated", "loop_id": "SG",
         "termination_reason": "no_edge_satisfied", "marker": "open"},
        {"run_id": "run-b", "state": "killed", "loop_id": "SG",
         "termination_reason": "shadow_violation", "marker": "open"},
        {"run_id": "run-b", "marker": "resolved"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows),
                    encoding="utf-8")
    sensed = manager.sense_graph_escalations(state)
    assert sensed["graph_escalation_count"] == 1
    assert sensed["graph_escalations"][0]["run_id"] == "run-a"
    assert sensed["graph_escalations_unreadable"] is False


def test_sense_graph_escalations_unreadable_is_flagged(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "graph_escalations.jsonl").write_text(
        "{not json\n", encoding="utf-8")
    sensed = manager.sense_graph_escalations(state)
    assert sensed["graph_escalations_unreadable"] is True


def test_compare_raises_breach_per_open_graph_escalation():
    sensed = {
        "graph_escalations": [
            {"run_id": "run-a", "state": "escalated", "loop_id": "SG",
             "termination_reason": "no_edge_satisfied"},
        ],
    }
    findings = manager.compare(sensed)
    breaches = [f for f in findings if f["code"] == "graph_run_escalated"]
    assert len(breaches) == 1
    assert breaches[0]["severity"] == "breach"
    assert breaches[0]["subject"] == "run-a"
    assert "run-a" in breaches[0]["detail"]


def test_compare_raises_breach_when_escalation_stream_unreadable():
    findings = manager.compare({"graph_escalations_unreadable": True})
    assert any(f["code"] == "graph_escalations_unreadable"
               and f["severity"] == "breach" for f in findings)


# --------------------------------------------------------------------------- #
# ACCEPTANCE: force a predicate failure -> exactly one terminal graph
# escalation + one manager breach finding on the next cycle + one outbox
# item; no graph transition advances.
# --------------------------------------------------------------------------- #

def test_predicate_failure_reaches_manager_and_outbox_exactly_once(tmp_path):
    dept = _make_dept(
        tmp_path, {"sense.py": ESCALATING_NODE, "record.py": RECORD_NODE},
        _two_node_manifest())
    result = _run(dept, tmp_path)
    assert result["state"] == "escalated"
    state_dir = dept / "state"

    # exactly one terminal graph escalation, and NO transition advanced:
    # the only receipt-bearing row is the escalation exit — N2 never ran.
    run_state = json.loads(
        (state_dir / "graph_runs" / result["run_id"] / "run_state.json")
        .read_text(encoding="utf-8"))
    assert run_state["state"] == "escalated"
    assert [(t["from"], t["to"], t["kind"]) for t in run_state["transitions"]] \
        == [("N1", None, "escalation")]
    assert "N2" not in run_state["nodes"]
    assert not (state_dir / "markers.txt").exists()
    assert len(_read_jsonl(state_dir / "graph_escalations.jsonl")) == 1

    # next manager cycle: one breach finding + one outbox item
    outbox = tmp_path / "outbox.jsonl"

    def escalate_fn(issue, context=None):
        hil.escalate("demo", issue, outbox, context=context)

    report = manager.run_manager_cycle(
        state_dir, escalate_fn=escalate_fn, department="demo", dept_dir=dept)
    breaches = [f for f in report["findings"]
                if f["code"] == "graph_run_escalated"]
    assert len(breaches) == 1
    state_json = json.loads(
        (state_dir / "STATE.json").read_text(encoding="utf-8"))
    assert any(f["code"] == "graph_run_escalated"
               for f in state_json["open_findings"])
    packets = [row for row in _read_jsonl(outbox)
               if row.get("kind") == "escalation"
               and "graph_run_escalated" in row.get("issue", "")]
    assert len(packets) == 1
    assert result["run_id"] in packets[0]["issue"]

    # a second cycle must NOT deliver a duplicate outbox item
    manager.run_manager_cycle(
        state_dir, escalate_fn=escalate_fn, department="demo", dept_dir=dept)
    packets = [row for row in _read_jsonl(outbox)
               if row.get("kind") == "escalation"
               and "graph_run_escalated" in row.get("issue", "")]
    assert len(packets) == 1


def test_resolution_clears_both_ledgers_and_reopen_realarm(tmp_path):
    """B2 regression: escalate -> resolve -> re-escalate the SAME run must
    deliver a SECOND outbox item. Resolution is one coordinated operation
    over BOTH ledgers (fingerprints first, then the graph sensor, so a crash
    between the two re-alarms noisily instead of going silent)."""
    dept = _make_dept(
        tmp_path, {"sense.py": ESCALATING_NODE, "record.py": RECORD_NODE},
        _two_node_manifest())
    result = _run(dept, tmp_path)
    state_dir = dept / "state"
    outbox = tmp_path / "outbox.jsonl"

    def escalate_fn(issue, context=None):
        hil.escalate("demo", issue, outbox, context=context)

    def packets():
        return [row for row in _read_jsonl(outbox)
                if row.get("kind") == "escalation"
                and "graph_run_escalated" in row.get("issue", "")]

    manager.run_manager_cycle(
        state_dir, escalate_fn=escalate_fn, department="demo", dept_dir=dept)
    assert len(packets()) == 1

    outcome = manager.resolve_graph_escalation(
        state_dir, department="demo", run_id=result["run_id"])
    assert outcome["resolved"] == result["run_id"]
    # both ledgers carry a coordinated resolution marker
    fp_rows = _read_jsonl(state_dir / "escalation_fingerprints.jsonl")
    assert any(r.get("marker") == "resolved" for r in fp_rows)
    esc_rows = _read_jsonl(state_dir / "graph_escalations.jsonl")
    assert any(r.get("marker") == "resolved"
               and r.get("run_id") == result["run_id"] for r in esc_rows)

    # resolved: no finding, no delivery
    report = manager.run_manager_cycle(
        state_dir, escalate_fn=escalate_fn, department="demo", dept_dir=dept)
    assert not any(f["code"] == "graph_run_escalated"
                   for f in report["findings"])
    assert len(packets()) == 1

    # REOPEN the same run: the alarm must ring again, not dedup into silence
    with (state_dir / "graph_escalations.jsonl").open(
            "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "run_id": result["run_id"], "loop_id": "SG-RUN",
            "state": "escalated", "termination_reason": "escalation_edge",
            "marker": "open"}) + "\n")
    report = manager.run_manager_cycle(
        state_dir, escalate_fn=escalate_fn, department="demo", dept_dir=dept)
    assert any(f["code"] == "graph_run_escalated" for f in report["findings"])
    assert len(packets()) == 2


def test_escalation_fingerprints_use_full_digest(tmp_path):
    dept = _make_dept(
        tmp_path, {"sense.py": ESCALATING_NODE, "record.py": RECORD_NODE},
        _two_node_manifest())
    _run(dept, tmp_path)
    state_dir = dept / "state"
    manager.run_manager_cycle(
        state_dir, escalate_fn=lambda issue, context=None: None,
        department="demo", dept_dir=dept)
    rows = _read_jsonl(state_dir / "escalation_fingerprints.jsonl")
    assert rows and all(len(r["fingerprint"]) == 64 for r in rows)


def test_more_than_twenty_open_escalations_all_surface(tmp_path):
    """B3 regression: 25 open escalations -> 25 breach findings and 25 outbox
    deliveries in one cycle; only the persisted STATE.json presentation is
    bounded (20 entries, truncated flag, honest total)."""
    state_dir = tmp_path / "departments" / "demo" / "state"
    state_dir.mkdir(parents=True)
    with (state_dir / "graph_escalations.jsonl").open(
            "w", encoding="utf-8") as fh:
        for index in range(25):
            fh.write(json.dumps({
                "run_id": f"SG-RUN-{index:03d}", "loop_id": "SG-RUN",
                "state": "escalated", "termination_reason": "escalation_edge",
                "marker": "open"}) + "\n")
    outbox = tmp_path / "outbox.jsonl"

    def escalate_fn(issue, context=None):
        hil.escalate("demo", issue, outbox, context=context)

    report = manager.run_manager_cycle(
        state_dir, escalate_fn=escalate_fn, department="demo")
    breaches = [f for f in report["findings"]
                if f["code"] == "graph_run_escalated"]
    assert len(breaches) == 25
    assert {f["subject"] for f in breaches} \
        == {f"SG-RUN-{index:03d}" for index in range(25)}
    packets = [row for row in _read_jsonl(outbox)
               if row.get("kind") == "escalation"
               and "graph_run_escalated" in row.get("issue", "")]
    assert len(packets) == 25
    assert report["sensed"]["graph_escalation_count"] == 25
    state_json = json.loads(
        (state_dir / "STATE.json").read_text(encoding="utf-8"))
    persisted = state_json["sensed"]
    assert len(persisted["graph_escalations"]) == 20
    assert persisted["graph_escalations_truncated"] is True
    assert persisted["graph_escalation_count"] == 25


def test_resolved_escalation_clears_the_finding(tmp_path):
    dept = _make_dept(
        tmp_path, {"sense.py": ESCALATING_NODE, "record.py": RECORD_NODE},
        _two_node_manifest())
    result = _run(dept, tmp_path)
    state_dir = dept / "state"
    with (state_dir / "graph_escalations.jsonl").open(
            "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run_id": result["run_id"],
                             "marker": "resolved"}) + "\n")
    report = manager.run_manager_cycle(
        state_dir, department="demo", dept_dir=dept)
    assert not any(f["code"] == "graph_run_escalated"
                   for f in report["findings"])
