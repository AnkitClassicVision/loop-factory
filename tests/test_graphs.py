"""Map QA layer: guard-matrix lint, node↔artifact traceability, release drift.

Pins the process-change contract: an invalid map fails lint; a runtime file
with no graph node fails traceability unless explicitly allowlisted with a
rationale; a live tree that differs from the pinned release is drift.
"""
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


G = _load("graphs", "factory/graphs.py")
REL = _load("release", "factory/release.py")


def _send_subgraph(nodes):
    return {"subgraphs": [{"id": "SG-T", "concept_refs": ["C3"],
                           "not_applicable": {"S8": "no cost nodes"}, "nodes": nodes}]}


GOOD_SEND_NODES = [
    {"id": "S1", "guard": "S1"},
    {"id": "S2", "guard": "S2"},
    {"id": "S3", "guard": "S3"},
    {"id": "S5", "guard": "S5"},
    {"id": "S4", "guard": "S4"},
    {"id": "S6", "guard": "S6"},
    {"id": "S7", "guard": "S7"},
    {"id": "N6", "external_dispatch": True},
]


def test_well_ordered_send_subgraph_passes():
    assert G.validate_subgraphs(_send_subgraph(GOOD_SEND_NODES)) == []


def test_dispatch_without_send_authorization_fails():
    nodes = [n for n in GOOD_SEND_NODES if n["id"] != "S4"]
    fails = G.validate_subgraphs(_send_subgraph(nodes))
    assert any("missing prior S4" in f for f in fails)


def test_model_node_without_privacy_preflight_fails():
    data = _send_subgraph([{"id": "N4", "model_capable": True}] + GOOD_SEND_NODES)
    fails = G.validate_subgraphs(data)
    assert any("not preceded by S3" in f for f in fails)


def test_cost_node_without_budget_reserve_fails():
    nodes = list(GOOD_SEND_NODES) + [{"id": "N9", "cost_incurring": True}]
    data = {"subgraphs": [{"id": "SG-T", "concept_refs": ["C3"], "not_applicable": {},
                           "nodes": nodes}]}
    fails = G.validate_subgraphs(data)
    assert any("not preceded by S8" in f for f in fails)


def test_read_only_funnel_must_justify_skipped_send_guards():
    data = {"subgraphs": [{"id": "SG-RO", "concept_refs": ["C2"],
                           "not_applicable": {"S8": "read only"},
                           "nodes": [{"id": "S1", "guard": "S1"}, {"id": "S2", "guard": "S2"},
                                     {"id": "S3", "guard": "S3"}]}]}
    fails = G.validate_subgraphs(data)
    assert any("must mark S4 not_applicable" in f for f in fails)


def test_empty_rationale_is_rejected():
    data = {"subgraphs": [{"id": "SG-T", "concept_refs": ["C3"],
                           "not_applicable": {"S8": "  "},
                           "nodes": GOOD_SEND_NODES}]}
    fails = G.validate_subgraphs(data)
    assert any("empty rationale" in f for f in fails)


def test_missing_concept_refs_fails():
    data = {"subgraphs": [{"id": "SG-T", "not_applicable": {"S8": "no cost nodes"},
                           "nodes": GOOD_SEND_NODES}]}
    fails = G.validate_subgraphs(data)
    assert any("missing concept_refs" in f for f in fails)


def test_dispatch_requires_both_controllers():
    nodes = [n for n in GOOD_SEND_NODES if n["id"] != "S7"]
    fails = G.validate_subgraphs(_send_subgraph(nodes))
    assert any("missing fresh S7" in f for f in fails)


def test_crm_write_without_crm_auth_fails():
    nodes = list(GOOD_SEND_NODES) + [{"id": "N9", "crm_write": True}]
    fails = G.validate_subgraphs(_send_subgraph(nodes))
    assert any("CRM write not preceded by crm_authorization" in f for f in fails)


# --------------------------------------------------------------------------- #
# Traceability
# --------------------------------------------------------------------------- #

def _dept(tmp_path, subgraphs, runtime_files=()):
    dept = tmp_path / "departments" / "t"
    (dept / "runtime").mkdir(parents=True)
    (dept / "subgraphs.json").write_text(json.dumps(subgraphs), encoding="utf-8")
    for name in runtime_files:
        (dept / "runtime" / name).write_text("# node\n", encoding="utf-8")
    return dept


def test_untraced_runtime_artifact_fails(tmp_path):
    dept = _dept(tmp_path, {"subgraphs": [{"id": "S", "nodes": []}]}, ["orphan.py"])
    fails = G.check_traceability(dept)
    assert any("orphan.py" in f and "traces to no graph node" in f for f in fails)


def test_allowlisted_untraced_artifact_passes(tmp_path):
    data = {"subgraphs": [{"id": "S", "nodes": []}],
            "untraced_allowed": {"runtime/bridge.py": "factory wiring, not a node"}}
    dept = _dept(tmp_path, data, ["bridge.py"])
    assert G.check_traceability(dept) == []


def test_declared_impl_must_exist(tmp_path):
    data = {"subgraphs": [{"id": "S", "nodes": [{"id": "N1", "impl": "runtime/missing.py"}]}]}
    dept = _dept(tmp_path, data)
    fails = G.check_traceability(dept)
    assert any("does not exist on disk" in f for f in fails)


def test_traced_impl_passes(tmp_path):
    data = {"subgraphs": [{"id": "S", "nodes": [{"id": "N1", "impl": "runtime/step.py"}]}]}
    dept = _dept(tmp_path, data, ["step.py"])
    assert G.check_traceability(dept) == []


# --------------------------------------------------------------------------- #
# Drift (process changed without re-pin)
# --------------------------------------------------------------------------- #

def test_drift_detected_after_unpinned_process_change(tmp_path):
    data = {"subgraphs": [{"id": "S", "nodes": [{"id": "N1", "impl": "runtime/step.py"}]}]}
    dept = _dept(tmp_path, data, ["step.py"])
    releases = dept / "releases"
    h = REL.pin_release(dept, releases, source_ref="test")
    REL.flip_current(releases, h)
    assert G.check_drift(dept, releases)["ok"] is True
    # the process changes without a re-pin
    (dept / "runtime" / "step.py").write_text("# changed behavior\n", encoding="utf-8")
    drift = G.check_drift(dept, releases)
    assert drift["ok"] is False
    assert "runtime/step.py" in drift["mismatches"]


def test_qa_verdict_aggregates(tmp_path):
    data = {"subgraphs": [{"id": "S", "nodes": [{"id": "N1", "impl": "runtime/step.py"}]}]}
    dept = _dept(tmp_path, data, ["step.py"])
    verdict = G.qa(dept)
    # lint flags missing universal guards on this minimal graph; verdict must fail
    assert verdict["ok"] is False
    assert verdict["traceability"] == []
