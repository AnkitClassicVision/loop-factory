#!/usr/bin/env python3
"""Deterministically observe SOCIAL's owner-ratified objective contracts."""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA = "objectives-observed/v1"
AGED_AFTER = timedelta(days=7)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp is not a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("JSONL row is not an object")
        if row.get("status") == "missing":
            raise ValueError("source reports missing")
        rows.append(row)
    return rows


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _verified_pct(path: Path) -> float:
    rows = _jsonl(path)
    evidence = [row for row in rows if row.get("metric") == "platform_verified"]
    if not evidence:
        raise ValueError("no platform verification rows")
    values = [row.get("value") for row in evidence]
    if any(value not in (0, 0.0, 1, 1.0) or isinstance(value, bool) for value in values):
        raise ValueError("platform verification value is not binary")
    return sum(float(value) for value in values) * 100.0 / len(values)


def _posting_volume(path: Path, now: datetime) -> int:
    rows = _jsonl(path)
    monday = (now - timedelta(days=now.isoweekday() - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    count = 0
    for row in rows:
        epoch = row.get("now")
        if not _number(epoch):
            raise ValueError("posting ledger row lacks numeric now")
        stamped = datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        if monday <= stamped <= now:
            count += 1
    return count


def _quarantine_aged(path: Path, now: datetime) -> int:
    if not path.is_dir():
        raise OSError("quarantine source missing")
    count = 0
    for item in path.glob("*.json"):
        value = json.loads(item.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("quarantine item is not an object")
        stamped = _iso(value.get("ts"))
        if now - stamped > AGED_AFTER:
            count += 1
    return count


def _baseline_rows(path: Path, allowed: set[str], ts: str) -> list[dict[str, Any]]:
    rows = _jsonl(path)
    result: list[dict[str, Any]] = []
    for row in rows:
        metric = row.get("metric")
        value = row.get("value")
        if metric in allowed and _number(value):
            result.append({"ts": ts, "metric": metric, "value": value})
    return result


def observe(root: Path, *, now: datetime | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    now = (now or _utc_now()).astimezone(timezone.utc)
    state = root / "departments" / "social" / "state"
    ts = now.isoformat()
    values: dict[str, Any] = {}
    sources = (
        ("platform_verified_delivery_pct", state / "zernio_analytics.jsonl", _verified_pct),
        ("posting_volume_week", state / "kernel" / "frequency.jsonl", lambda path: _posting_volume(path, now)),
        ("quarantine_backlog_aged", state / "quarantine", lambda path: _quarantine_aged(path, now)),
    )
    for metric, path, reader in sources:
        try:
            values[metric] = reader(path)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            pass

    baselines: list[dict[str, Any]] = []
    try:
        baselines.extend(_baseline_rows(
            state / "call_joins.jsonl", {"discovery_calls_booked"}, ts
        ))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        pass
    try:
        baselines.extend(_baseline_rows(
            state / "zernio_analytics.jsonl",
            {"impressions", "engagement", "engagement_rate", "engagement_rate_per_surface"},
            ts,
        ))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {"schema": SCHEMA, "ts": ts, "values": values}, baselines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    state = root / "departments" / "social" / "state"
    observed, baselines = observe(root)
    _atomic_json(state / "objectives_observed.json", observed)
    baseline_path = state / "objective_baselines.jsonl"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    if baselines:
        with baseline_path.open("a", encoding="utf-8") as handle:
            for row in baselines:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    if args.out:
        _atomic_json(args.out, {
            "status": "observed", "schema": SCHEMA,
            "observed_metrics": sorted(observed["values"]),
            "baseline_rows_appended": len(baselines),
            "external_actions_taken": 0,
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
