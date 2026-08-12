import json
import threading
from pathlib import Path

import pytest

from factory import human_in_the_loop


def test_save_load_roundtrip_unchanged(tmp_path):
    queue = tmp_path / "approval_queue.jsonl"
    rows = [{"id": 1, "status": "pending_approval"}, {"id": 2, "draft": "hello"}]

    human_in_the_loop._save(queue, rows)

    assert human_in_the_loop._load(queue) == rows


def test_crash_before_replace_leaves_original_intact_and_parseable(tmp_path, monkeypatch):
    queue = tmp_path / "approval_queue.jsonl"
    original = [{"id": "original", "status": "pending_approval"}]
    human_in_the_loop._save(queue, original)

    def crash_before_replace(source, destination):
        raise OSError("simulated crash")

    monkeypatch.setattr(human_in_the_loop.os, "replace", crash_before_replace)

    with pytest.raises(OSError, match="simulated crash"):
        human_in_the_loop._save(queue, [{"id": "replacement"}])

    assert human_in_the_loop._load(queue) == original
    assert json.loads(queue.read_text(encoding="utf-8")) == original[0]


def test_successful_save_leaves_no_stray_temp_files(tmp_path):
    queue = tmp_path / "approval_queue.jsonl"

    human_in_the_loop._save(queue, [{"id": 1}])

    assert queue in list(tmp_path.iterdir())
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_saves_leave_parseable_file(tmp_path):
    queue = tmp_path / "approval_queue.jsonl"
    barrier = threading.Barrier(2)
    errors = []

    def save(rows):
        try:
            barrier.wait()
            human_in_the_loop._save(queue, rows)
        except Exception as exc:
            errors.append(exc)

    rows_a = [{"writer": "a", "index": index} for index in range(100)]
    rows_b = [{"writer": "b", "index": index} for index in range(100)]
    threads = [threading.Thread(target=save, args=(rows,)) for rows in (rows_a, rows_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert human_in_the_loop._load(queue) in (rows_a, rows_b)


def test_concurrent_appends_preserve_every_escalation_row(tmp_path):
    ledger = tmp_path / "escalations.jsonl"
    barrier = threading.Barrier(2)
    errors = []

    def append_row(index):
        try:
            barrier.wait()
            human_in_the_loop._append(ledger, [{"id": index}])
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=append_row, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert {row["id"] for row in human_in_the_loop._load(ledger)} == {1, 2}


def test_empty_queue_save_works(tmp_path):
    queue = tmp_path / "approval_queue.jsonl"

    human_in_the_loop._save(queue, [])

    assert queue.read_text(encoding="utf-8") == ""
    assert human_in_the_loop._load(queue) == []


def test_concurrent_conflicting_decisions_only_one_can_close_pending_row(tmp_path):
    queue = tmp_path / "approval_queue.jsonl"
    human_in_the_loop._save(
        queue,
        [{"decision_id": "one", "status": "pending_approval"}],
    )

    barrier = threading.Barrier(2)
    results = []

    def decide(verdict):
        barrier.wait()
        results.append(human_in_the_loop.apply(queue, "one", verdict))

    threads = [threading.Thread(target=decide, args=(verdict,)) for verdict in ("APPROVE", "REJECT")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result["applied"] for result in results) == [False, True]
    refusal = next(result for result in results if not result["applied"])
    assert refusal["error"] == "not pending (replay or already decided)"
    row = human_in_the_loop._load(queue)[0]
    assert row["status"] in {"approved", "rejected"}


def test_concurrent_resolution_attempts_leave_one_terminal_row(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "escalations.jsonl"
    opened = human_in_the_loop.escalate(
        "sales",
        "truth_contract_failed",
        outbox,
        owner="human-owner",
        deadline="2026-08-01T00:00:00Z",
        next_action="review the signed verdict",
        ledger_path=ledger,
    )
    receipt = tmp_path / "decision.json"
    receipt.write_text('{"decision":"repair approved"}\n', encoding="utf-8")

    # The old implementation built both terminal rows before either append.
    # This gate makes that ordering deterministic without blocking the fixed
    # implementation, which appends through its deliberately locked helper.
    original_append = human_in_the_loop._append
    append_barrier = threading.Barrier(2)

    def adversarial_append(path, rows):
        if (
            Path(path) == ledger
            and rows
            and rows[0].get("status") == "resolved"
        ):
            append_barrier.wait(timeout=5)
        return original_append(path, rows)

    monkeypatch.setattr(human_in_the_loop, "_append", adversarial_append)

    results = []
    errors = []

    def resolve_once():
        try:
            results.append(
                human_in_the_loop.resolve_escalation(
                    ledger,
                    opened["escalation_id"],
                    owner="human-owner",
                    decided_at="2026-08-02T01:00:00Z",
                    action="approve a source-only repair",
                    receipt_path=receipt,
                    receipt_root=tmp_path,
                )
            )
        except Exception as exc:  # pragma: no cover - exposes a broken fence
            errors.append(exc)

    threads = [threading.Thread(target=resolve_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(result["resolved"] for result in results) == [False, True]
    refusal = next(result for result in results if not result["resolved"])
    assert refusal["reason"] == "escalation_already_resolved"

    rows = human_in_the_loop._load(ledger)
    assert sum(row["status"] == "resolved" for row in rows) == 1
    assert human_in_the_loop._latest_escalations(
        ledger, receipt_root=tmp_path
    )[opened["escalation_id"]]["status"] == "resolved"


def test_nonzero_command_hook_is_recorded_as_a_durable_failure(tmp_path):
    queue = tmp_path / "approval_queue.jsonl"
    human_in_the_loop._save(
        queue,
        [{"decision_id": "one", "status": "pending_approval"}],
    )

    result = human_in_the_loop.apply(
        queue,
        "one",
        "APPROVE",
        on_approved=human_in_the_loop._run_cmd_hook("sh -c 'exit 7'"),
    )

    assert result == {"applied": True, "status": "approved_hook_failed"}
    row = human_in_the_loop._load(queue)[0]
    assert row["status"] == "approved_hook_failed"
    assert row["hook_exit_code"] == 7
