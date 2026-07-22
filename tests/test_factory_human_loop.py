import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "factory" / "human_in_the_loop.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("factory_human_in_the_loop", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_push_creates_packets_for_department(tmp_path):
    human_loop = _load_module()
    queue = tmp_path / "queue.jsonl"
    outbox = tmp_path / "outbox.jsonl"
    _write_jsonl(
        queue,
        [
            {"contact_id": "c-1", "draft": "First", "status": "pending_approval", "queued_at": "2026-07-21T09:00:00Z"},
            {"contact_id": "c-2", "draft": "Second", "status": "pending_approval", "queued_at": "2026-07-21T09:01:00Z"},
            {"contact_id": "c-3", "draft": "Already", "status": "pending_approval", "queued_at": "2026-07-21T09:02:00Z", "decision_id": "sales-existing"},
            {"contact_id": "c-4", "draft": "Not pending", "status": "approved", "queued_at": "2026-07-21T09:03:00Z"},
        ],
    )

    result = human_loop.push(queue, department="sales", outbox_path=outbox)

    packets = _read_jsonl(outbox)
    assert result["pushed"] == 2
    assert len(packets) == 2
    for packet in packets:
        assert "sales" in packet["decision_id"]
        assert packet["department"] == "sales"
        assert packet["packet"]["eli5"].startswith("Approve sales message to ")
        assert packet["packet"]["draft"] in {"First", "Second"}
        assert packet["packet"]["options"] == ["APPROVE", "REJECT"]

    rows = _read_jsonl(queue)
    assert rows[0]["decision_id"] == packets[0]["decision_id"]
    assert rows[1]["decision_id"] == packets[1]["decision_id"]
    assert rows[2]["decision_id"] == "sales-existing"
    assert "decision_id" not in rows[3]


def test_apply_maps_verdict_to_queue(tmp_path):
    human_loop = _load_module()
    queue = tmp_path / "queue.jsonl"
    outbox = tmp_path / "outbox.jsonl"
    _write_jsonl(
        queue,
        [
            {"contact_id": "c-1", "draft": "First", "status": "pending_approval", "queued_at": "2026-07-21T09:00:00Z"},
            {"contact_id": "c-2", "draft": "Second", "status": "pending_approval", "queued_at": "2026-07-21T09:01:00Z"},
        ],
    )
    human_loop.push(queue, department="sales", outbox_path=outbox)
    decision_ids = [row["decision_id"] for row in _read_jsonl(queue)]

    assert human_loop.apply(queue, decision_ids[0], verdict="APPROVE") == {"applied": True}
    assert human_loop.apply(queue, decision_ids[1], verdict="REJECT") == {"applied": True}
    before_unknown = _read_jsonl(queue)
    assert human_loop.apply(queue, "unknown-id", verdict="APPROVE") == {"applied": False}
    assert _read_jsonl(queue) == before_unknown
    assert [row["status"] for row in _read_jsonl(queue)] == ["approved", "rejected"]


def test_apply_fires_on_approved_hook_only_on_approve(tmp_path):
    human_loop = _load_module()
    queue = tmp_path / "queue.jsonl"
    outbox = tmp_path / "outbox.jsonl"
    _write_jsonl(
        queue,
        [
            {"contact_id": "c-1", "draft": "First", "status": "pending_approval", "queued_at": "2026-07-21T09:00:00Z"},
            {"contact_id": "c-2", "draft": "Second", "status": "pending_approval", "queued_at": "2026-07-21T09:01:00Z"},
        ],
    )
    human_loop.push(queue, department="sales", outbox_path=outbox)
    decision_ids = [row["decision_id"] for row in _read_jsonl(queue)]

    calls = []
    human_loop.apply(queue, decision_ids[0], verdict="APPROVE", on_approved=lambda q: calls.append(q))
    human_loop.apply(queue, decision_ids[1], verdict="REJECT", on_approved=lambda q: calls.append(q))
    human_loop.apply(queue, "unknown-id", verdict="APPROVE", on_approved=lambda q: calls.append(q))

    # Hook fires exactly once: on the APPROVE, never on REJECT or no-match.
    assert calls == [str(queue)]


def test_department_isolation(tmp_path):
    human_loop = _load_module()
    outbox = tmp_path / "outbox.jsonl"
    sales_queue = tmp_path / "sales.jsonl"
    support_queue = tmp_path / "support.jsonl"
    row = {"contact_id": "same", "draft": "Draft", "status": "pending_approval", "queued_at": "2026-07-21T09:00:00Z"}
    _write_jsonl(sales_queue, [row])
    _write_jsonl(support_queue, [row])

    human_loop.push(sales_queue, department="sales", outbox_path=outbox)
    human_loop.push(support_queue, department="support", outbox_path=outbox)

    ids = [packet["decision_id"] for packet in _read_jsonl(outbox)]
    assert len(ids) == len(set(ids)) == 2
    assert "sales" in ids[0]
    assert "support" in ids[1]
