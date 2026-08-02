import json

import pytest

from factory import goldenset


CASES = {
    "open": [
        {"id": "o1", "input_summary": "clean", "expected_verdict": "allow"},
        {
            "id": "o2",
            "input_summary": "critical claim",
            "expected_verdict": "block",
            "expected_defect_codes": ["unsupported_claim"],
        },
    ],
    "holdout": [
        {"id": "h1", "input_summary": "minor issue", "expected_verdict": "allow"},
        {"id": "h2", "input_summary": "edit needed", "expected_verdict": "revise"},
        {"id": "h3", "input_summary": "unsafe", "expected_verdict": "block"},
    ],
}


def _write(tmp_path, cases=CASES, suffix=".yaml"):
    path = tmp_path / f"golden{suffix}"
    if suffix == ".json":
        path.write_text(json.dumps(cases), encoding="utf-8")
    else:
        import yaml

        path.write_text(yaml.safe_dump(cases), encoding="utf-8")
    return path


def test_valid_set_loads_with_open_and_holdout_splits(tmp_path):
    loaded = goldenset.load_golden_set(_write(tmp_path))
    assert [case["id"] for case in loaded["open"]] == ["o1", "o2"]
    assert [case["id"] for case in loaded["holdout"]] == ["h1", "h2", "h3"]


def test_json_golden_set_loads(tmp_path):
    assert goldenset.load_golden_set(_write(tmp_path, suffix=".json"))["holdout"][0]["id"] == "h1"


def test_fewer_than_five_cases_are_rejected(tmp_path):
    too_small = {"open": CASES["open"], "holdout": CASES["holdout"][:2]}
    with pytest.raises(ValueError, match="at least 5 cases"):
        goldenset.load_golden_set(_write(tmp_path, too_small))


def test_score_passes_with_perfect_judge():
    result = goldenset.score(CASES, lambda case: case["expected_verdict"])
    assert result == {"passed": True, "accuracy": 1.0, "failures": []}


def test_score_fails_when_holdout_accuracy_is_below_point_eight():
    def judge(case):
        return case["expected_verdict"] if case["id"] in {"o1", "o2", "h1", "h2"} else "allow"

    result = goldenset.score(CASES, judge)
    assert result["passed"] is False
    assert result["accuracy"] == pytest.approx(0.8)


def test_score_failure_lists_failing_case_ids():
    wrong = {"o2", "h2"}
    result = goldenset.score(
        CASES,
        lambda case: "allow" if case["id"] in wrong else case["expected_verdict"],
    )
    assert result["failures"] == ["o2", "h2"]
    assert result["passed"] is False
