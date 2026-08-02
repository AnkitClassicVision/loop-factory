"""Graph schema v2: typed nodes, explicit edges, safe predicates, reachability.

The predicate language is deliberately tiny and deterministic: comparisons and
boolean combinators over the PREDECESSOR'S RECEIPT JSON only. No eval(), no
attribute access, no calls. A predicate that cannot be evaluated (missing
field, type-mismatched ordering) raises PredicateError — deny-by-default, the
transition blocks; it never silently evaluates to True or False.
"""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RG = _load("rungraph", "factory/rungraph.py")


RECEIPT = {
    "status": "ok",
    "delivered_count": 0,
    "metrics": {"findings": 2, "score": 0.75},
    "flagged": False,
}


# --------------------------------------------------------------------------- #
# Predicate subset
# --------------------------------------------------------------------------- #

def test_true_literal_predicate():
    assert RG.eval_predicate("true", RECEIPT) is True


def test_equality_on_string_field():
    assert RG.eval_predicate("receipt.status == 'ok'", RECEIPT) is True
    assert RG.eval_predicate("receipt.status == 'failed'", RECEIPT) is False


def test_numeric_comparisons_on_nested_field():
    assert RG.eval_predicate("receipt.metrics.findings >= 2", RECEIPT) is True
    assert RG.eval_predicate("receipt.metrics.score < 0.5", RECEIPT) is False


def test_boolean_combinators_and_parentheses():
    assert RG.eval_predicate(
        "receipt.status == 'ok' and (receipt.delivered_count == 0 or receipt.flagged)",
        RECEIPT) is True
    assert RG.eval_predicate("not receipt.flagged", RECEIPT) is True


def test_cross_type_equality_blocks():
    # Python's == would coerce (1 == True, 0 == False) — a default-allow.
    # Contract: equality across TYPE FAMILIES blocks exactly like ordering.
    for expr in ("receipt.status != 3",
                 "receipt.flagged == 0",          # bool is not int here
                 "receipt.delivered_count == false",
                 "receipt.status == null"):
        with pytest.raises(RG.PredicateError):
            RG.eval_predicate(expr, RECEIPT)


def test_same_family_equality_still_works():
    assert RG.eval_predicate("receipt.flagged == false", RECEIPT) is True
    assert RG.eval_predicate("receipt.delivered_count == 0", RECEIPT) is True
    assert RG.eval_predicate("receipt.metrics.score == 0.75", RECEIPT) is True


def test_missing_field_blocks_instead_of_defaulting():
    with pytest.raises(RG.PredicateError):
        RG.eval_predicate("receipt.absent == 1", RECEIPT)


def test_ordering_across_types_blocks():
    with pytest.raises(RG.PredicateError):
        RG.eval_predicate("receipt.status > 3", RECEIPT)


def test_path_must_be_rooted_at_receipt():
    with pytest.raises(RG.PredicateError):
        RG.eval_predicate("__import__ == 1", RECEIPT)


def test_calls_attributes_and_subscripts_rejected():
    for expr in ("receipt.status.__class__ == 1",  # dunder segment
                 "open('x')",
                 "receipt.metrics[0] == 1",
                 "1 + 1 == 2"):
        with pytest.raises(RG.PredicateError):
            RG.eval_predicate(expr, RECEIPT)


def test_non_boolean_result_blocks():
    with pytest.raises(RG.PredicateError):
        RG.eval_predicate("receipt.delivered_count", RECEIPT)


def test_parse_rejects_trailing_garbage():
    with pytest.raises(RG.PredicateError):
        RG.eval_predicate("receipt.status == 'ok' extra", RECEIPT)


def test_nesting_depth_capped():
    depth = RG.MAX_PREDICATE_DEPTH + 8
    expr = "(" * depth + "true" + ")" * depth
    with pytest.raises(RG.PredicateError, match="depth"):
        RG.eval_predicate(expr, RECEIPT)
    assert RG.check_predicate(expr) is not None


def test_pathological_nesting_never_raises_recursionerror():
    depth = 5000  # far past any recursion limit — must still be PredicateError
    expr = "(" * depth + "true" + ")" * depth
    with pytest.raises(RG.PredicateError):
        RG.eval_predicate(expr, RECEIPT)


def test_expression_length_capped():
    expr = "receipt.status == 'ok'" + (" and true" * 600)
    assert len(expr) > RG.MAX_PREDICATE_LENGTH
    with pytest.raises(RG.PredicateError, match="length"):
        RG.eval_predicate(expr, RECEIPT)
    assert RG.check_predicate(expr) is not None


def test_depth_within_limit_still_evaluates():
    expr = "(" * 8 + "true" + ")" * 8
    assert RG.eval_predicate(expr, RECEIPT) is True


# --------------------------------------------------------------------------- #
# v2 manifest validation
# --------------------------------------------------------------------------- #

GRAPHS = _load("graphs_for_rungraph", "factory/graphs.py")


def _node(node_id, impl, **extra):
    base = {
        "id": node_id,
        "impl": impl,
        "runtime_mode": "script",
        "action_class": "observe",
        "inputs": {"type": "object"},
        "outputs": {"type": "object", "required": ["status"],
                    "properties": {"status": {"type": "string"}}},
        "receipt_schema": {"type": "object", "required": ["status"],
                           "properties": {"status": {"type": "string"}}},
        "failure_policy": {"max_retries": 1, "backoff_s": 0, "on_fail": "escalate"},
        "concept_ref": "C1",
        "interview_ref": "Q1",
    }
    base.update(extra)
    return base


def _v2_manifest():
    return {
        "schema_version": 2,
        "subgraphs": [{
            "id": "SG-RUN",
            "concept_refs": ["C1"],
            "not_applicable": {
                "S4": "read-only", "S5": "read-only",
                "S6": "read-only", "S7": "read-only",
                "S1": "no identity", "S2": "no eligibility", "S8": "no spend",
            },
            "entry": "N1",
            "nodes": [
                {"id": "S3", "guard": "S3"},
                _node("N1", "runtime/sense.py"),
                _node("N2", "runtime/record.py"),
            ],
            "edges": [
                {"from": "N1", "to": "N2", "kind": "normal",
                 "when": "receipt.status == 'ok'"},
                {"from": "N1", "kind": "escalation",
                 "when": "receipt.status != 'ok'"},
                {"from": "N2", "kind": "terminal", "when": "true"},
            ],
        }],
        "untraced_allowed": {},
    }


def test_manifest_version_defaults_to_v1():
    assert RG.manifest_version({"subgraphs": []}) == 1
    assert RG.manifest_version(_v2_manifest()) == 2


def test_v1_manifest_has_no_v2_failures():
    assert RG.validate_manifest({"subgraphs": [{"id": "SG-A", "nodes": []}]}) == []


def test_valid_v2_manifest_passes_and_v1_lint_still_passes():
    data = _v2_manifest()
    assert RG.validate_manifest(data) == []
    assert GRAPHS.validate_subgraphs(data) == []


def test_edge_to_unknown_node_fails():
    data = _v2_manifest()
    data["subgraphs"][0]["edges"][0]["to"] = "N9"
    assert any("unknown node" in f for f in RG.validate_manifest(data))


def test_terminal_edge_must_not_have_target():
    data = _v2_manifest()
    data["subgraphs"][0]["edges"][2]["to"] = "N1"
    assert any("terminal" in f for f in RG.validate_manifest(data))


def test_bad_edge_kind_fails():
    data = _v2_manifest()
    data["subgraphs"][0]["edges"][0]["kind"] = "sometimes"
    assert any("kind" in f for f in RG.validate_manifest(data))


def test_bad_predicate_caught_at_validation_time():
    data = _v2_manifest()
    data["subgraphs"][0]["edges"][0]["when"] = "receipt.status ==="
    assert any("predicate" in f for f in RG.validate_manifest(data))


def test_unreachable_node_fails():
    data = _v2_manifest()
    data["subgraphs"][0]["nodes"].append(_node("N3", "runtime/orphan.py"))
    fails = RG.validate_manifest(data)
    assert any("unreachable" in f and "N3" in f for f in fails)


def test_node_without_terminal_path_fails():
    data = _v2_manifest()
    sg = data["subgraphs"][0]
    sg["nodes"].append(_node("N3", "runtime/dead_end.py"))
    sg["edges"].append({"from": "N2", "to": "N3", "kind": "normal", "when": "true"})
    sg["edges"] = [e for e in sg["edges"] if e["kind"] != "terminal"]
    sg["edges"].append({"from": "N2", "kind": "terminal", "when": "true"})
    fails = RG.validate_manifest(data)
    assert any("terminal" in f and "N3" in f for f in fails)


def test_missing_entry_fails():
    data = _v2_manifest()
    del data["subgraphs"][0]["entry"]
    assert any("entry" in f for f in RG.validate_manifest(data))


def test_executable_node_missing_v2_fields_fails():
    data = _v2_manifest()
    del data["subgraphs"][0]["nodes"][1]["runtime_mode"]
    del data["subgraphs"][0]["nodes"][2]["receipt_schema"]
    fails = RG.validate_manifest(data)
    assert any("runtime_mode" in f for f in fails)
    assert any("receipt_schema" in f for f in fails)


def test_bad_runtime_mode_fails():
    data = _v2_manifest()
    data["subgraphs"][0]["nodes"][1]["runtime_mode"] = "bash"
    assert any("runtime_mode" in f for f in RG.validate_manifest(data))


def test_on_fail_node_target_must_have_matching_edge():
    data = _v2_manifest()
    data["subgraphs"][0]["nodes"][1]["failure_policy"]["on_fail"] = "N2"
    fails = RG.validate_manifest(data)
    assert any("on_fail" in f for f in fails)


def test_traceability_refs_required_on_executable_nodes():
    data = _v2_manifest()
    del data["subgraphs"][0]["nodes"][1]["concept_ref"]
    del data["subgraphs"][0]["nodes"][2]["interview_ref"]
    fails = RG.validate_manifest(data)
    assert any("concept_ref" in f for f in fails)
    assert any("interview_ref" in f for f in fails)


def test_unknown_contract_keyword_fails_closed():
    data = _v2_manifest()
    data["subgraphs"][0]["nodes"][1]["outputs"] = {
        "type": "object", "pattern": ".*"}
    assert any("pattern" in f for f in RG.validate_manifest(data))


def test_duplicate_node_ids_fail():
    data = _v2_manifest()
    data["subgraphs"][0]["nodes"].append(_node("N2", "runtime/record.py"))
    assert any("duplicate" in f for f in RG.validate_manifest(data))


# --------------------------------------------------------------------------- #
# JSON-schema-lite instance validation
# --------------------------------------------------------------------------- #

CONTRACT = {
    "type": "object",
    "required": ["status", "counts"],
    "properties": {
        "status": {"type": "string", "enum": ["ok", "alarm"]},
        "counts": {"type": "object", "required": ["runs"],
                   "properties": {"runs": {"type": "integer"}}},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}


def test_instance_conforms():
    value = {"status": "ok", "counts": {"runs": 3}, "tags": ["a"]}
    assert RG.validate_instance(CONTRACT, value) == []


def test_instance_violations_reported():
    value = {"status": "bad", "counts": {"runs": "three"}, "tags": [1]}
    fails = RG.validate_instance(CONTRACT, value)
    assert any("enum" in f for f in fails)
    assert any("runs" in f for f in fails)
    assert any("tags" in f for f in fails)


def test_missing_required_reported():
    fails = RG.validate_instance(CONTRACT, {"status": "ok"})
    assert any("counts" in f for f in fails)


def test_boolean_is_not_integer():
    fails = RG.validate_instance(
        {"type": "object", "properties": {"n": {"type": "integer"}}}, {"n": True})
    assert fails


def test_non_finite_numbers_rejected_at_contract_boundary():
    schema = {"type": "object", "properties": {"n": {"type": "number"}}}
    for bad in (float("nan"), float("inf"), float("-inf")):
        fails = RG.validate_instance(schema, {"n": bad})
        assert any("finite" in f for f in fails)
    assert RG.validate_instance(schema, {"n": 0.5}) == []


# --------------------------------------------------------------------------- #
# Template drift guard
# --------------------------------------------------------------------------- #

def test_v2_template_passes_both_v1_lint_and_v2_validation():
    import json
    data = json.loads((ROOT / "templates" / "subgraphs-v2.json.tmpl")
                      .read_text(encoding="utf-8"))
    assert RG.manifest_version(data) == RG.GRAPH_SCHEMA_VERSION
    assert RG.validate_manifest(data) == []
    assert GRAPHS.validate_subgraphs(data) == []
