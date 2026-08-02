"""Ticket 009 hook: the release manifest records the factory's own version —
graph schema, runner, telemetry schema, and a hash over the template set —
so later tooling can compare what factory built each release. Record now,
compare later."""
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


REL = _load("fv_release", "factory/release.py")
RG = _load("fv_rungraph", "factory/rungraph.py")
RUN = _load("fv_runner", "factory/runner.py")
PJ = _load("fv_projection", "factory/projection.py")


def _dept(tmp_path):
    d = tmp_path / "demo"
    (d / "runtime").mkdir(parents=True)
    (d / "charter.yaml").write_text("department: demo\n", encoding="utf-8")
    return d


def test_manifest_records_factory_version(tmp_path):
    dept = _dept(tmp_path)
    h = REL.pin_release(dept, tmp_path / "releases", source_ref="sha1")
    manifest = REL.load_manifest(tmp_path / "releases" / h)
    fv = manifest["factory_version"]
    assert fv["graph_schema_version"] == RG.GRAPH_SCHEMA_VERSION
    assert fv["runner_version"] == RUN.RUNNER_VERSION
    assert fv["telemetry_schema_version"] == PJ.TELEMETRY_SCHEMA_VERSION
    assert len(fv["template_set_hash"]) == 16


def test_template_set_hash_tracks_template_bytes(tmp_path):
    a = tmp_path / "templates-a"
    b = tmp_path / "templates-b"
    a.mkdir()
    b.mkdir()
    (a / "x.tmpl").write_text("one\n", encoding="utf-8")
    (b / "x.tmpl").write_text("two\n", encoding="utf-8")
    ha = REL.template_set_hash(a)
    hb = REL.template_set_hash(b)
    assert ha != hb
    assert REL.template_set_hash(a) == ha  # deterministic
