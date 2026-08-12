from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from departments.podcast.runtime import floor_compiler_run


NOW = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)


def test_not_due_skips_without_observation_and_records(tmp_path, monkeypatch):
    state, dept = tmp_path / "state", tmp_path / "podcast"
    state.mkdir()
    (state / "floors-history.jsonl").write_text(json.dumps({"computed_at": NOW.isoformat()}) + "\n")
    records = []
    monkeypatch.setattr(floor_compiler_run.runrecord, "emit_record", lambda *a, **kw: records.append(kw))
    result = floor_compiler_run.run(state, dept, now=NOW + timedelta(days=1))
    assert result is None
    assert not (state / "observations.jsonl").exists()
    assert records[0]["status"] == "ok"
    assert any("skipped_not_due" in artifact for artifact in records[0]["artifacts"])


def test_due_unconfigured_emits_floors_unknown(tmp_path, monkeypatch):
    state, dept = tmp_path / "state", tmp_path / "podcast"
    state.mkdir(); dept.mkdir()
    (dept / "charter.yaml").write_text("department: podcast\nowner: owner\nautonomy_state: shadow\nimmutable_safety_invariants:\n  heal_may_not_modify: [charter]\n")
    monkeypatch.setattr(floor_compiler_run.runrecord, "emit_record", lambda *a, **kw: tmp_path / "record")
    observation = floor_compiler_run.run(state, dept, now=NOW)
    assert observation["sensor"] == "floors"
    assert observation["status"] == "unknown"


def test_due_run_always_emits_record(tmp_path, monkeypatch):
    state, dept = tmp_path / "state", tmp_path / "podcast"
    state.mkdir(); dept.mkdir()
    (dept / "charter.yaml").write_text("department: podcast\nowner: owner\nautonomy_state: shadow\nimmutable_safety_invariants:\n  heal_may_not_modify: [charter]\n")
    calls = []
    monkeypatch.setattr(floor_compiler_run.runrecord, "emit_record", lambda *a, **kw: calls.append(kw))
    floor_compiler_run.run(state, dept, now=NOW)
    assert len(calls) == 1
    assert calls[0]["node"] == "floor_compiler_run"


def test_crash_path_exits_one(tmp_path, monkeypatch):
    monkeypatch.setattr(floor_compiler_run, "run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert floor_compiler_run.main(["--state-dir", str(tmp_path / "state"), "--dept-dir", str(tmp_path / "dept"), "--shadow"]) == 1
