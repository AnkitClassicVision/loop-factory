import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "graphs", ROOT / "factory/graphs.py"
)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)


def _subgraph(node):
    return {
        "id": "SG-COMMS",
        "concept_refs": ["C1"],
        "not_applicable": {
            guard: "test subgraph has no external dispatch"
            for guard in G.ALL_GUARDS
        },
        "nodes": [node],
    }


def _comms_errors(node):
    return [
        error for error in G.validate_subgraph(_subgraph(node))
        if "emits_ask node" in error
    ]


def test_emits_ask_requires_return_path_and_sla():
    errors = _comms_errors({"id": "N-ASK", "emits_ask": True})

    assert all("SG-COMMS/N-ASK" in error for error in errors)
    assert any("return_path" in error for error in errors)
    assert any("return_sla_hours" in error for error in errors)


def test_emits_ask_with_return_contract_has_no_comms_errors():
    errors = _comms_errors({
        "id": "N-ASK",
        "emits_ask": True,
        "return_path": "answer_reader",
        "return_sla_hours": 24,
        "unknown_future_field": {"allowed": True},
    })

    assert errors == []


def test_node_without_emits_ask_has_no_comms_errors():
    assert _comms_errors({"id": "N-PLAIN"}) == []


@pytest.mark.parametrize("return_sla_hours", [0, -1])
def test_emits_ask_rejects_non_positive_sla(return_sla_hours):
    errors = _comms_errors({
        "id": "N-ASK",
        "emits_ask": True,
        "return_path": "answer_reader",
        "return_sla_hours": return_sla_hours,
    })

    assert any(
        "SG-COMMS/N-ASK" in error and "return_sla_hours" in error
        for error in errors
    )
