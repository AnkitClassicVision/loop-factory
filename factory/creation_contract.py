"""Load and validate the Stage 3 department Creation Contract."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    yaml = None


AUTHORITY_CONCERNS = frozenset({
    "transition",
    "run_identity",
    "telemetry",
    "eval_policy",
    "eval_evidence",
    "board_projection",
    "healing",
    "escalation",
})


class CreationContractError(RuntimeError):
    """The contract is absent, malformed, ambiguous, or incomplete."""


if yaml is not None:
    class _UniqueKeyLoader(yaml.SafeLoader):
        pass


    def _construct_unique_mapping(loader, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise CreationContractError(f"duplicate field {key!r}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping


    _UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        _construct_unique_mapping,
    )


def _why(field: str, reason: str) -> CreationContractError:
    return CreationContractError(f"WHY {field}: {reason}")


def _mapping(value: Any, field: str) -> dict:
    if not isinstance(value, dict):
        raise _why(field, "must be a mapping")
    return value


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _why(field, "must be a non-empty string")
    return value.strip()


def _list(value: Any, field: str, *, nonempty: bool = False) -> list:
    if not isinstance(value, list) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise _why(field, f"must be a {qualifier}list")
    return value


def load(path: str | Path) -> dict:
    """Parse a contract without allowing YAML duplicate-key ambiguity."""
    if yaml is None:
        raise _why("contract", "PyYAML is required")
    path = Path(path)
    if not path.is_file():
        raise _why("contract", f"file not found: {path}")
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except CreationContractError as exc:
        message = str(exc)
        if message.startswith("WHY "):
            raise
        raise _why("canonical_authorities", message) from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise _why("contract", f"malformed YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise _why("contract", "top level must be a mapping")
    return data


def validate(data: dict) -> dict:
    """Validate and return normalized values needed by downstream stages."""
    contract = _mapping(data, "contract")
    destination = _mapping(contract.get("destination"), "destination")
    _nonempty_string(destination.get("statement"), "destination.statement")
    _nonempty_string(
        destination.get("binary_exit_test"), "destination.binary_exit_test"
    )
    proof_department = _nonempty_string(
        destination.get("proof_department"), "destination.proof_department"
    )
    _nonempty_string(contract.get("proof_loop"), "proof_loop")
    owner = _nonempty_string(contract.get("owner"), "owner")
    _nonempty_string(contract.get("factory_version_target"), "factory_version_target")
    _nonempty_string(
        contract.get("privacy_external_effect_boundary"),
        "privacy_external_effect_boundary",
    )

    authorities = _mapping(
        contract.get("canonical_authorities"), "canonical_authorities"
    )
    unknown = sorted(set(authorities) - AUTHORITY_CONCERNS)
    if unknown:
        raise _why("canonical_authorities", f"unknown concern(s): {', '.join(unknown)}")
    missing = sorted(AUTHORITY_CONCERNS - set(authorities))
    if missing:
        raise _why("canonical_authorities", f"missing concern(s): {', '.join(missing)}")
    for concern, authority in authorities.items():
        _nonempty_string(authority, f"canonical_authorities.{concern}")

    decision_sources = _list(
        contract.get("decision_sources"), "decision_sources", nonempty=True
    )
    source_set = set()
    for index, source in enumerate(decision_sources):
        source_set.add(_nonempty_string(source, f"decision_sources[{index}]"))

    answers = _list(contract.get("f1_answers"), "f1_answers")
    for index, answer in enumerate(answers):
        item = _mapping(answer, f"f1_answers[{index}]")
        _nonempty_string(item.get("question_id"), f"f1_answers[{index}].question_id")
        _nonempty_string(item.get("answer"), f"f1_answers[{index}].answer")
        source = _nonempty_string(item.get("source"), f"f1_answers[{index}].source")
        if source not in source_set:
            raise _why(
                f"f1_answers[{index}].source",
                f"{source!r} is not listed in decision_sources",
            )

    open_questions = _list(contract.get("open_questions"), "open_questions")
    for index, question in enumerate(open_questions):
        item = _mapping(question, f"open_questions[{index}]")
        _nonempty_string(
            item.get("question_id"), f"open_questions[{index}].question_id"
        )
        blocking = item.get("blocking")
        if not isinstance(blocking, bool):
            raise _why(f"open_questions[{index}].blocking", "must be a boolean")
        if blocking:
            raise _why(
                f"open_questions[{index}].blocking",
                "build-blocking question remains open",
            )

    matrix = _list(contract.get("proof_matrix"), "proof_matrix", nonempty=True)
    unit_ids = []
    for index, proof in enumerate(matrix):
        item = _mapping(proof, f"proof_matrix[{index}]")
        stage = _nonempty_string(item.get("stage"), f"proof_matrix[{index}].stage")
        _nonempty_string(item.get("gate"), f"proof_matrix[{index}].gate")
        slug = re.sub(r"[^a-z0-9]+", "-", stage.lower()).strip("-")
        unit_ids.append(f"proof-{index + 1:02d}-{slug}")

    return {
        "f0_inputs": {"name": proof_department, "owner": owner},
        "ringer_unit_ids": unit_ids,
    }


def check(path: str | Path) -> dict:
    """Load, validate, and return the derived Stage 4/9 handoff."""
    return validate(load(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Creation Contract")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("path")
    check_parser.add_argument("--emit", choices=("json",))
    args = parser.parse_args(argv)
    try:
        result = check(args.path)
    except CreationContractError as exc:
        message = str(exc)
        print(message if message.startswith("WHY ") else f"WHY contract: {message}")
        return 1
    if args.emit == "json":
        print(json.dumps(result, sort_keys=True))
    else:
        print("PASS creation contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
