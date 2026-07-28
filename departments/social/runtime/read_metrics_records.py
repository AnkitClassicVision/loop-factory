"""Build a grounded evidence pack from SG-SENSE observation records only."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
_KINDS = ("published", "engagement", "quarantine", "qa_defect")
_ALLOWED_SOURCES = frozenset({"zernio", "calendar_join", "compare_charter"})
_ALLOWED_FIELDS = frozenset(
    {"metric", "value", "status", "source", "ts", "post_ref", "surface", "row_id"}
)
_SENSITIVE_KEYS = frozenset({"body", "message", "text", "email", "phone"})


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _row_id(row: dict[str, Any], line_number: int) -> str:
    supplied = row.get("row_id", row.get("id"))
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{line_number}:{canonical}".encode()).hexdigest()[:16]
    return f"obs-{digest}"


def _kind(row: dict[str, Any]) -> str | None:
    explicit = row.get("kind", row.get("category"))
    if explicit in _KINDS:
        return str(explicit)
    metric = str(row.get("metric", "")).lower()
    if "quarantine" in metric:
        return "quarantine"
    if ("qa" in metric and "defect" in metric) or metric.startswith("defect."):
        return "qa_defect"
    if any(token in metric for token in ("engagement", "reaction", "comment", "share", "click")):
        return "engagement"
    if any(token in metric for token in ("published", "post_count", "posts_published")):
        return "published"
    return None


def _dimensions(row: dict[str, Any]) -> tuple[str, str, str]:
    dims = row.get("dimensions") if isinstance(row.get("dimensions"), dict) else {}
    return tuple(
        str(row.get(name, dims.get(name, "unknown"))) for name in ("surface", "lane", "item_type")
    )


def _provenance(row: dict[str, Any]) -> dict[str, Any]:
    return {"row_id": row["row_id"], "ts": row["ts"]}


def build_evidence_pack(
    observations: str | Path,
    *,
    assembled_at: str | None = None,
) -> dict[str, Any]:
    """Aggregate observation rows while retaining exact row-level provenance."""
    source_path = Path(observations)
    if not source_path.exists():
        raise FileNotFoundError(f"observations missing: {source_path}")

    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, raw in enumerate(source_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid observation JSON on line {line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"observation line {line_number} must be an object")
        source = row.get("source")
        if source not in _ALLOWED_SOURCES:
            LOGGER.warning("dropping non-SG-SENSE source on line %s: %r", line_number, source)
            continue
        sensitive = sorted(key for key in row if str(key).lower() in _SENSITIVE_KEYS)
        if sensitive:
            LOGGER.warning(
                "dropping sensitive-shaped observation on line %s; keys=%s",
                line_number,
                sensitive,
            )
            continue
        required = {"metric", "source", "ts"}
        missing = sorted(required - row.keys())
        if missing or ("value" not in row and row.get("status") != "missing"):
            raise ValueError(
                f"observation line {line_number} violates SG-SENSE schema; missing={missing}"
            )
        row_id = _row_id(row, line_number)
        if row_id in seen_ids:
            raise ValueError(f"duplicate observation row id: {row_id}")
        seen_ids.add(row_id)
        normalized = {key: row[key] for key in _ALLOWED_FIELDS if key in row}
        normalized["row_id"] = row_id
        rows.append(normalized)

    grouped: dict[tuple[str, str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        kind = _kind(row)
        if kind is not None:
            grouped[_dimensions(row)][kind].append(row)

    aggregates: list[dict[str, Any]] = []
    for (surface, lane, item_type), buckets in sorted(grouped.items()):
        aggregate: dict[str, Any] = {
            "surface": surface,
            "lane": lane,
            "item_type": item_type,
        }
        for kind in _KINDS:
            bucket = buckets.get(kind, [])
            numeric = [
                float(row["value"])
                for row in bucket
                if row.get("status") != "missing"
                and isinstance(row.get("value"), (int, float))
                and not isinstance(row.get("value"), bool)
            ]
            summary: dict[str, Any] = {
                "observed_rows": len(bucket),
                "missing_rows": sum(row.get("status") == "missing" for row in bucket),
                "source_row_ids": [row["row_id"] for row in bucket],
                "provenance": [_provenance(row) for row in bucket],
            }
            if numeric:
                summary["value_sum"] = sum(numeric)
                summary["value_mean"] = sum(numeric) / len(numeric)
                summary["value_min"] = min(numeric)
                summary["value_max"] = max(numeric)
            if kind == "qa_defect":
                frequency: dict[str, int] = defaultdict(int)
                for row in bucket:
                    code = row.get("code")
                    if not code and "." in str(row["metric"]):
                        code = str(row["metric"]).rsplit(".", 1)[-1]
                    amount = row.get("value", 1)
                    frequency[str(code or "unknown")] += (
                        int(amount)
                        if isinstance(amount, (int, float)) and not isinstance(amount, bool)
                        else 1
                    )
                summary["frequency_by_code"] = dict(sorted(frequency.items()))
            aggregate[kind] = summary
        aggregates.append(aggregate)

    return {
        "version": "sg-learn-evidence-v1",
        "assembled_at": assembled_at or datetime.now(timezone.utc).isoformat(),
        "source": "SG-SENSE",
        "sanitized": True,
        "rows": rows,
        "aggregates": aggregates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate SG-SENSE observation records")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        pack = build_evidence_pack(args.observations)
    except FileNotFoundError as exc:
        LOGGER.error("%s", exc)
        _write_json(args.out, {"status": "missing", "reason": str(exc)})
        return 3
    except (OSError, ValueError) as exc:
        LOGGER.error("%s", exc)
        _write_json(args.out, {"status": "missing", "reason": str(exc)})
        return 3
    _write_json(args.out, pack)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
