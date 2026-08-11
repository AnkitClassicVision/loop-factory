from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from factory.approve_dispatch import DispatchInputError, dispatch_pending, main


def _append(path: Path, *rows: object) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _files(tmp_path: Path, *, kind: str = "send") -> tuple[Path, Path, Path]:
    ledger = tmp_path / "card-ledger.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    receipts = tmp_path / "action-receipts.jsonl"
    original = {
        "ts": "2026-08-11T12:00:00+00:00",
        "first_raised": "2026-08-11T12:00:00+00:00",
        "row_hash": "content-v1",
        "department": "podcast",
        "kind": kind,
        "summary": "Send approved draft",
        "packet_text": "sanitized approved payload",
        "card_identifier": "ANK-321",
        "card_url": "https://linear.example/ANK-321",
        "status": "open",
        "urgency": "normal",
    }
    decided = {
        "ts": "2026-08-11T12:05:00+00:00",
        "row_hash": "content-v1",
        "department": "podcast",
        "kind": kind,
        "card_identifier": "ANK-321",
        "status": "decided:approve",
    }
    decision = {
        "ts": "2026-08-11T12:05:00+00:00",
        "card_identifier": "ANK-321",
        "row_hash": "content-v1",
        "row_hashes": ["content-v1"],
        "department": "podcast",
        "kind": kind,
        "decision": "approve",
        "source": "linear-comment",
        "first_line": "APPROVE",
    }
    _append(ledger, original, decided)
    _append(decisions, decision)
    receipts.write_text("", encoding="utf-8")
    return ledger, decisions, receipts


def test_shadow_default_reports_pending_without_running(tmp_path):
    ledger, decisions, receipts = _files(tmp_path)
    calls = []

    report = dispatch_pending(
        ledger, decisions, receipts, {"send": ["sender", "--approved"]},
        run_handler=lambda argv, payload: calls.append((argv, payload)),
    )

    assert report["mode"] == "shadow"
    assert report["dispatched"] == 0
    assert report["pending"][0]["card_identifier"] == "ANK-321"
    assert report["pending"][0]["kind"] == "send"
    assert report["pending"][0]["handler_argv"] == ["sender", "--approved"]
    assert report["pending"][0]["decision_age_seconds"] >= 0
    assert calls == []


def test_apply_success_receipts_and_second_run_is_replay_safe(tmp_path):
    ledger, decisions, receipts = _files(tmp_path)
    calls = []

    def runner(argv, payload):
        calls.append((argv, json.loads(payload)))
        return 0, "", ""

    first = dispatch_pending(
        ledger, decisions, receipts, {"send": ["sender"]}, apply=True,
        run_handler=runner,
    )
    second = dispatch_pending(
        ledger, decisions, receipts, {"send": ["sender"]}, apply=True,
        run_handler=runner,
    )

    assert first["dispatched"] == 1
    assert len(calls) == 1
    assert calls[0][1]["decision"] == "approve"
    receipt = json.loads(receipts.read_text(encoding="utf-8"))
    assert receipt["card_identifier"] == "ANK-321"
    assert receipt["handler_exit"] == 0
    assert len(receipt["decision_hash"]) == 64
    assert second["dispatched"] == 0
    assert second["skipped_receipted"] == 1
    assert second["pending"] == []


def test_nonzero_exit_has_no_receipt_and_cli_exits_one(tmp_path):
    ledger, decisions, receipts = _files(tmp_path)
    report = dispatch_pending(
        ledger, decisions, receipts, {"send": ["sender"]}, apply=True,
        run_handler=lambda argv, payload: (3, "", "handler refused"),
    )
    assert report["failed"] == 1
    assert report["pending"][0]["failure"]["exit_code"] == 3
    assert receipts.read_text(encoding="utf-8") == ""

    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"send": [sys.executable, "-c", "import sys; sys.exit(3)"]}),
        encoding="utf-8",
    )
    assert main([
        "--ledger", str(ledger), "--decisions", str(decisions),
        "--receipts", str(receipts), "--registry", str(registry), "--apply",
    ]) == 1


def test_unknown_kind_is_unhandled_and_never_executed(tmp_path):
    ledger, decisions, receipts = _files(tmp_path, kind="unregistered-action")
    calls = []
    report = dispatch_pending(
        ledger, decisions, receipts, {}, apply=True,
        run_handler=lambda argv, payload: calls.append((argv, payload)),
    )
    assert report["unhandled"] == 1
    assert report["pending"][0]["unhandled"] is True
    assert calls == []


def test_new_approval_after_content_change_dispatches_once(tmp_path):
    ledger, decisions, receipts = _files(tmp_path)
    calls = []
    runner = lambda argv, payload: (calls.append(json.loads(payload)) or (0, "", ""))
    registry = {"send": ["sender"]}
    dispatch_pending(ledger, decisions, receipts, registry, apply=True, run_handler=runner)

    _append(
        ledger,
        {
            "ts": "2026-08-11T13:00:00+00:00", "first_raised": "2026-08-11T13:00:00+00:00",
            "row_hash": "content-v2", "department": "podcast", "kind": "send",
            "summary": "Revised draft", "packet_text": "revised sanitized payload",
            "card_identifier": "ANK-321", "status": "open", "urgency": "normal",
        },
        {
            "ts": "2026-08-11T13:05:00+00:00", "row_hash": "content-v2",
            "department": "podcast", "kind": "send", "card_identifier": "ANK-321",
            "status": "decided:approve",
        },
    )
    _append(
        decisions,
        {
            "ts": "2026-08-11T13:05:00+00:00", "card_identifier": "ANK-321",
            "row_hash": "content-v2", "row_hashes": ["content-v2"],
            "department": "podcast", "kind": "send", "decision": "approve",
            "source": "linear-comment", "first_line": "APPROVE revised",
        },
    )
    second = dispatch_pending(
        ledger, decisions, receipts, registry, apply=True, run_handler=runner,
    )
    third = dispatch_pending(
        ledger, decisions, receipts, registry, apply=True, run_handler=runner,
    )
    assert second["dispatched"] == 1
    assert third["dispatched"] == 0
    assert len(calls) == 2
    assert len({json.loads(line)["decision_hash"] for line in receipts.read_text().splitlines()}) == 2


def test_unreadable_input_refuses_but_malformed_row_is_counted(tmp_path):
    ledger, decisions, receipts = _files(tmp_path)
    missing = tmp_path / "missing-ledger.jsonl"
    with pytest.raises(DispatchInputError):
        dispatch_pending(missing, decisions, receipts, {"send": ["sender"]})

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"send": ["sender"]}), encoding="utf-8")
    assert main([
        "--ledger", str(missing), "--decisions", str(decisions),
        "--receipts", str(receipts), "--registry", str(registry),
    ]) == 2

    with ledger.open("a", encoding="utf-8") as handle:
        handle.write("{malformed row\n")
    report = dispatch_pending(ledger, decisions, receipts, {"send": ["sender"]})
    assert report["malformed"] == 1
    assert len(report["pending"]) == 1


def test_latest_decided_status_wins_over_older_open_row(tmp_path):
    ledger, decisions, receipts = _files(tmp_path)
    report = dispatch_pending(ledger, decisions, receipts, {"send": ["sender"]})
    assert report["scanned"] == 2
    assert [row["card_identifier"] for row in report["pending"]] == ["ANK-321"]
