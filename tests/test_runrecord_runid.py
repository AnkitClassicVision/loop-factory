import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from factory import runrecord


def _emit(tmp_path, **kw):
    runrecord.emit_record(tmp_path, department="d", node="n", status="ok", **kw)
    rows = [json.loads(l) for l in (tmp_path / "runs-v2.jsonl").read_text().splitlines()]
    return rows[-1]


def test_explicit_run_id_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_FACTORY_RUN_ID", "env-id-123")
    assert _emit(tmp_path, run_id="explicit-1")["run_id"] == "explicit-1"


def test_env_run_id_used_when_not_passed(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOP_FACTORY_RUN_ID", "env-id-123")
    assert _emit(tmp_path)["run_id"] == "env-id-123"


def test_fresh_id_when_neither(tmp_path, monkeypatch):
    monkeypatch.delenv("LOOP_FACTORY_RUN_ID", raising=False)
    assert _emit(tmp_path)["run_id"]


def test_capabilities_allowlist_passes_run_id(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "caps", Path(__file__).parents[1] / "kernel" / "capabilities.py")
    caps = importlib.util.module_from_spec(spec); spec.loader.exec_module(caps)
    env = caps.department_env({"LOOP_FACTORY_RUN_ID": "r1", "AWS_SECRET_ACCESS_KEY": "nope"})
    assert env.get("LOOP_FACTORY_RUN_ID") == "r1"
    assert "AWS_SECRET_ACCESS_KEY" not in env
