from __future__ import annotations

import copy
from pathlib import Path

import yaml

from factory.creation_contract import check, main


TEMPLATE = Path(__file__).parents[1] / "templates" / "department-creation-contract.yaml"


def _contract():
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def _write(tmp_path, data):
    path = tmp_path / "department-creation-contract.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _fails(tmp_path, capsys, data, field):
    assert main(["check", str(_write(tmp_path, data))]) == 1
    output = capsys.readouterr().out
    assert "WHY" in output
    assert field in output


def test_actual_template_validates():
    result = check(TEMPLATE)
    assert result["f0_inputs"] == {
        "name": "example-department",
        "owner": "example-owner",
    }
    assert result["ringer_unit_ids"] == [
        "proof-01-contract-validation",
        "proof-02-shadow-proof",
    ]


def test_emit_json_on_pass(capsys):
    assert main(["check", str(TEMPLATE), "--emit", "json"]) == 0
    output = capsys.readouterr().out
    assert '"f0_inputs"' in output
    assert '"ringer_unit_ids"' in output


def test_missing_authority_concern_fails_with_why(tmp_path, capsys):
    data = _contract()
    del data["canonical_authorities"]["healing"]
    _fails(tmp_path, capsys, data, "canonical_authorities")


def test_duplicate_authority_concern_fails_with_why(tmp_path, capsys):
    text = TEMPLATE.read_text(encoding="utf-8").replace(
        "  transition: kernel.transition\n",
        "  transition: kernel.transition\n  transition: another.transition\n",
    )
    path = tmp_path / "duplicate.yaml"
    path.write_text(text, encoding="utf-8")
    assert main(["check", str(path)]) == 1
    output = capsys.readouterr().out
    assert "WHY" in output
    assert "transition" in output


def test_unknown_authority_concern_fails_with_why(tmp_path, capsys):
    data = _contract()
    data["canonical_authorities"]["other_store"] = "factory.other"
    _fails(tmp_path, capsys, data, "canonical_authorities")


def test_blocking_open_question_fails_with_why(tmp_path, capsys):
    data = _contract()
    data["open_questions"][0]["blocking"] = True
    _fails(tmp_path, capsys, data, "open_questions[0].blocking")


def test_empty_decision_sources_fails_with_why(tmp_path, capsys):
    data = _contract()
    data["decision_sources"] = []
    _fails(tmp_path, capsys, data, "decision_sources")


def test_f1_source_absent_from_decision_sources_fails_with_why(tmp_path, capsys):
    data = _contract()
    data["f1_answers"][0]["source"] = "decisions/missing.md"
    _fails(tmp_path, capsys, data, "f1_answers[0].source")


def test_missing_binary_exit_test_fails_with_why(tmp_path, capsys):
    data = _contract()
    del data["destination"]["binary_exit_test"]
    _fails(tmp_path, capsys, data, "destination.binary_exit_test")


def test_malformed_yaml_is_clean_failure_without_traceback(tmp_path, capsys):
    path = tmp_path / "broken.yaml"
    path.write_text("destination: [unterminated\n", encoding="utf-8")
    assert main(["check", str(path)]) == 1
    output = capsys.readouterr().out
    assert "WHY contract" in output
    assert "malformed YAML" in output
    assert "Traceback" not in output


def test_authority_may_own_multiple_concerns(tmp_path):
    data = copy.deepcopy(_contract())
    data["canonical_authorities"]["telemetry"] = data["canonical_authorities"]["run_identity"]
    assert check(_write(tmp_path, data))["f0_inputs"]["name"] == "example-department"
