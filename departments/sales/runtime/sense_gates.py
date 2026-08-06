"""Observe the sales department's four context-is-king gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from factory import runrecord
from factory.charter_loader import load_charter


LOGGER = logging.getLogger(__name__)
NODE = "sense_gates"


def _utc(value: datetime | str | None) -> datetime:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_events(state_dir: Path) -> list[dict[str, Any]]:
    path = state_dir / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        _utc(row["ts"])
        rows.append(row)
    return rows


def _read_threads(state_dir: Path) -> list[dict[str, Any]]:
    document = json.loads((state_dir / "sources" / "threads.json").read_text(encoding="utf-8"))
    threads = document.get("threads")
    if not isinstance(threads, list) or not all(isinstance(row, dict) for row in threads):
        raise ValueError("threads.json threads must be a list of objects")
    return threads


def _subject_id(salt: str, email: str) -> str:
    return hashlib.sha256((salt + email.strip().lower()).encode()).hexdigest()[:16]


def _staleness(state_dir: Path, events: list[dict], current: datetime) -> dict:
    salt = (state_dir / "sources" / ".id_salt").read_text(encoding="utf-8").strip()
    if not salt:
        raise ValueError("identity salt is empty")
    latest: dict[str, dict] = {}
    for row in events:
        subject = row["subject_id"]
        if subject not in latest or _utc(row["ts"]) > _utc(latest[subject]["ts"]):
            latest[subject] = row
    live = {subject for subject, row in latest.items() if row.get("to_stage") == "conversation_live"}
    recent: set[str] = set()
    cutoff = current - timedelta(days=7)
    for thread in _read_threads(state_dir):
        if thread.get("kind") not in {"reply", "live"} or not isinstance(thread.get("email"), str):
            continue
        try:
            timestamp = _utc(thread.get("last_two_way_ts"))
        except (TypeError, ValueError):
            continue
        if timestamp >= cutoff:
            recent.add(_subject_id(salt, thread["email"]))
    stale = sorted(live - recent)
    return {
        "subject": "conversation_staleness",
        "status": "alarm" if stale else "ok",
        "detail": f"{len(stale)} conversation_live subjects lack two-way evidence within 7 days",
        "metrics": {"count": len(stale), "subject_ids": stale},
    }


def _double_touch(events: list[dict], cadence_days: int) -> dict:
    received: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for row in events:
        source = (row.get("meta") or {}).get("source")
        if row.get("to_stage") == "received" and isinstance(source, str) and source:
            received[row["subject_id"]].append((_utc(row["ts"]), source))
    collisions = []
    window = timedelta(days=cadence_days)
    for subject, touches in received.items():
        colliding_lanes: set[str] = set()
        for index, (left_ts, left_lane) in enumerate(touches):
            for right_ts, right_lane in touches[index + 1 :]:
                if left_lane != right_lane and abs(right_ts - left_ts) < window:
                    colliding_lanes.update((left_lane, right_lane))
        if colliding_lanes:
            collisions.append({"subject_id": subject, "lanes": sorted(colliding_lanes)})
    collisions.sort(key=lambda row: (row["subject_id"], row["lanes"]))
    return {
        "subject": "cross_lane_double_touch",
        "status": "alarm" if collisions else "ok",
        "detail": f"{len(collisions)} subjects received from multiple lanes inside the {cadence_days}-day cadence floor",
        "metrics": {"count": len(collisions), "collision_pairs": collisions},
    }


def _coverage() -> dict:
    return {
        "subject": "context_voice_coverage",
        "status": "ok",
        "detail": "vacuous truth: drafts do not exist in v1",
        "metrics": {"drafts": 0, "checked": 0},
    }


def _floors(dept_dir: Path, events: list[dict], current: datetime) -> dict:
    path = dept_dir / "floors.yaml"
    if not path.exists():
        return {
            "subject": "floors_attainment",
            "status": "unknown",
            "detail": "floors.yaml is absent; attainment is unmeasured",
            "metrics": {"shortfalls": {}},
        }
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    floors = document.get("floors") if isinstance(document, dict) else None
    if not isinstance(floors, dict):
        raise ValueError("floors.yaml floors must be a mapping")
    monday = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = monday + timedelta(days=7)
    counts: dict[str, int] = defaultdict(int)
    for row in events:
        timestamp = _utc(row["ts"])
        if monday <= timestamp < week_end:
            counts[row["to_stage"]] += 1
    shortfalls = {}
    for stage, config in floors.items():
        required = config.get("flow_per_week") if isinstance(config, dict) else None
        if isinstance(required, bool) or not isinstance(required, int) or required < 0:
            raise ValueError(f"floors.{stage}.flow_per_week must be a non-negative integer")
        actual = counts.get(stage, 0)
        if actual < required:
            shortfalls[stage] = {"actual": actual, "required": required, "shortfall": required - actual}
    shortfalls = dict(sorted(shortfalls.items()))
    return {
        "subject": "floors_attainment",
        "status": "alarm" if shortfalls else "ok",
        "detail": f"{len(shortfalls)} stage floors are short for ISO week {current.isocalendar().year}-W{current.isocalendar().week:02d}",
        "metrics": {"shortfalls": shortfalls},
    }


def _append(state_dir: Path, observations: list[dict], current: datetime) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "observations.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for finding in observations:
            row = {"ts": current.isoformat(), "sensor": "salesgate", **finding}
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def run(state_dir: Path, dept_dir: Path, *, now: datetime | None = None) -> list[dict]:
    state_dir, dept_dir = Path(state_dir), Path(dept_dir)
    current = _utc(now)
    started = time.perf_counter()
    try:
        events = _read_events(state_dir)
        charter = load_charter(dept_dir / "charter.yaml", expect_department="sales")
        cadence = charter["budget"]["estate_send_caps"]["per_contact_cadence_floor_days"]
        if isinstance(cadence, bool) or not isinstance(cadence, int) or cadence < 0:
            raise ValueError("per_contact_cadence_floor_days must be a non-negative integer")
        findings = [
            _staleness(state_dir, events, current),
            _double_touch(events, cadence),
            _coverage(),
            _floors(dept_dir, events, current),
        ]
        observation_path = _append(state_dir, findings, current)
        runrecord.emit_record(
            state_dir,
            department="sales",
            node=NODE,
            status="ok",
            release=runrecord.read_release(dept_dir),
            trigger={"kind": "time", "id": "sales-daily", "dedupe_key": f"{current.date().isoformat()}-{NODE}"},
            cost={"lane": "flat_subscription", "model_calls": 0},
            duration_ms=int((time.perf_counter() - started) * 1000),
            artifacts=[str(observation_path)],
            external_actions_taken=0,
        )
        return [{"ts": current.isoformat(), "sensor": "salesgate", **row} for row in findings]
    except Exception as exc:
        runrecord.emit_record(
            state_dir,
            department="sales",
            node=NODE,
            status="error",
            release=runrecord.read_release(dept_dir),
            trigger={"kind": "time", "id": "sales-daily", "dedupe_key": f"{current.date().isoformat()}-{NODE}"},
            cost={"lane": "flat_subscription", "model_calls": 0},
            duration_ms=int((time.perf_counter() - started) * 1000),
            errors=[type(exc).__name__],
            external_actions_taken=0,
        )
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--dept-dir", type=Path, required=True)
    parser.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    try:
        run(args.state_dir, args.dept_dir)
    except Exception:
        LOGGER.exception("sales sense gates crashed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
