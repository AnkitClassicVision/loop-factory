"""Local-first records + pluggable memory backends.

Local records always work; STATE writes are atomic with a monotonic epoch;
remote backends are seams that BLOCK until deliberately wired (never a silent
no-op that pretends to have archived)."""
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


MEM = _load("memory", "factory/memory.py")


def test_append_run_and_read_back(tmp_path):
    store = MEM.LocalStore(tmp_path)
    store.append_run({"node": "draft", "status": "ok"})
    store.append_run({"node": "qa", "status": "ok"})
    runs = store.runs()
    assert [r["node"] for r in runs] == ["draft", "qa"]
    assert all("timestamp" in r for r in runs)


def test_state_epoch_is_monotonic(tmp_path):
    store = MEM.LocalStore(tmp_path)
    s0 = store.write_state({"autonomy_state": "shadow"})
    s1 = store.write_state({"autonomy_state": "shadow"})
    assert s0["epoch"] == 0 and s1["epoch"] == 1
    assert store.read_state()["epoch"] == 1


def test_heartbeat_appends(tmp_path):
    store = MEM.LocalStore(tmp_path)
    store.heartbeat(epoch=0)
    store.heartbeat(epoch=1)
    assert len(store.heartbeats()) == 2


def test_local_backend_ships_as_noop(tmp_path):
    (tmp_path / "runs.jsonl").write_text("{}\n", encoding="utf-8")
    out = MEM.LocalBackend().ship("t", [tmp_path / "runs.jsonl", tmp_path / "missing.jsonl"])
    assert out["backend"] == "local"
    assert str(tmp_path / "runs.jsonl") in out["shipped"]


def test_remote_backends_block_until_wired(tmp_path):
    with pytest.raises(MEM.BackendNotWired):
        MEM.S3Backend().ship("t", [])
    with pytest.raises(MEM.BackendNotWired):
        MEM.OpenBrainBackend().ship("t", [])


def test_backends_from_charter(tmp_path):
    charter = {"memory": {"backends": ["s3"]}}
    backends = MEM.backends_from_charter(charter)
    assert [b.name for b in backends] == ["local", "s3"]


def test_unknown_backend_fails_closed():
    with pytest.raises(MEM.BackendNotWired):
        MEM.backends_from_charter({"memory": {"backends": ["carrier_pigeon"]}})
