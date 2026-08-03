"""Fixture-only regression tests for social CTA drafting failures."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CTA_URL = "https://example.test/exact-offer"


def _load_draft_post():
    path = ROOT / "departments" / "social" / "runtime" / "draft_post.py"
    spec = importlib.util.spec_from_file_location("test_social_cta_draft_post", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle() -> dict:
    return {
        "sanitized": True,
        "complete": True,
        "missing": [],
        "item": {
            "item_id": "fixture-item",
            "url": "https://example.test/source",
            "title": "Fixture title",
        },
        "body_text": "Fixture source material.",
        "brand": {},
        "offer": {"cta_url": CTA_URL},
    }


def _response(body: str, cta_url=CTA_URL) -> dict:
    return {
        "body": body,
        "cta_url": cta_url,
        "sources": [
            {"claim": "Fixture source material.", "source": "fixture-item"}
        ],
    }


def _normalize(module, response: dict) -> dict:
    return module._normalize_draft(
        response,
        surface="linkedin_mybcat",
        engine="fixture_engine",
        round_number=0,
        bundle=_bundle(),
    )


def _run_rejected_main(tmp_path: Path, monkeypatch, body: str, cta_url) -> Path:
    module = _load_draft_post()
    bundle_path = tmp_path / "bundle.json"
    engines_path = tmp_path / "engines.yaml"
    out_path = tmp_path / "receipt.json"
    state_dir = tmp_path / "state"
    bundle_path.write_text(json.dumps(_bundle()), encoding="utf-8")
    engines_path.write_text(
        "fixture_engine:\n  command: [fixture, '{prompt}']\n",
        encoding="utf-8",
    )
    response = _response(body, cta_url)
    monkeypatch.setattr(
        module,
        "_load_charter_policy",
        lambda path: (frozenset({"fixture_engine"}), 2),
    )
    monkeypatch.setattr(
        module, "_call_engine", lambda *args, **kwargs: json.dumps(response)
    )
    monkeypatch.setattr(module, "_record_run", lambda *args: None)
    rc = module.main(
        [
            "--state-dir",
            str(state_dir),
            "--out",
            str(out_path),
            "--bundle",
            str(bundle_path),
            "--surface",
            "linkedin_mybcat",
            "--engine",
            "fixture_engine",
            "--engines-file",
            str(engines_path),
            "--no-kernel",
        ]
    )
    assert rc == 2
    return state_dir / "rejected_attempts.jsonl"


def test_prompt_contains_literal_exact_cta_contract():
    module = _load_draft_post()
    prompt = module._prompt(
        _bundle(),
        "linkedin_mybcat",
        round_number=0,
        prior_draft=None,
        defects=None,
    )
    assert "Set output cta_url to the exact offer.cta_url value" in prompt
    assert f"SANITIZED BUNDLE: {CTA_URL}" in prompt
    assert "Include that same URL verbatim in body." in prompt
    assert "Emit NO other HTTP(S) URL anywhere." in prompt


def test_compliant_fixture_response_passes_gate_unchanged():
    module = _load_draft_post()
    response = _response(f"Fixture source material. Learn more: {CTA_URL}")
    draft = _normalize(module, response)
    assert draft["body"] == response["body"]
    assert draft["cta_url"] == response["cta_url"]
    assert draft["sources"] == response["sources"]


def test_zero_url_fixture_still_fails_gate():
    module = _load_draft_post()
    with pytest.raises(module.GateBlocked, match="exactly one distinct"):
        _normalize(module, _response("Fixture source material.", ""))


def test_multi_url_fixture_still_fails_gate():
    module = _load_draft_post()
    body = f"Fixture source material. {CTA_URL} https://other.test/path"
    with pytest.raises(module.GateBlocked, match="exactly one distinct"):
        _normalize(module, _response(body))


def test_rejected_attempt_writes_safe_diagnostics_without_body(tmp_path, monkeypatch):
    rejected_body = "PRIVATE FIXTURE BODY https://other.test/path"
    path = _run_rejected_main(tmp_path, monkeypatch, rejected_body, CTA_URL)
    text = path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines()]
    assert len(rows) == 1
    assert rows[0]["url_count"] == 2
    assert rows[0]["equals_offer"] is False
    assert rejected_body not in text
    assert '"body":' not in text


def test_rejected_attempt_diagnostics_are_append_only(tmp_path, monkeypatch):
    path = _run_rejected_main(tmp_path, monkeypatch, "First rejection", "")
    first = path.read_text(encoding="utf-8")
    path = _run_rejected_main(
        tmp_path,
        monkeypatch,
        "Second rejection https://other.test/path",
        CTA_URL,
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith(first)
    assert len(text.splitlines()) == 2


def test_daily_script_wraps_each_engine_attempt_and_keeps_terminal_receipt():
    path = ROOT / "departments" / "social" / "runtime" / "social_daily.sh"
    script = path.read_text(encoding="utf-8")
    function = script.split("run_draft_with_fallback() {", 1)[1].split("\n}\n", 1)[0]
    assert function.count('timeout 150s "$@" --engine') == 2
    assert function.count('if "$@" --engine') == 0
    assert (
        'incident_receipt_failure "${node}" "${receipt}" '
        '"all_draft_engines_failed"'
    ) in function
