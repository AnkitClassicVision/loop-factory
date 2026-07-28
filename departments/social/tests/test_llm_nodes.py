"""Contract tests for social N4 draft_post and N5 qa_post."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
DRAFT_NODE = ROOT / "departments" / "social" / "runtime" / "draft_post.py"
QA_NODE = ROOT / "departments" / "social" / "runtime" / "qa_post.py"
CHARTER = ROOT / "departments" / "social" / "charter.yaml"


def _write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _bundle(*, sanitized: bool = True) -> dict:
    return {
        "version": "fake-v1",
        "assembled_at": "2026-01-01T00:00:00+00:00",
        "item": {
            "item_id": "fake-item-1",
            "source_type": "podcast",
            "title": "Obviously Fake Archive Item",
            "url": "https://example.test/archive",
            "published_at": "2020-01-01T00:00:00+00:00",
            "body_path": "/fixtures/fake-item.txt",
            "last_resurfaced_at": None,
            "prior_engagement": {"score": 1.0},
        },
        "body_text": "An invented fixture about careful business operations.",
        "brand": {"name": "Example Test Brand"},
        "offer": {"cta_url": "https://example.test/book"},
        "complete": True,
        "missing": [],
        "sanitized": sanitized,
        "redactions": 0,
    }


def _draft(body: str, **updates) -> dict:
    value = {
        "surface": "linkedin_mybcat",
        "body": body,
        "cta_url": "https://example.test/book",
        "sources": [
            {
                "claim": "An archive item supports this draft.",
                "source": "https://example.test/archive",
            }
        ],
        "engine": "codex_oauth",
        "round": 0,
    }
    value.update(updates)
    return value


def _fake_engine(
    tmp_path: Path,
    *,
    crash: bool = False,
    source_ref: str = "https://example.test/archive",
    prompt_capture: Path | None = None,
    placeholder: str = "{prompt}",
) -> tuple[Path, Path]:
    script = tmp_path / ("crash_engine.py" if crash else "fake_engine.py")
    if crash:
        script.write_text(
            "import sys\n"
            "sys.stderr.write('synthetic engine outage\\n')\n"
            "raise SystemExit(7)\n",
            encoding="utf-8",
        )
    else:
        capture_line = (
            f"pathlib.Path({str(prompt_capture)!r}).write_text(prompt, encoding='utf-8')\n"
            if prompt_capture is not None
            else ""
        )
        script.write_text(
            "import json, pathlib, sys\n"
            + (
                "if len(sys.argv) != 2 or "
                "'Obviously Fake Archive Item' not in sys.argv[1]:\n"
                "    raise SystemExit('prompt content was not one argv element')\n"
                "prompt = sys.argv[1]\n"
                if placeholder == "{prompt}"
                else
                "if len(sys.argv) != 2:\n"
                "    raise SystemExit('prompt file was not one argv element')\n"
                "prompt = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')\n"
            )
            + capture_line
            + "if 'cross-model voice' in prompt:\n"
            "    result = {'defects': []}\n"
            "else:\n"
            "    result = {\n"
            "        'body': 'A useful idea from the archive. https://example.test/book',\n"
            "        'cta_url': 'https://example.test/book',\n"
            "        'sources': [\n"
            "            {'claim': 'A useful idea from the archive.',\n"
            f"             'source': {source_ref!r}}}\n"
            "        ],\n"
            "    }\n"
            "sys.stdout.write(json.dumps(result))\n",
            encoding="utf-8",
        )
    engines = tmp_path / ("crash_engines.yaml" if crash else "engines.yaml")
    engines.write_text(
        "codex_oauth:\n"
        f"  - {json.dumps(sys.executable)}\n"
        f"  - {json.dumps(str(script))}\n"
        f"  - {json.dumps(placeholder)}\n"
        "claude_subscription:\n"
        f"  - {json.dumps(sys.executable)}\n"
        f"  - {json.dumps(str(script))}\n"
        f"  - {json.dumps(placeholder)}\n"
        "fixture_engine:\n"
        f"  - {json.dumps(sys.executable)}\n"
        f"  - {json.dumps(str(script))}\n"
        f"  - {json.dumps(placeholder)}\n",
        encoding="utf-8",
    )
    return script, engines


def _mutated_charter(tmp_path: Path, mutate) -> Path:
    charter = yaml.safe_load(CHARTER.read_text(encoding="utf-8"))
    mutate(charter)
    path = tmp_path / "charter.yaml"
    path.write_text(yaml.safe_dump(charter, sort_keys=False), encoding="utf-8")
    return path


def _run_draft(
    tmp_path: Path,
    bundle: dict,
    *,
    engine: str = "codex_oauth",
    engines_file: Path | None = None,
    charter: Path | None = None,
    extra: list[str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
    if engines_file is None:
        _, engines_file = _fake_engine(tmp_path)
    bundle_path = _write_json(tmp_path / "bundle.json", bundle)
    out = tmp_path / "draft-out.json"
    state = tmp_path / "state"
    command = [
        sys.executable,
        str(DRAFT_NODE),
        "--state-dir",
        str(state),
        "--out",
        str(out),
        "--bundle",
        str(bundle_path),
        "--surface",
        "linkedin_mybcat",
        "--engine",
        engine,
        "--engines-file",
        str(engines_file),
        "--no-kernel",
    ]
    if charter is not None:
        command.extend(["--charter", str(charter)])
    command.extend(extra or [])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return completed, json.loads(out.read_text(encoding="utf-8")), state


def _run_qa(
    tmp_path: Path,
    draft: dict,
    *,
    bundle: dict | None = None,
    engine: str = "claude_subscription",
    engines_file: Path | None = None,
    charter: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
    if engines_file is None:
        _, engines_file = _fake_engine(tmp_path)
    draft_path = _write_json(tmp_path / "input-draft.json", draft)
    bundle_path = _write_json(tmp_path / "bundle.json", bundle or _bundle())
    out = tmp_path / "qa-out.json"
    state = tmp_path / "state"
    command = [
        sys.executable,
        str(QA_NODE),
        "--state-dir",
        str(state),
        "--out",
        str(out),
        "--draft",
        str(draft_path),
        "--bundle",
        str(bundle_path),
        "--engine",
        engine,
        "--engines-file",
        str(engines_file),
        "--no-kernel",
    ]
    if charter is not None:
        command.extend(["--charter", str(charter)])
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, json.loads(out.read_text(encoding="utf-8")), state


def _codes(report: dict) -> list[str]:
    return [defect["code"] for defect in report["defects"]]


def test_draft_refuses_unsanitized_bundle_and_quarantines(tmp_path):
    completed, output, state = _run_draft(tmp_path, _bundle(sanitized=False))

    assert completed.returncode == 2
    assert output["status"] == "blocked"
    assert "sanitized flag must be true" in " ".join(output["reasons"])
    quarantine = json.loads(
        (state / "quarantine" / "fake-item-1.json").read_text(encoding="utf-8")
    )
    assert quarantine["status"] == "blocked"


def test_engine_allowlist_refuses_unknown_engine(tmp_path):
    completed, output, _ = _run_draft(
        tmp_path, _bundle(), engine="per_token_api"
    )

    assert completed.returncode == 2
    assert output["status"] == "blocked"
    assert "not allowlisted" in " ".join(output["reasons"])


def test_prompt_placeholder_passes_full_prompt_as_one_argv_element(tmp_path):
    prompt_capture = tmp_path / "prompt-content.txt"
    _, engines_file = _fake_engine(tmp_path, prompt_capture=prompt_capture)

    completed, _, _ = _run_draft(
        tmp_path,
        _bundle(),
        engines_file=engines_file,
    )

    prompt = prompt_capture.read_text(encoding="utf-8")
    assert completed.returncode == 0
    assert "Obviously Fake Archive Item" in prompt
    assert "\n" in prompt


def test_prompt_file_placeholder_remains_supported(tmp_path):
    _, engines_file = _fake_engine(tmp_path, placeholder="{prompt_file}")

    drafted, _, _ = _run_draft(
        tmp_path,
        _bundle(),
        engines_file=engines_file,
    )
    reviewed, report, _ = _run_qa(
        tmp_path,
        _draft("A clean archive idea. https://example.test/book"),
        engines_file=engines_file,
    )

    assert drafted.returncode == 0
    assert reviewed.returncode == 0
    assert report["pass"] is True


def test_engine_template_without_prompt_placeholder_is_rejected(tmp_path):
    script, _ = _fake_engine(tmp_path)
    engines_file = _write_json(
        tmp_path / "missing-placeholder-engines.json",
        {
            "codex_oauth": [sys.executable, str(script)],
            "claude_subscription": [sys.executable, str(script)],
        },
    )

    drafted, draft_output, _ = _run_draft(
        tmp_path,
        _bundle(),
        engines_file=engines_file,
    )
    reviewed, qa_output, _ = _run_qa(
        tmp_path,
        _draft("A clean archive idea. https://example.test/book"),
        engines_file=engines_file,
    )

    assert drafted.returncode == 2
    assert "must contain {prompt} or {prompt_file}" in " ".join(
        draft_output["reasons"]
    )
    assert reviewed.returncode == 0
    assert _codes(qa_output) == ["qa_engine_unavailable"]
    assert "must contain {prompt} or {prompt_file}" in qa_output["defects"][0]["detail"]


def test_qa_refuses_same_engine_as_draft(tmp_path):
    completed, output, state = _run_qa(
        tmp_path, _draft("A clean archive idea. https://example.test/book"),
        engine="codex_oauth",
    )

    assert completed.returncode == 2
    assert output["status"] == "blocked"
    assert "cross-model QA required" in " ".join(output["reasons"])
    assert (state / "quarantine" / "fake-item-1.json").is_file()


@pytest.mark.parametrize(
    ("draft", "expected_code"),
    [
        (
            _draft("A specific idea — without hype. https://example.test/book"),
            "em_dash",
        ),
        (
            _draft("Leverage this archive idea. https://example.test/book"),
            "banned_word",
        ),
        (
            _draft(
                "Read https://example.test/book and https://example.test/other."
            ),
            "cta_url_count",
        ),
        (
            _draft("A" * 281, surface="x_mybcat"),
            "surface_length_exceeded",
        ),
        (
            _draft("The fixture claims 42 wins. https://example.test/book"),
            "ungrounded_number",
        ),
        (
            _draft("This week, revisit the archive. https://example.test/book"),
            "time_anchor",
        ),
    ],
    ids=["em-dash", "banned-word", "two-ctas", "length", "number", "time-anchor"],
)
def test_each_deterministic_defect_is_enumerated(tmp_path, draft, expected_code):
    completed, report, _ = _run_qa(tmp_path, draft)

    assert completed.returncode == 0
    assert expected_code in _codes(report)
    assert report["pass"] is False


def test_deterministic_checks_stay_silent_on_clean_draft(tmp_path):
    completed, report, _ = _run_qa(
        tmp_path,
        _draft("A specific archive idea. https://example.test/book"),
    )

    assert completed.returncode == 0
    assert report == {
        "pass": True,
        "defects": [],
        "engine": "claude_subscription",
    }


def test_empty_sources_is_a_deterministic_defect(tmp_path):
    completed, report, _ = _run_qa(
        tmp_path,
        _draft(
            "A specific archive idea. https://example.test/book",
            sources=[],
        ),
    )

    assert completed.returncode == 0
    assert "missing_sources" in _codes(report)
    assert report["pass"] is False


def test_draft_rejects_empty_sources(tmp_path):
    script, engines_file = _fake_engine(tmp_path)
    script.write_text(
        "import json, sys\n"
        "sys.stdout.write(json.dumps({\n"
        "    'body': 'A source-free draft. https://example.test/book',\n"
        "    'cta_url': 'https://example.test/book',\n"
        "    'sources': [],\n"
        "}))\n",
        encoding="utf-8",
    )

    completed, output, _ = _run_draft(
        tmp_path,
        _bundle(),
        engines_file=engines_file,
    )

    assert completed.returncode == 2
    assert output["status"] == "blocked"
    assert "missing_sources" in " ".join(output["reasons"])


@pytest.mark.parametrize(
    "source_ref",
    [
        "https://example.test/archive",
        "fake-item-1",
        "Obviously Fake Archive Item",
    ],
    ids=["item-url", "item-id", "item-title"],
)
def test_draft_accepts_each_allowed_source_identifier(tmp_path, source_ref):
    _, engines_file = _fake_engine(tmp_path, source_ref=source_ref)

    completed, output, _ = _run_draft(
        tmp_path,
        _bundle(),
        engines_file=engines_file,
    )

    assert completed.returncode == 0
    assert output["sources"][0]["source"] == source_ref


def test_draft_rejects_free_text_source_and_names_invalid_ref(tmp_path):
    _, engines_file = _fake_engine(
        tmp_path,
        source_ref="episode description",
    )

    completed, output, _ = _run_draft(
        tmp_path,
        _bundle(),
        engines_file=engines_file,
    )

    assert completed.returncode == 2
    assert output["status"] == "blocked"
    assert "episode description" in " ".join(output["reasons"])


def test_draft_prompt_enumerates_allowed_source_identifiers(tmp_path):
    prompt_capture = tmp_path / "captured-prompt.txt"
    _, engines_file = _fake_engine(tmp_path, prompt_capture=prompt_capture)

    completed, _, _ = _run_draft(
        tmp_path,
        _bundle(),
        engines_file=engines_file,
    )

    prompt = prompt_capture.read_text(encoding="utf-8")
    assert completed.returncode == 0
    assert "https://example.test/archive" in prompt
    assert "fake-item-1" in prompt
    assert "Obviously Fake Archive Item" in prompt
    assert "https://example.test/book" in prompt
    assert (
        "every sources[].source MUST be exactly one of these identifiers; "
        "every factual claim and every number in the body must appear in a "
        "sources[].claim."
    ) in prompt


@pytest.mark.parametrize(
    ("source_ref", "expected_pass"),
    [
        ("https://example.test/archive", True),
        ("fake-item-1", True),
        ("Obviously Fake Archive Item", True),
        ("episode description", False),
    ],
    ids=["item-url", "item-id", "item-title", "free-text"],
)
def test_qa_source_identifier_contract_matches_draft(
    tmp_path, source_ref, expected_pass
):
    draft = _draft(
        "A specific archive idea. https://example.test/book",
        sources=[
            {
                "claim": "A specific archive idea.",
                "source": source_ref,
            }
        ],
    )

    completed, report, _ = _run_qa(tmp_path, draft)

    assert completed.returncode == 0
    assert report["pass"] is expected_pass
    if expected_pass:
        assert "invalid_source" not in _codes(report)
    else:
        invalid_source = next(
            defect for defect in report["defects"] if defect["code"] == "invalid_source"
        )
        assert source_ref in invalid_source["detail"]


def test_qa_marks_missing_sanitized_flag_without_calling_model(tmp_path):
    completed, report, _ = _run_qa(
        tmp_path,
        _draft("A specific archive idea. https://example.test/book"),
        bundle=_bundle(sanitized=False),
    )

    assert completed.returncode == 0
    assert _codes(report) == ["unsanitized_bundle"]
    assert report["pass"] is False


def test_revise_consumes_defects_and_increments_round(tmp_path):
    prompt_capture = tmp_path / "captured-revise-prompt.txt"
    _, engines_file = _fake_engine(tmp_path, prompt_capture=prompt_capture)
    prior = _write_json(
        tmp_path / "prior.json",
        _draft(
            "Leverage the archive. https://example.test/book",
            round=0,
        ),
    )
    report = _write_json(
        tmp_path / "report.json",
        {
            "pass": False,
            "defects": [{"code": "banned_word", "detail": "remove leverage"}],
            "engine": "claude_subscription",
        },
    )

    completed, output, _ = _run_draft(
        tmp_path,
        _bundle(),
        engines_file=engines_file,
        extra=[
            "--revise",
            "--prior-draft",
            str(prior),
            "--qa-report",
            str(report),
        ],
    )

    assert completed.returncode == 0
    assert output["round"] == 1
    assert output["engine"] == "codex_oauth"
    revise_prompt = prompt_capture.read_text(encoding="utf-8")
    assert "ALLOWED_SOURCE_IDS:" in revise_prompt
    assert "https://example.test/archive" in revise_prompt
    assert (
        "every sources[].source MUST be exactly one of these identifiers"
        in revise_prompt
    )


def test_round_three_is_refused_and_quarantined(tmp_path):
    _, engines_file = _fake_engine(tmp_path)
    prior = _write_json(
        tmp_path / "prior.json",
        _draft("A prior draft. https://example.test/book", round=2),
    )
    report = _write_json(
        tmp_path / "report.json",
        {
            "pass": False,
            "defects": [{"code": "voice", "detail": "still generic"}],
            "engine": "claude_subscription",
        },
    )

    completed, output, state = _run_draft(
        tmp_path,
        _bundle(),
        engines_file=engines_file,
        extra=[
            "--revise",
            "--prior-draft",
            str(prior),
            "--qa-report",
            str(report),
        ],
    )

    assert completed.returncode == 2
    assert "maximum round 2" in " ".join(output["reasons"])
    assert (state / "quarantine" / "fake-item-1.json").is_file()


def test_charter_engine_allowlist_controls_draft_and_qa(tmp_path):
    charter = _mutated_charter(
        tmp_path,
        lambda value: value["budget"].update(
            {"engine_allowlist": ["fixture_engine"]}
        ),
    )
    _, engines_file = _fake_engine(tmp_path)

    drafted, draft, _ = _run_draft(
        tmp_path,
        _bundle(),
        engine="fixture_engine",
        engines_file=engines_file,
        charter=charter,
    )
    reviewed, report, _ = _run_qa(
        tmp_path,
        _draft(
            "A specific archive idea. https://example.test/book",
            engine="codex_oauth",
        ),
        engine="fixture_engine",
        engines_file=engines_file,
        charter=charter,
    )

    assert drafted.returncode == 0
    assert draft["engine"] == "fixture_engine"
    assert reviewed.returncode == 0
    assert report["pass"] is True


def test_charter_max_edit_rounds_controls_draft_and_qa(tmp_path):
    charter = _mutated_charter(
        tmp_path,
        lambda value: value["qa_shape"].update({"max_edit_rounds": 3}),
    )
    _, engines_file = _fake_engine(tmp_path)
    prior = _write_json(
        tmp_path / "prior.json",
        _draft("A prior draft. https://example.test/book", round=2),
    )
    qa_report = _write_json(
        tmp_path / "report.json",
        {
            "pass": False,
            "defects": [{"code": "voice", "detail": "still generic"}],
            "engine": "claude_subscription",
        },
    )

    drafted, draft, _ = _run_draft(
        tmp_path,
        _bundle(),
        engines_file=engines_file,
        charter=charter,
        extra=[
            "--revise",
            "--prior-draft",
            str(prior),
            "--qa-report",
            str(qa_report),
        ],
    )
    reviewed, report, _ = _run_qa(
        tmp_path,
        draft,
        engines_file=engines_file,
        charter=charter,
    )

    assert drafted.returncode == 0
    assert draft["round"] == 3
    assert reviewed.returncode == 0
    assert report["pass"] is True


@pytest.mark.parametrize(
    "missing_key",
    ["engine_allowlist", "max_edit_rounds"],
)
def test_missing_charter_policy_keys_refuse_draft_and_qa(tmp_path, missing_key):
    def remove_policy(value):
        if missing_key == "engine_allowlist":
            del value["budget"]["engine_allowlist"]
        else:
            del value["qa_shape"]["max_edit_rounds"]

    charter = _mutated_charter(tmp_path, remove_policy)
    drafted, draft_output, _ = _run_draft(
        tmp_path,
        _bundle(),
        charter=charter,
    )
    reviewed, qa_output, _ = _run_qa(
        tmp_path,
        _draft("A specific archive idea. https://example.test/book"),
        charter=charter,
    )

    assert drafted.returncode == 2
    assert draft_output["status"] == "blocked"
    assert missing_key in " ".join(draft_output["reasons"])
    assert reviewed.returncode == 2
    assert qa_output["status"] == "blocked"
    assert missing_key in " ".join(qa_output["reasons"])


def test_qa_engine_crash_fails_closed(tmp_path):
    _, engines_file = _fake_engine(tmp_path, crash=True)

    completed, report, _ = _run_qa(
        tmp_path,
        _draft("A specific archive idea. https://example.test/book"),
        engines_file=engines_file,
    )

    assert completed.returncode == 0
    assert report["pass"] is False
    assert _codes(report) == ["qa_engine_unavailable"]
