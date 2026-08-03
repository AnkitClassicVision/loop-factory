"""Validated append-only evaluator score records, separate from receipts."""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel.jsonl_store import append_jsonl


SCHEMA_VERSION = "score-record/v1"
# Runner-injected spool location (canonical name: kernel/capabilities.py).
# When present, appends land in the spool; the runner stamps identity from
# its own execution state at promotion — never from the emitter's claims.
RECORD_SPOOL_ENV = "OE_RECORD_SPOOL"
# "promotion" is stamped by the runner at promotion time (identity +
# signature) — never by an emitter.
OPTIONAL_FIELDS = frozenset({"promotion"})
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


def _require_text(field: str, value: Any, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def validate_score(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("score record must be an object")
    missing = sorted(FIELDS - set(record))
    unknown = sorted(set(record) - FIELDS - OPTIONAL_FIELDS)
    if missing or unknown:
        raise ValueError(f"score fields mismatch: missing={missing}, unknown={unknown}")
    if "promotion" in record and not isinstance(record["promotion"], dict):
        raise ValueError("promotion must be an object")
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
    if not isinstance(target, dict) or set(target) != TARGET_FIELDS:
        raise ValueError("target_ref must contain run_id, step_id, node, department")
    for field in ("node", "department"):
        _require_text(f"target_ref.{field}", target[field])
    for field in ("run_id", "step_id"):
        _require_text(f"target_ref.{field}", target[field], nullable=True)
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

    Runner-mediated appends (review B1, Option C): inside a graph-runner
    node process (RECORD_SPOOL_ENV present) the row lands in the per-attempt
    spool — the canonical stream is unreachable from node code through this
    API. The runner stamps target_ref.department and target_ref.run_id from
    its own execution state at promotion; target_ref.node stays the
    SUBJECT of the score (a judge node scoring another node is legitimate).
    """
    validated = validate_score(record)
    spool = os.environ.get(RECORD_SPOOL_ENV)
    target_dir = Path(spool) if spool else Path(state_dir)
    return append_jsonl(target_dir / "scores.jsonl", validated)
