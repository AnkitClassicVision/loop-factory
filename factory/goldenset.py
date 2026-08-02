"""Engine-agnostic golden-set loading and scoring."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard, not logic
    yaml = None


VALID_VERDICTS = {"block", "revise", "allow"}
MINIMUM_CASES = 5
# Promotion requires perfection on visible examples and at least 80% holdout accuracy.
OPEN_ACCURACY_REQUIRED = 1.0
HOLDOUT_ACCURACY_REQUIRED = 0.8


def _error(field: str, message: str) -> ValueError:
    return ValueError(f"golden set field {field}: {message}")


def _validate_cases(value: Any, split: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise _error(split, "must be a non-empty list")
    cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(value):
        field = f"{split}[{index}]"
        if not isinstance(raw_case, dict):
            raise _error(field, "must be a mapping")
        required = {"id", "input_summary", "expected_verdict"}
        missing = sorted(required - set(raw_case))
        if missing:
            raise _error(field, f"missing {', '.join(missing)}")
        unexpected = sorted(
            set(raw_case) - (required | {"expected_defect_codes"})
        )
        if unexpected:
            raise _error(field, f"unknown field(s): {', '.join(unexpected)}")
        case_id = raw_case["id"]
        if not isinstance(case_id, str) or not case_id.strip():
            raise _error(f"{field}.id", "must be a non-empty string")
        summary = raw_case["input_summary"]
        if not isinstance(summary, str) or not summary.strip():
            raise _error(f"{field}.input_summary", "must be a non-empty string")
        verdict = raw_case["expected_verdict"]
        if verdict not in VALID_VERDICTS:
            raise _error(
                f"{field}.expected_verdict",
                f"must be one of {sorted(VALID_VERDICTS)}",
            )
        normalized = {
            "id": case_id.strip(),
            "input_summary": summary.strip(),
            "expected_verdict": verdict,
        }
        if "expected_defect_codes" in raw_case:
            codes = raw_case["expected_defect_codes"]
            if not isinstance(codes, list) or any(
                not isinstance(code, str) or not code.strip() for code in codes
            ):
                raise _error(
                    f"{field}.expected_defect_codes",
                    "must be a list of non-empty strings",
                )
            normalized["expected_defect_codes"] = [code.strip() for code in codes]
        cases.append(normalized)
    return cases


def _validate_golden_set(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        raise _error("root", "must be a mapping with open and holdout splits")
    if set(value) != {"open", "holdout"}:
        missing = sorted({"open", "holdout"} - set(value))
        unexpected = sorted(set(value) - {"open", "holdout"})
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unexpected:
            details.append(f"unknown field(s): {', '.join(unexpected)}")
        raise _error("root", "; ".join(details))
    result = {
        "open": _validate_cases(value["open"], "open"),
        "holdout": _validate_cases(value["holdout"], "holdout"),
    }
    all_ids = [case["id"] for split in result.values() for case in split]
    if len(all_ids) < MINIMUM_CASES:
        raise _error("root", f"must contain at least {MINIMUM_CASES} cases total")
    if len(all_ids) != len(set(all_ids)):
        raise _error("id", "case ids must be unique across both splits")
    return result


def load_golden_set(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    """Load a YAML or JSON golden set and validate both splits."""
    if yaml is None:
        raise ValueError("PyYAML is required to load a golden set")
    golden_path = Path(path)
    try:
        loaded = yaml.safe_load(golden_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"golden set path {golden_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"golden set YAML/JSON {golden_path}: {exc}") from exc
    return _validate_golden_set(loaded)


def score(
    cases: dict[str, list[dict[str, Any]]],
    judge_fn: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    """Score an injected judge without knowing or invoking its engine."""
    validated = _validate_golden_set(cases)
    failures: list[str] = []
    correct_by_split: dict[str, int] = {}
    for split in ("open", "holdout"):
        correct = 0
        for case in validated[split]:
            if judge_fn(case) == case["expected_verdict"]:
                correct += 1
            else:
                failures.append(case["id"])
        correct_by_split[split] = correct
    open_accuracy = correct_by_split["open"] / len(validated["open"])
    holdout_accuracy = correct_by_split["holdout"] / len(validated["holdout"])
    total = len(validated["open"]) + len(validated["holdout"])
    accuracy = (correct_by_split["open"] + correct_by_split["holdout"]) / total
    return {
        "passed": (
            open_accuracy == OPEN_ACCURACY_REQUIRED
            and holdout_accuracy >= HOLDOUT_ACCURACY_REQUIRED
        ),
        "accuracy": accuracy,
        "failures": failures,
    }
