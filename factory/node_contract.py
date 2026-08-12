"""Deterministic node-contract/v1 inventory and binding checks.

The graph is the inventory authority.  A department contract supplies the
source-grounded work object and QA evidence for every graph implementation
occurrence.  The composite identity intentionally includes the subgraph and
implementation path because node ids and implementations may be shared.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA = "node-contract/v1"
REV = 1
CONTRACT_FILE = "node-contract.json"


class NodeContractRefused(ValueError):
    """A node contract cannot safely bind graph truth to runtime evidence."""


def _fail(reason: str) -> None:
    raise NodeContractRefused(reason)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_impl(value: Any) -> str:
    if not _nonempty(value):
        _fail("impl_missing")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not str(path).startswith("runtime/"):
        _fail("impl_path_invalid")
    return str(path)


def _identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        row["department"], row["subgraph"], row["node_id"], row["impl"]
    )


def canonical_contract_bytes(document: dict[str, Any]) -> bytes:
    """Return the digest payload, excluding an optional self-reported digest."""
    value = dict(document)
    value.pop("contract_sha256", None)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def contract_digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_contract_bytes(document)).hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NodeContractRefused(f"contract_unreadable:{path}") from exc


def derive_inventory(department_dir: str | Path) -> list[dict[str, str]]:
    """Derive every implementation occurrence from subgraphs.json."""
    root = Path(department_dir)
    department = root.name
    graph_path = root / "subgraphs.json"
    graph = _load_json(graph_path)
    if not isinstance(graph, dict) or not isinstance(graph.get("subgraphs"), list):
        _fail("graph_schema_invalid")
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for subgraph in graph["subgraphs"]:
        if not isinstance(subgraph, dict) or not _nonempty(subgraph.get("id")):
            _fail("subgraph_identity_invalid")
        subgraph_id = subgraph["id"]
        nodes = subgraph.get("nodes")
        if not isinstance(nodes, list):
            _fail(f"{subgraph_id}:nodes_invalid")
        for node in nodes:
            if not isinstance(node, dict) or not _nonempty(node.get("id")):
                _fail(f"{subgraph_id}:node_identity_invalid")
            impl = node.get("impl")
            if impl is None:
                continue
            impl = _safe_impl(impl)
            identity = (department, subgraph_id, node["id"], impl)
            if identity in seen:
                _fail("duplicate_node_contract_identity:" + "/".join(identity[1:]))
            seen.add(identity)
            impl_path = root / impl
            if not impl_path.is_file():
                _fail(f"untraced_impl:{impl}")
            rows.append({
                "department": department,
                "subgraph": subgraph_id,
                "node_id": node["id"],
                "impl": impl,
            })
    return sorted(rows, key=lambda row: (row["subgraph"], row["node_id"], row["impl"]))


def _validate_work_object(value: Any, index: int) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"name", "consumer", "artifact_or_field"}:
        _fail(f"nodes[{index}].work_object_invalid")
    if not all(_nonempty(value[key]) for key in value):
        _fail(f"nodes[{index}].work_object_empty")
    return {key: value[key] for key in ("name", "consumer", "artifact_or_field")}


def _validate_qa(value: Any, index: int, department_dir: Path) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"verifier", "evidence"}:
        _fail(f"nodes[{index}].qa_invalid")
    if not all(_nonempty(value[key]) for key in value):
        _fail(f"nodes[{index}].qa_empty")
    evidence = value["evidence"]
    evidence_path = evidence.split("::", 1)[0]
    if not evidence_path.startswith("tests/") or not (
        (department_dir / evidence_path).is_file()
        or (department_dir.parent.parent / evidence_path).is_file()
    ):
        _fail(f"nodes[{index}].qa_evidence_unresolved")
    return {"verifier": value["verifier"], "evidence": evidence}


def validate_document(document: Any, department_dir: str | Path) -> dict[str, Any]:
    """Validate a contract and reconcile it exactly to graph-derived truth."""
    root = Path(department_dir)
    if not isinstance(document, dict) or set(document) - {"schema", "rev", "department", "nodes", "contract_sha256"}:
        _fail("contract_schema_invalid")
    if document.get("schema") != SCHEMA or document.get("rev") != REV:
        _fail("contract_revision_invalid")
    if document.get("department") != root.name or not isinstance(document.get("nodes"), list):
        _fail("contract_department_invalid")
    expected = derive_inventory(root)
    actual: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(document["nodes"]):
        if not isinstance(row, dict) or set(row) != {"department", "subgraph", "node_id", "impl", "work_object", "qa"}:
            _fail(f"nodes[{index}].schema_invalid")
        impl = _safe_impl(row.get("impl"))
        if not all(_nonempty(row.get(key)) for key in ("department", "subgraph", "node_id")):
            _fail(f"nodes[{index}].identity_invalid")
        normalized = {
            "department": row["department"],
            "subgraph": row["subgraph"],
            "node_id": row["node_id"],
            "impl": impl,
            "work_object": _validate_work_object(row.get("work_object"), index),
            "qa": _validate_qa(row.get("qa"), index, root),
        }
        identity = _identity(normalized)
        if identity in seen:
            _fail("duplicate_contract_identity:" + "/".join(identity[1:]))
        seen.add(identity)
        actual.append(normalized)
    actual.sort(key=lambda row: (row["subgraph"], row["node_id"], row["impl"]))
    expected_ids = {_identity(row) for row in expected}
    actual_ids = {_identity(row) for row in actual}
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        _fail(f"contract_inventory_mismatch:missing={missing}:extra={extra}")
    if "contract_sha256" in document and document["contract_sha256"] != contract_digest(document):
        _fail("contract_digest_mismatch")
    return {
        "schema": SCHEMA,
        "rev": REV,
        "department": root.name,
        "nodes": actual,
        "contract_sha256": contract_digest({
            "schema": SCHEMA, "rev": REV, "department": root.name, "nodes": actual
        }),
    }


def load(department_dir: str | Path) -> dict[str, Any]:
    root = Path(department_dir)
    document = _load_json(root / CONTRACT_FILE)
    return validate_document(document, root)


def index(department_dir: str | Path) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    document = load(department_dir)
    return {_identity(row): row for row in document["nodes"]}


def lookup(department_dir: str | Path, *, subgraph: str, node_id: str, impl: str | None = None) -> dict[str, Any]:
    """Resolve one identity; ambiguous implementation aliases refuse."""
    rows = [row for row in load(department_dir)["nodes"] if row["subgraph"] == subgraph and row["node_id"] == node_id]
    if impl is not None:
        rows = [row for row in rows if row["impl"] == impl]
    if len(rows) != 1:
        _fail("node_contract_identity_ambiguous")
    return rows[0]


def lookup_by_node(department_dir: str | Path, node: str) -> dict[str, Any]:
    """Resolve a runtime stem only when its graph identity is unique."""
    rows = [row for row in load(department_dir)["nodes"] if Path(row["impl"]).stem == node]
    if len(rows) != 1:
        _fail("node_contract_identity_ambiguous")
    return rows[0]


def validate_bound_record(record: dict[str, Any], department_dir: str | Path) -> dict[str, Any]:
    """Require a record's identity, digest, work object, and QA evidence."""
    document = load(department_dir)
    identity = record.get("node_contract")
    if not isinstance(identity, dict) or set(identity) != {"department", "subgraph", "node_id", "impl"}:
        _fail("record_node_contract_missing")
    key = (identity.get("department"), identity.get("subgraph"), identity.get("node_id"), identity.get("impl"))
    declared = index(department_dir).get(key)
    if declared is None:
        _fail("record_node_contract_unknown")
    if record.get("department") != declared["department"] or record.get("node") != Path(declared["impl"]).stem:
        _fail("record_node_contract_mismatch")
    if record.get("contract_sha256") != document["contract_sha256"]:
        _fail("record_contract_digest_mismatch")
    if record.get("work_object_ref") != declared["work_object"]:
        _fail("record_work_object_mismatch")
    if record.get("qa_receipt_ref") != declared["qa"]:
        _fail("record_qa_evidence_mismatch")
    return declared


def validate_roster_document(roster: Any, department_dir: str | Path) -> dict[str, Any]:
    """Reconcile a rev3 roster exactly to the graph-derived contract."""
    root = Path(department_dir)
    document = load(root)
    if not isinstance(roster, dict) or roster.get("schema") != "run-roster" or roster.get("rev") != 3:
        _fail("legacy_roster_revision")
    contract = roster.get("contract")
    if not isinstance(contract, dict) or contract.get("sha256") != document["contract_sha256"]:
        _fail("roster_contract_digest_mismatch")
    rows = roster.get("nodes")
    if not isinstance(rows, list):
        _fail("roster_nodes_invalid")
    actual = set()
    for row in rows:
        if not isinstance(row, dict):
            _fail("roster_node_invalid")
        impl = row.get("impl")
        identity = (roster.get("department"), row.get("subgraph"), row.get("node_id"), impl)
        if identity in actual:
            _fail("duplicate_roster_identity")
        actual.add(identity)
    expected = {_identity(row) for row in document["nodes"]}
    if actual != expected:
        _fail("roster_contract_inventory_mismatch")
    return document
