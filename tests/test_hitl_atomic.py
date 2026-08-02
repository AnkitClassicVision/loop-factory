import json
import threading

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

    assert list(tmp_path.iterdir()) == [queue]


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


def test_empty_queue_save_works(tmp_path):
    queue = tmp_path / "approval_queue.jsonl"

    human_in_the_loop._save(queue, [])

    assert queue.read_text(encoding="utf-8") == ""
    assert human_in_the_loop._load(queue) == []
