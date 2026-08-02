"""Fixture-only tests for model envelope telemetry and auth blocking."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    path = ROOT / "departments" / "social" / "runtime" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _usage_fixture(**updates):
    value = {
        "model": "claude-fixture-1",
        "usage": {
            "input_tokens": 12,
            "output_tokens": 7,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 2,
        },
        "duration_ms": 19,
        "result": "{}",
    }
    value.update(updates)
    return json.dumps(value)


def test_clean_json_envelope_parses_model_and_usage():
    parsed = _load("draft_post").parse_engine_envelope(_usage_fixture(), {})
    assert parsed["model"] == "claude-fixture-1"
    assert parsed["usage"] == {
        "input_tokens": 12,
        "output_tokens": 7,
        "cache_read": 3,
        "cache_creation": 2,
    }
    assert parsed["raw_ok"] is True


def test_envelope_after_noise_lines_uses_final_complete_object():
    stdout = 'launcher noise\n{"model":"old"}\n' + _usage_fixture()
    parsed = _load("draft_post").parse_engine_envelope(stdout, {})
    assert parsed["model"] == "claude-fixture-1"
    assert parsed["usage"]["output_tokens"] == 7


def test_text_only_stdout_is_not_raw_ok_and_has_reason():
    parsed = _load("draft_post").parse_engine_envelope("fixture plain text", {})
    assert parsed["raw_ok"] is False
    assert "JSON object" in parsed["reason"]


def test_missing_usage_fields_are_none_not_zero():
    parsed = _load("draft_post").parse_engine_envelope('{"model":"fixture"}', {})
    assert parsed["usage"] == {
        "input_tokens": None,
        "output_tokens": None,
        "cache_read": None,
        "cache_creation": None,
    }


def test_duration_ms_is_non_negative_int_when_derivable():
    parsed = _load("draft_post").parse_engine_envelope(
        _usage_fixture(duration_ms=14.9), {}
    )
    assert isinstance(parsed["duration_ms"], int)
    assert parsed["duration_ms"] >= 0


def test_qa_parser_uses_same_envelope_contract():
    parsed = _load("qa_post").parse_engine_envelope(_usage_fixture(), {})
    assert parsed["model"] == "claude-fixture-1"
    assert parsed["usage"]["cache_creation"] == 2


def test_auth_probe_failure_blocks_receipt_without_model_invocation(tmp_path, monkeypatch):
    module = _load("draft_post")
    bundle = {
        "sanitized": True,
        "complete": True,
        "missing": [],
        "item": {"item_id": "fixture", "url": "https://example.test/item"},
        "body_text": "fixture",
        "brand": {},
        "offer": {"cta_url": "https://example.test/cta"},
    }
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    engines_path = tmp_path / "engines.yaml"
    engines_path.write_text(
        "fixture_engine:\n"
        "  command: [fixture, '{prompt}']\n"
        "  auth_class: oauth_cli\n"
        "  auth_probe: [fixture-auth]\n",
        encoding="utf-8",
    )
    out = tmp_path / "receipt.json"
    called = False

    def forbidden_call(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("model runner must not be called")

    monkeypatch.setattr(module, "_load_charter_policy", lambda path: (frozenset({"fixture_engine"}), 2))
    monkeypatch.setattr(module, "_auth_probe", lambda cfg, timeout: False)
    monkeypatch.setattr(module, "_call_engine", forbidden_call)
    monkeypatch.setattr(module, "_record_run", lambda *args: None)
    rc = module.main([
        "--state-dir", str(tmp_path / "state"),
        "--out", str(out),
        "--bundle", str(bundle_path),
        "--surface", "linkedin_mybcat",
        "--engine", "fixture_engine",
        "--engines-file", str(engines_path),
        "--no-kernel",
    ])
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 2
    assert receipt["status"] == "blocked"
    assert receipt["error"]["code"] == "AUTH_EXPIRED"
    assert receipt["auth_class"] == "blocked"
    assert called is False


def test_draft_receipt_gains_telemetry_and_keeps_existing_fields(tmp_path, monkeypatch):
    module = _load("draft_post")
    bundle = {
        "sanitized": True, "complete": True, "missing": [],
        "item": {"item_id": "fixture", "url": "https://example.test/item"},
        "body_text": "fixture", "brand": {},
        "offer": {"cta_url": "https://example.test/cta"},
    }
    (tmp_path / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    (tmp_path / "engines.yaml").write_text(
        "fixture_engine:\n  command: [fixture, '{prompt}']\n  auth_class: oauth_cli\n",
        encoding="utf-8",
    )
    response = {
        "body": "Fixture body. https://example.test/cta",
        "cta_url": "https://example.test/cta",
        "sources": [{"claim": "Fixture body.", "source": "https://example.test/item"}],
    }
    envelope = json.loads(_usage_fixture(result=json.dumps(response)))
    monkeypatch.setattr(module, "_load_charter_policy", lambda path: (frozenset({"fixture_engine"}), 2))
    monkeypatch.setattr(module, "_call_engine", lambda *args, **kwargs: json.dumps(envelope))
    monkeypatch.setattr(module, "_record_run", lambda *args: None)
    out = tmp_path / "receipt.json"
    assert module.main([
        "--state-dir", str(tmp_path / "state"), "--out", str(out),
        "--bundle", str(tmp_path / "bundle.json"), "--surface", "linkedin_mybcat",
        "--engine", "fixture_engine", "--engines-file", str(tmp_path / "engines.yaml"),
        "--no-kernel",
    ]) == 0
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["engine"] == "fixture_engine"
    assert receipt["body"] == response["body"]
    assert receipt["model"] == "claude-fixture-1"
    assert receipt["usage"]["input_tokens"] == 12
    assert receipt["duration_ms"] == 19
    assert receipt["auth_class"] == "oauth_cli"
