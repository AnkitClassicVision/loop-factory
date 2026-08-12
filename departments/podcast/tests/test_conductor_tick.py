from __future__ import annotations

from departments.podcast.runtime import conductor_tick


def test_node_emits_ok_record_with_date_dedupe(tmp_path, monkeypatch):
    records = []
    monkeypatch.setattr(conductor_tick.conductor, "tick", lambda *a, **k: {"held_lease": True, "refused_by": None, "run_id": None, "decisions": []})
    monkeypatch.setattr(conductor_tick.runrecord, "emit_record", lambda *a, **k: records.append(k) or tmp_path / "runs.jsonl")
    assert conductor_tick.main(["--state-dir", str(tmp_path), "--dept-dir", str(tmp_path), "--shadow"]) == 0
    assert records[0]["node"] == "conductor_tick" and records[0]["status"] == "ok"
    assert records[0]["trigger"]["dedupe_key"].endswith("-conductor_tick")


def test_refused_tick_still_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(conductor_tick.conductor, "tick", lambda *a, **k: {"held_lease": False, "refused_by": "other", "run_id": None, "decisions": []})
    monkeypatch.setattr(conductor_tick.runrecord, "emit_record", lambda *a, **k: tmp_path / "runs.jsonl")
    assert conductor_tick.main(["--state-dir", str(tmp_path), "--dept-dir", str(tmp_path)]) == 0


def test_crash_emits_error_and_exits_one(tmp_path, monkeypatch):
    records = []
    def boom(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(conductor_tick.conductor, "tick", boom)
    monkeypatch.setattr(conductor_tick.runrecord, "emit_record", lambda *a, **k: records.append(k) or tmp_path / "runs.jsonl")
    assert conductor_tick.main(["--state-dir", str(tmp_path), "--dept-dir", str(tmp_path)]) == 1
    assert records[0]["status"] == "error"


def test_emission_failure_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(conductor_tick.conductor, "tick", lambda *a, **k: {"held_lease": True, "refused_by": None, "run_id": None, "decisions": []})
    def boom(*args, **kwargs):
        raise OSError("disk")
    monkeypatch.setattr(conductor_tick.runrecord, "emit_record", boom)
    assert conductor_tick.main(["--state-dir", str(tmp_path), "--dept-dir", str(tmp_path)]) == 1
