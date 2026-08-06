from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from factory.conductor import tick
from kernel.lease import acquire


NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _dept(tmp_path, ceiling=900):
    dept = tmp_path / "podcast"
    dept.mkdir()
    (dept / "charter.yaml").write_text(
        "department: podcast\nowner: owner\nautonomy_state: shadow\n"
        "immutable_safety_invariants:\n  heal_may_not_modify: [charter]\n"
        f"budget:\n  weekly_ceilings:\n    model_calls: {ceiling}\n",
        encoding="utf-8",
    )
    return dept


def _manifest(state, *, status="red", run_id="run-9", missing=(), unexpected=(), duplicates=()):
    directory = state / "run-manifests"
    _json(directory / f"{run_id}.json", {"run_id": run_id})
    _json(directory / f"{run_id}.verdict.json", {"status": status, "missing": list(missing), "unexpected": list(unexpected), "duplicates": list(duplicates)})


def test_refused_tick_returns_holder_and_writes_no_heartbeat(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path)
    acquire(state, holder="other", ttl_s=60, now=NOW)
    result = tick(dept, state, now=NOW)
    assert result["held_lease"] is False and result["refused_by"] == "other"
    assert not (state / "conductor-heartbeat.json").exists()
    assert len((state / "lease-refusals.jsonl").read_text().splitlines()) == 1


def test_red_verdict_creates_rerun_decisions(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path)
    _manifest(state, missing=["alpha"], unexpected=["beta"], duplicates=["gamma"])
    result = tick(dept, state, now=NOW)
    assert [d["node"] for d in result["decisions"]] == ["alpha", "beta", "gamma"]
    assert all(d["action"] == "rerun_node" and d["run_id"] == "run-9" for d in result["decisions"])


def test_stale_incident_creates_re_escalate(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path)
    _json(state / "incidents.json", {"fp-1": {"state": "open", "last_escalated_at": (NOW - timedelta(hours=49)).isoformat()}})
    result = tick(dept, state, now=NOW)
    assert result["decisions"] == [{"kind": "unblock", "action": "re_escalate", "fingerprint": "fp-1"}]


def test_old_pending_approval_creates_deduped_reminder(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path)
    old = (NOW - timedelta(hours=25)).isoformat()
    _jsonl(state / "approval_queue.jsonl", [{"status": "pending_approval", "queued_at": old, "decision_id": "card-1"}, {"status": "pending_approval", "queued_at": old, "decision_id": "card-1"}])
    result = tick(dept, state, now=NOW)
    assert result["decisions"] == [{"kind": "unblock", "action": "remind_owner", "card": "card-1"}]


def test_proposed_without_later_applied_heal_is_routine(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path)
    _jsonl(state / "heals.jsonl", [{"ts": "2026-08-01T00:00:00Z", "fingerprint": "fp", "playbook": "pb", "result": "proposed"}, {"ts": "2026-08-02T00:00:00Z", "fingerprint": "done", "playbook": "pb", "result": "proposed"}, {"ts": "2026-08-03T00:00:00Z", "fingerprint": "done", "playbook": "pb", "result": "verified"}])
    result = tick(dept, state, now=NOW)
    assert result["decisions"] == [{"kind": "routine", "action": "apply_heal_shadow", "fingerprint": "fp", "playbook": "pb"}]


def test_frozen_floors_create_floor_gap(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path)
    _jsonl(state / "floors-history.jsonl", [{"computed_at": NOW.isoformat(), "status": "frozen", "reason": "red manifest", "changes": []}])
    assert tick(dept, state, now=NOW)["decisions"] == [{"kind": "floor_gap", "action": "repair_floor_inputs", "reason": "red manifest"}]


def test_mixed_fixture_priority_and_oldest_first(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path)
    _json(state / "incidents.json", {
        "new": {"state": "open", "last_escalated_at": "2026-08-01T00:00:00Z"},
        "old": {"state": "open", "last_escalated_at": "2026-07-01T00:00:00Z"},
    })
    _jsonl(state / "floors-history.jsonl", [{"computed_at": "2026-07-02T00:00:00Z", "status": "frozen", "reason": "gap", "changes": []}])
    _jsonl(state / "heals.jsonl", [{"ts": "2026-06-01T00:00:00Z", "fingerprint": "heal", "playbook": "pb", "result": "proposed"}])
    decisions = tick(dept, state, now=NOW)["decisions"]
    assert [(d["kind"], d["action"], d.get("fingerprint")) for d in decisions] == [
        ("unblock", "re_escalate", "old"), ("unblock", "re_escalate", "new"),
        ("floor_gap", "repair_floor_inputs", None), ("routine", "apply_heal_shadow", "heal")]


def test_aging_promotes_three_tick_repeated_routine(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path)
    decision = {"kind": "routine", "action": "apply_heal_shadow", "fingerprint": "fp", "playbook": "pb"}
    _jsonl(state / "heals.jsonl", [{"ts": "2026-08-01T00:00:00Z", "fingerprint": "fp", "playbook": "pb", "result": "proposed"}])
    _jsonl(state / "conductor-shadow.jsonl", [{"ts": f"2026-08-0{i}T00:00:00Z", "holder": "c", "run_id": None, "decisions": [decision], "refused_by": None} for i in range(1, 4)])
    assert tick(dept, state, now=NOW)["decisions"][0]["kind"] == "floor_gap"


def test_unreadable_incidents_is_unknown_not_crash(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path)
    state.mkdir()
    (state / "incidents.json").write_text("not json", encoding="utf-8")
    assert {"kind": "unknown_source", "source": "incidents.json"} in tick(dept, state, now=NOW)["decisions"]


def test_heartbeat_epoch_and_ledger_shape(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path)
    first = tick(dept, state, holder="one", now=NOW)
    from kernel.lease import release
    from kernel.lease import Lease
    lease_row = json.loads((state / "driver.lease").read_text())
    release(Lease(state / "driver.lease", "one", lease_row["nonce"], lease_row["expires_at"]))
    tick(dept, state, holder="two", now=NOW + timedelta(minutes=1))
    assert json.loads((state / "conductor-heartbeat.json").read_text())["epoch"] == 2
    rows = [json.loads(line) for line in (state / "conductor-shadow.jsonl").read_text().splitlines()]
    assert set(rows[-1]) == {"ts", "holder", "run_id", "decisions", "refused_by"}
    assert first["held_lease"] is True


def test_budget_warning_is_first(tmp_path):
    state, dept = tmp_path / "state", _dept(tmp_path, ceiling=4)
    _manifest(state, missing=["a"])
    assert tick(dept, state, now=NOW)["decisions"][0] == {"kind": "unblock", "action": "halt_and_review_budget"}

