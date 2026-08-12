from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from factory import node_contract, runrecord
from kernel import run_manifest


ROOT = Path(__file__).parents[1]


def _document(department: str) -> dict:
    return json.loads((ROOT / "departments" / department / "node-contract.json").read_text())


def test_contract_inventory_covers_all_actual_occurrences_and_rosters_bind_it():
    expected_counts = {"sales": 6, "podcast": 19, "outreach": 7, "social": 20}
    for department, count in expected_counts.items():
        root = ROOT / "departments" / department
        document = node_contract.load(root)
        roster = json.loads((root / "runtime" / "run-roster.json").read_text())
        node_contract.validate_roster_document(roster, root)
        assert len(document["nodes"]) == count
        assert roster["contract"]["sha256"] == document["contract_sha256"]


def test_social_child_nodes_are_not_hidden_by_the_top_level_driver():
    document = _document("social")
    children = {(row["subgraph"], row["node_id"], row["impl"]) for row in document["nodes"]}
    assert ("SG-SENSE", "N1", "runtime/pull_zernio_analytics.py") in children
    assert ("SG-LEARN", "N9", "runtime/record.py") in children
    assert "social_daily_driver.py" not in {row["impl"] for row in document["nodes"]}


def test_outreach_orchestrator_is_a_composite_contract_occurrence():
    row = next(row for row in _document("outreach")["nodes"] if row["node_id"] == "ORCH")
    assert row["impl"] == "runtime/outreach_daily.sh"
    assert row["work_object"]["name"] == "receipt-gated daily cycle"


def test_optional_podcast_heal_nodes_remain_rostered_but_not_required():
    roster = json.loads((ROOT / "departments/podcast/runtime/run-roster.json").read_text())
    optional = {(row["subgraph"], row["node_id"]) for row in roster["nodes"] if not row["required"]}
    assert {("SG-HEAL", "N1"), ("SG-HEAL", "N2"), ("SG-HEAL", "N3")} <= optional


@pytest.mark.parametrize("field", ["work_object", "qa"])
def test_empty_contract_semantics_fail_closed(field):
    document = _document("sales")
    poisoned = copy.deepcopy(document)
    poisoned["nodes"][0][field] = {}
    with pytest.raises(node_contract.NodeContractRefused):
        node_contract.validate_document(poisoned, ROOT / "departments/sales")


@pytest.mark.parametrize("poison", [
    lambda row: row.update(qa={"verifier": "pytest", "evidence": "generic-receipt"}),
    lambda row: row.pop("qa"),
])
def test_missing_or_generic_qa_evidence_fails_closed(poison):
    document = _document("sales")
    poisoned = copy.deepcopy(document)
    poison(poisoned["nodes"][0])
    poisoned.pop("contract_sha256", None)
    with pytest.raises(node_contract.NodeContractRefused):
        node_contract.validate_document(poisoned, ROOT / "departments/sales")


def test_duplicate_node_ids_across_subgraphs_are_allowed_only_with_composite_identity():
    document = _document("podcast")
    ids = [row["node_id"] for row in document["nodes"]]
    assert ids.count("N1") >= 4
    assert len({(row["subgraph"], row["node_id"], row["impl"]) for row in document["nodes"]}) == len(document["nodes"])


def test_legacy_rev2_record_and_roster_cannot_be_green_capable():
    roster = json.loads((ROOT / "departments/social/runtime/run-roster.json").read_text())
    legacy = copy.deepcopy(roster)
    legacy["rev"] = 2
    with pytest.raises(node_contract.NodeContractRefused, match="legacy_roster_revision"):
        node_contract.validate_roster_document(legacy, ROOT / "departments/social")

    record = {
        "schema": runrecord.SCHEMA,
        "rev": 2,
        "run_id": "legacy",
        "department": "social",
        "node": "dispatch",
        "epoch": 1,
        "ts": "2026-08-09T12:00:00+00:00",
        "attempt": 1,
        "round": None,
        "release": None,
        "trigger": None,
        "engine": None,
        "model": None,
        "auth_class": None,
        "usage": None,
        "cost": {"lane": "flat_subscription", "model_calls": 0},
        "duration_ms": 1,
        "status": "ok",
        "errors": [],
        "artifacts": [{"path": "generic"}],
        "receipts": [],
        "evaluator": None,
        "approval": None,
        "external_actions_taken": 0,
    }
    validated = runrecord.build_record(**record)
    with pytest.raises(node_contract.NodeContractRefused, match="record_node_contract_missing"):
        node_contract.validate_bound_record(validated, ROOT / "departments/social")


def test_roster_rev2_is_rejected_by_manifest_validator():
    roster = json.loads((ROOT / "departments/sales/runtime/run-roster.json").read_text())
    roster["rev"] = 2
    with pytest.raises(run_manifest.ManifestRefused):
        run_manifest._validate_roster(roster, "sales")


def _bound_record(department: str, row: dict) -> dict:
    root = ROOT / "departments" / department
    document = node_contract.load(root)
    return runrecord.build_record(
        schema=runrecord.SCHEMA, rev=2, run_id="bound", department=department,
        node=Path(row["impl"]).stem, epoch=1, ts="2026-08-09T12:00:00+00:00",
        attempt=1, round=None, release=None, trigger=None, engine=None,
        model=None, auth_class=None, usage=None,
        cost={"lane": "flat_subscription", "model_calls": 0}, duration_ms=1,
        status="ok", errors=[], artifacts=[], receipts=[], evaluator=None,
        approval=None, external_actions_taken=0,
        node_contract={key: row[key] for key in ("department", "subgraph", "node_id", "impl")},
        contract_sha256=document["contract_sha256"], work_object_ref=row["work_object"],
        qa_receipt_ref=row["qa"],
    )


def test_cross_node_work_object_or_failed_qa_evidence_cannot_bind_a_record():
    document = _document("social")
    first, second = document["nodes"][0], document["nodes"][1]
    record = _bound_record("social", first)
    record["work_object_ref"] = second["work_object"]
    with pytest.raises(node_contract.NodeContractRefused, match="record_work_object_mismatch"):
        node_contract.validate_bound_record(record, ROOT / "departments/social")
    record = _bound_record("social", first)
    record["qa_receipt_ref"] = {"verifier": "pytest", "evidence": "failed-qa-receipt"}
    with pytest.raises(node_contract.NodeContractRefused, match="record_qa_evidence_mismatch"):
        node_contract.validate_bound_record(record, ROOT / "departments/social")


def test_missing_bound_qa_fields_are_not_repaired_from_generic_artifacts():
    row = _document("outreach")["nodes"][1]
    fields = _bound_record("outreach", row)
    fields.pop("qa_receipt_ref")
    with pytest.raises(ValueError, match="node_contract"):
        runrecord.validate_record(fields)
