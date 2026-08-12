#!/usr/bin/env python3
"""Append one validated run-record/v2 sidecar row for a completed stage."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import node_contract, runrecord


class ReceiptValidationError(ValueError):
    """The stage receipt cannot pass the existing receipt gate."""


def _read_receipt(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
        try:
            values = [json.loads(text)]
        except json.JSONDecodeError:
            values = [json.loads(line) for line in text.splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptValidationError("stage receipt is not readable JSON") from exc
    if not values or any(not isinstance(value, dict) for value in values):
        raise ReceiptValidationError("stage receipt must contain one or more JSON objects")
    invalid_statuses = {"blocked", "missing", "failed", "error"}
    if any(not value or value.get("status") in invalid_statuses for value in values):
        raise ReceiptValidationError("stage receipt contains an invalid status")
    return values


def _external_actions(receipts: list[dict], node: str) -> int:
    for receipt in receipts:
        value = receipt.get("external_actions_taken")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    if node == "N6":
        for receipt in receipts:
            for field in ("dispatched_count", "delivered_count"):
                value = receipt.get(field)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    return value
    return 1 if node == "N10" and any(
        receipt.get("status") == "card_created" for receipt in receipts
    ) else 0


def append_stage_record(
    state_dir: Path,
    receipt_path: Path,
    *,
    run_id: str,
    node: str,
    department_dir: Path | None = None,
) -> dict:
    receipt_rows = _read_receipt(receipt_path)
    receipt = receipt_rows[-1]
    status = "skipped" if any(
        row.get("status") == "yielded" for row in receipt_rows
    ) else "ok"
    epoch = receipt.get("epoch", 0)
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        epoch = 0
    department_dir = department_dir or REPO_ROOT / "departments" / "social"
    graph_node = node.split("-", 1)[0]
    impl_by_node = {
        "N1": "runtime/inventory_backcatalog.py",
        "N2": "runtime/select_candidate.py",
        "N3": "runtime/assemble_context.py",
        "N4": "runtime/draft_post.py",
        "N5": "runtime/qa_post.py",
        "N6": "runtime/dispatch.py",
        "N7": "runtime/delivery_verify.py",
        "N9": "runtime/record.py",
        "N10": "runtime/create_review_card.py",
        "N11": "runtime/harvest_review_asks.py",
    }
    impl = impl_by_node.get(graph_node)
    if impl is None:
        raise ReceiptValidationError(f"unmapped social graph node: {node}")
    declared = node_contract.lookup(
        department_dir, subgraph="SG-REPUBLISH", node_id=graph_node, impl=impl
    )
    record = runrecord.build_record(
        schema=runrecord.SCHEMA,
        rev=2,
        run_id=run_id,
        department="social",
        node=Path(impl).stem,
        epoch=epoch,
        ts=datetime.now(timezone.utc).isoformat(),
        attempt=1,
        round=None,
        release=runrecord.read_release(department_dir),
        trigger={"kind": "time", "id": "social-daily", "dedupe_key": run_id},
        engine=None,
        model=None,
        auth_class=None,
        usage=None,
        cost={"lane": "flat_subscription", "model_calls": 0},
        duration_ms=None,
        status=status,
        errors=[],
        artifacts=[{"kind": "stage_receipt", "path": str(receipt_path)}],
        receipts=[{"path": str(receipt_path), "status": receipt.get("status", "ok")}],
        evaluator=None,
        approval=None,
        external_actions_taken=_external_actions(receipt_rows, node),
        node_contract={
            "department": "social", "subgraph": "SG-REPUBLISH",
            "node_id": graph_node, "impl": impl,
        },
        contract_sha256=node_contract.load(department_dir)["contract_sha256"],
        work_object_ref=declared["work_object"],
        qa_receipt_ref=declared["qa"],
    )
    runrecord.append_record(state_dir, record)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--node", required=True)
    args = parser.parse_args(argv)
    append_stage_record(
        args.state_dir, args.receipt, run_id=args.run_id, node=args.node
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
