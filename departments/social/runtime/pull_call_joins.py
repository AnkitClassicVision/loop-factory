"""SG-SENSE N2 — join discovery-call bookings to social attribution.

Independent of the department's own claims (charter setpoints.outcome.sensor
== independent_calendar_hubspot_join). Reads a pre-joined calendar export
(the real calendar/HubSpot wiring is a later seam; this node builds the join
logic against the export shape). An unreadable/missing export is reported as
missing, never fabricated as zero (charter C13). Ambiguous rows (missing
fields, duplicate event ids) are quarantined for owner review, never guessed
(exceptions.ambiguous_item), and excluded from the counted totals.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("pull_call_joins")

REQUIRED_FIELDS = ("event_id", "start", "source_tag", "contact_ref")


class JoinError(RuntimeError):
    """Raised when the calendar export is missing, unreadable, or malformed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_export(path: str | Path) -> list[dict[str, Any]]:
    export_path = Path(path)
    if not export_path.exists():
        raise JoinError(f"calendar export not found: {export_path}")
    try:
        text = export_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise JoinError(f"calendar export unreadable: {export_path}: {exc}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JoinError(f"calendar export is malformed JSON: {export_path}: {exc}") from exc
    if not isinstance(value, list):
        raise JoinError(f"calendar export must be a JSON list: {export_path}")
    return value


def join_bookings(
    rows: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split export rows into clean (independently attributable) and quarantined."""
    event_id_counts: Counter[str] = Counter(
        str(row.get("event_id")) for row in rows if isinstance(row, dict) and row.get("event_id")
    )
    clean: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            quarantined.append({"item_id": f"index-{idx}", "reason": "booking entry is not an object"})
            continue
        event_id = row.get("event_id")
        missing = [field for field in REQUIRED_FIELDS if not row.get(field)]
        if missing:
            quarantined.append(
                {
                    "item_id": str(event_id or f"index-{idx}"),
                    "reason": f"missing fields {missing}; ambiguous item",
                }
            )
            continue
        if event_id_counts[str(event_id)] > 1:
            quarantined.append(
                {
                    "item_id": str(event_id),
                    "reason": "duplicate event_id across export; cannot resolve independently",
                }
            )
            continue
        clean.append(row)
    return clean, quarantined


def build_rows(clean: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    total = len(clean)
    rows = [
        {
            "metric": "discovery_calls_booked",
            "value": float(total),
            "source": "calendar_export_join",
            "ts": now,
        }
    ]
    by_source_tag: Counter[str] = Counter(str(row["source_tag"]) for row in clean)
    for source_tag in sorted(by_source_tag):
        rows.append(
            {
                "metric": "discovery_calls_booked_by_source",
                "value": float(by_source_tag[source_tag]),
                "source": "calendar_export_join",
                "ts": now,
                "source_tag": source_tag,
            }
        )
    return rows


def write_quarantine(state_dir: str | Path, quarantined: list[dict[str, Any]]) -> None:
    if not quarantined:
        return
    qdir = Path(state_dir) / "quarantine"
    qdir.mkdir(parents=True, exist_ok=True)
    for item in quarantined:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(item["item_id"]))
        path = qdir / f"{safe_id}.json"
        path.write_text(
            json.dumps({**item, "node": "pull_call_joins", "ts": _now()}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def write_rows(out_path: str | Path, rows: list[dict[str, Any]]) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def write_missing(out_path: str | Path, reason: str) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"status": "missing", "reason": reason, "source": "calendar_export_join", "ts": _now()},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Join discovery-call bookings to social attribution")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--calendar-export", required=True)
    args = parser.parse_args()

    try:
        raw_rows = load_export(args.calendar_export)
    except JoinError as exc:
        logger.error("calendar export unavailable: %s", exc)
        write_missing(args.out, str(exc))
        raise SystemExit(3)

    clean, quarantined = join_bookings(raw_rows)
    write_quarantine(args.state_dir, quarantined)
    rows = build_rows(clean, _now())
    write_rows(args.out, rows)
    logger.info(
        "joined %d bookings (%d quarantined) from %d export rows",
        len(clean), len(quarantined), len(raw_rows),
    )
    raise SystemExit(0)


if __name__ == "__main__":
    main()
