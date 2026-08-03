"""Validated append-only evaluator score records, separate from receipts."""
from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel import graph_context as _graph_context
from kernel.jsonl_store import append_jsonl


SCHEMA_VERSION = "score-record/v1"


def _load_graph_context():
    """The ambient runner-signed identity, or None outside the runner.
    Malformed/expired/forged (where checkable) tokens raise — fail-closed."""
    return _graph_context.load_context(now=time.time())
SOURCES = frozenset({"script", "judge", "human"})
FIELDS = frozenset(
    {
        "gen_ai.evaluation.name",
        "gen_ai.evaluation.score.value",
        "gen_ai.evaluation.score.label",
        "gen_ai.evaluation.explanation",
        "source",
        "judge_model",
        "config_version",
        "target_ref",
        "ts",
        "schema_version",
    }
)
TARGET_FIELDS = frozenset({"run_id", "step_id", "node", "department"})
# Additive, backward-compatible: pre-existing emitters omit it entirely.
OPTIONAL_TARGET_FIELDS = frozenset({"graph_run_id"})


def _require_text(field: str, value: Any, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def validate_score(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("score record must be an object")
    if set(record) != FIELDS:
        missing = sorted(FIELDS - set(record))
        unknown = sorted(set(record) - FIELDS)
        raise ValueError(f"score fields mismatch: missing={missing}, unknown={unknown}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must equal {SCHEMA_VERSION}")
    for field in (
        "gen_ai.evaluation.name",
        "gen_ai.evaluation.score.label",
        "gen_ai.evaluation.explanation",
        "config_version",
        "ts",
    ):
        _require_text(field, record[field])
    explanation = record["gen_ai.evaluation.explanation"]
    if len(explanation) > 500 or "\n" in explanation or "\r" in explanation:
        raise ValueError(
            "gen_ai.evaluation.explanation must be a single line of at most 500 characters"
        )
    _require_text("judge_model", record["judge_model"], nullable=True)
    value = record["gen_ai.evaluation.score.value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("gen_ai.evaluation.score.value must be numeric")
    if record["source"] not in SOURCES:
        raise ValueError(f"source must be one of {sorted(SOURCES)}")
    if record["source"] == "judge" and record["judge_model"] is None:
        raise ValueError("judge_model is required when source is judge")
    target = record["target_ref"]
    if (
        not isinstance(target, dict)
        or not TARGET_FIELDS <= set(target)
        or set(target) - TARGET_FIELDS - OPTIONAL_TARGET_FIELDS
    ):
        raise ValueError(
            "target_ref must contain run_id, step_id, node, department "
            "(graph_run_id optional)"
        )
    for field in ("node", "department"):
        _require_text(f"target_ref.{field}", target[field])
    for field in ("run_id", "step_id"):
        _require_text(f"target_ref.{field}", target[field], nullable=True)
    if "graph_run_id" in target:
        _require_text("target_ref.graph_run_id", target["graph_run_id"],
                      nullable=True)
    try:
        json.dumps(record, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"score record must be finite JSON: {exc}") from exc
    return copy.deepcopy(record)


def build_score(
    *,
    name: str,
    value: int | float,
    label: str,
    explanation: str,
    source: str,
    judge_model: str | None,
    config_version: str,
    target_ref: dict[str, Any],
    ts: str | None = None,
) -> dict[str, Any]:
    # Inside a graph-runner node process the runner-signed identity is the
    # default; an explicitly supplied graph_run_id is left for append_score
    # to gate against the token.
    if isinstance(target_ref, dict) and "graph_run_id" not in target_ref:
        context = _load_graph_context()
        if context is not None:
            target_ref = {**target_ref, "graph_run_id": context["run_id"]}
    return validate_score(
        {
            "gen_ai.evaluation.name": name,
            "gen_ai.evaluation.score.value": value,
            "gen_ai.evaluation.score.label": label,
            "gen_ai.evaluation.explanation": explanation,
            "source": source,
            "judge_model": judge_model,
            "config_version": config_version,
            "target_ref": target_ref,
            "ts": ts or datetime.now(timezone.utc).isoformat(),
            "schema_version": SCHEMA_VERSION,
        }
    )


def append_score(state_dir: str | Path, record: dict[str, Any]) -> Path:
    """Validate and append one score to ``state/scores.jsonl``.

    Fail-closed identity gate: inside a graph-runner node process (the runner
    injects a SIGNED graph context) a score whose target_ref is missing or
    carries a different graph_run_id is refused, never silently appended.
    target_ref.node is deliberately NOT matched against the token's node: it
    names the SUBJECT of the score, and a judge node scoring another node's
    output inside the same run is legitimate.
    """
    context = _load_graph_context()
    validated = validate_score(record)
    if context is not None:
        supplied = validated["target_ref"].get("graph_run_id")
        if supplied != context["run_id"]:
            raise ValueError(
                "target_ref.graph_run_id must match the runner-signed "
                "graph context"
            )
    return append_jsonl(Path(state_dir) / "scores.jsonl", validated)
