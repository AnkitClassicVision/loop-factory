"""Deterministically derive funnel flow and stock floors from governed inputs."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from factory.charter_loader import funnel_config, load_charter
from factory.events_ledger import LedgerError, read_transitions


HEADER = "# MACHINE-WRITTEN — derived; humans set goals in charter.yaml\n"


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _previous(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        floors = value.get("floors", value)
        return floors if isinstance(floors, dict) else {}
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return {}


def _freeze_reason(state: Path, now: datetime) -> str | None:
    objectives = state / "objectives_observed.json"
    if objectives.exists():
        try:
            values = json.loads(objectives.read_text(encoding="utf-8"))["values"]
            for key in ("state_drift", "unledgered_inbound"):
                if values.get(key) != 0 and key in values:
                    return f"{key} != 0"
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass
    manifest_dir = state / "run-manifests"
    if manifest_dir.is_dir():
        manifests = [p for p in manifest_dir.glob("*.json") if not p.name.endswith(".verdict.json")]
        if manifests:
            newest = max(manifests, key=lambda path: path.stat().st_mtime)
            verdict = newest.with_name(f"{newest.stem}.verdict.json")
            try:
                if json.loads(verdict.read_text(encoding="utf-8")).get("status") == "red":
                    return "newest run-manifest verdict is red"
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
    malformed = read_transitions(
        state, from_stage="__scan__", to_stage="__scan__",
        since=datetime.min.replace(tzinfo=timezone.utc), until=now,
    ).malformed
    if malformed:
        return f"events ledger has {malformed} malformed line(s)"
    return None


def _all_events(state: Path) -> list[dict]:
    path = state / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            timestamp = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
            row = dict(row)
            row["_ts"] = timestamp.astimezone(timezone.utc)
            rows.append(row)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return rows


def _rate(row: dict, state: Path, now: datetime, old: float) -> tuple[float, str]:
    events = _all_events(state)
    cutoff = now - timedelta(days=28)
    maturity_cutoff = now - timedelta(days=row["maturity_days"])
    entries: dict[str, datetime] = {}
    for event in events:
        if event.get("to_stage") == row["from"] and cutoff <= event["_ts"] <= maturity_cutoff:
            entries.setdefault(event["subject_id"], event["_ts"])
    converted = set()
    for event in events:
        subject = event.get("subject_id")
        if (
            subject in entries
            and event.get("from_stage") == row["from"]
            and event.get("to_stage") == row["to"]
            and entries[subject] <= event["_ts"] <= now
        ):
            converted.add(subject)
    if len(entries) < 30 or len(converted) < 10:
        return float(row["prior_rate"]), "prior"
    measured = len(converted) / len(entries)
    return 0.75 * old + 0.25 * measured, "blended"


def _cap(old: int, proposed: int) -> int:
    return min(math.floor(old * 1.2), max(math.ceil(old * 0.8), proposed))


def _persist(state: Path, result: dict) -> None:
    state.mkdir(parents=True, exist_ok=True)
    with (state / "floors-history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True) + "\n")


def compile_floors(dept_dir, state_dir, *, now=None) -> dict:
    dept, state, current = Path(dept_dir), Path(state_dir), _utc(now)
    computed_at = current.isoformat()
    config = funnel_config(load_charter(dept / "charter.yaml", expect_department=dept.name))
    if config is None:
        result = {"status": "unconfigured", "reason": "charter has no funnel section", "floors": {}, "changes": [], "computed_at": computed_at}
        _persist(state, result)
        return result
    floors_path = dept / "floors.yaml"
    previous = _previous(floors_path)
    freeze = _freeze_reason(state, current)
    if freeze:
        result = {"status": "frozen", "reason": freeze, "floors": previous, "changes": [], "computed_at": computed_at}
        _persist(state, result)
        return result

    terminal = config["end_goal"]
    proposed: dict[str, dict[str, Any]] = {
        terminal["stage"]: {"flow_per_week": terminal["per_week"], "stock_min": 0, "rate_used": 1.0, "rate_source": "prior"}
    }
    downstream_flow = terminal["per_week"]
    for transition in reversed(config["transitions"]):
        old_rate = float(previous.get(transition["from"], {}).get("rate_used", transition["prior_rate"]))
        rate, source = _rate(transition, state, current, old_rate)
        flow = math.ceil(downstream_flow / rate * (1 + transition["buffer"]))
        stock = math.ceil(flow * transition["lead_days"] / 7 * (1 + transition["stock_buffer"]))
        proposed[transition["from"]] = {"flow_per_week": flow, "stock_min": stock, "rate_used": rate, "rate_source": source}
        downstream_flow = flow

    changes = []
    if previous:
        for stage, row in proposed.items():
            old_row = previous.get(stage)
            if not isinstance(old_row, dict):
                continue
            for field in ("flow_per_week", "stock_min"):
                old, wanted = old_row.get(field), row[field]
                if isinstance(old, int) and old != wanted:
                    row[field] = _cap(old, wanted)
                    if row[field] != old:
                        changes.append({"stage": stage, "field": field, "old": old, "new": row[field]})
    result = {"status": "ok", "reason": "floors compiled", "floors": proposed, "changes": changes, "computed_at": computed_at}
    dept.mkdir(parents=True, exist_ok=True)
    floors_path.write_text(HEADER + yaml.safe_dump(result, sort_keys=False), encoding="utf-8")
    _persist(state, result)
    return result
