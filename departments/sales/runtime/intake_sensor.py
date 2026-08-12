"""Ingest PII-bearing sales lane sources into the opaque events ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from factory import runrecord
from factory.events_ledger import LedgerError, append_event


LANES = ("icaregrow", "podcast_handoffs", "pfs_warm", "website_forms", "luma")
LOGGER = logging.getLogger(__name__)


def _subject_id(salt: str, email: object) -> str:
    normalized = str(email).strip().lower()
    if not normalized:
        raise ValueError("source row email must be non-empty")
    return hashlib.sha256((salt + normalized).encode()).hexdigest()[:16]


def _salt(sources: Path) -> str:
    value = (sources / ".id_salt").read_text(encoding="utf-8")
    if not value:
        raise ValueError("id salt must not be empty")
    return value


def _append_observation(state_dir: Path, observation: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, sort_keys=True) + "\n")


def _received_subjects(state_dir: Path) -> set[str]:
    path = state_dir / "events.jsonl"
    found: set[str] = set()
    if not path.exists():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("to_stage") == "received":
                found.add(row["subject_id"])
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return found


def _work(state_dir: Path) -> list[dict]:
    sources = state_dir / "sources"
    salt = _salt(sources)
    missing: list[str] = []
    unparseable: list[str] = []
    candidates: list[tuple[str, dict, str, str]] = []
    for lane in LANES:
        path = sources / f"{lane}.json"
        if not path.is_file():
            missing.append(lane)
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            rows = document["rows"]
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise TypeError("rows must be a list of objects")
            for row in rows:
                subject_id = _subject_id(salt, row.get("email"))
                parsed = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                candidates.append((lane, row, subject_id, parsed.astimezone(timezone.utc).isoformat()))
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            unparseable.append(lane)

    existing = _received_subjects(state_dir)
    winners: dict[tuple[str, str], str] = {}
    priority_wins: Counter[str] = Counter()
    cross_lane_duplicates = 0
    appended = 0
    for lane, _row, subject_id, ts in candidates:
        day_key = (subject_id, datetime.fromisoformat(ts).date().isoformat())
        if day_key in winners:
            cross_lane_duplicates += 1
            priority_wins[winners[day_key]] += 1
            continue
        winners[day_key] = lane
        if subject_id in existing:
            continue
        event_time = datetime.fromisoformat(ts)
        cohort = f"{event_time.isocalendar().year}-W{event_time.isocalendar().week:02d}"
        try:
            append_event(
                state_dir,
                subject_id=subject_id,
                from_stage="arrival",
                to_stage="received",
                ts=ts,
                meta={"source": lane, "cohort": cohort},
            )
        except LedgerError as exc:
            if "duplicate transition" not in str(exc):
                raise
        else:
            existing.add(subject_id)
            appended += 1

    metrics = {
        "appended": appended,
        "cross_lane_duplicates": cross_lane_duplicates,
        "missing_lanes": missing,
        "priority_lane_wins": dict(priority_wins),
        "unparseable_lanes": unparseable,
    }
    detail_parts = [f"appended {appended} new arrivals"]
    if missing:
        detail_parts.append(f"missing lane files: {len(missing)}")
    if unparseable:
        detail_parts.append(f"unparseable lane files: {len(unparseable)}")
    observation = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "sensor": "intake",
        "subject": "sales-intake",
        "status": "alarm" if unparseable else "ok",
        "evidence": "source-lane-counts",
        "detail": "; ".join(detail_parts),
        "metrics": metrics,
    }
    _append_observation(state_dir, observation)
    return [observation]


def _emit(state_dir: Path, started: float, status: str, errors: list[str]) -> None:
    runrecord.emit_record(
        state_dir,
        department="sales",
        node="intake_sensor",
        status=status,
        release=runrecord.read_release(state_dir.parent),
        trigger={"kind": "time", "id": "intake_sensor", "dedupe_key": f"{datetime.now(timezone.utc).date()}-intake_sensor"},
        duration_ms=int((time.perf_counter() - started) * 1000),
        errors=errors,
        artifacts=[str(path) for path in (state_dir / "events.jsonl", state_dir / "observations.jsonl") if path.exists()],
        external_actions_taken=0,
    )


def run(state_dir: Path) -> list[dict]:
    state_dir = Path(state_dir)
    started = time.perf_counter()
    try:
        observations = _work(state_dir)
    except Exception as exc:
        _emit(state_dir, started, "error", [type(exc).__name__])
        raise
    _emit(state_dir, started, "ok", [])
    return observations


def main() -> int:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=repo / "departments/sales/state")
    parser.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    run(args.state_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
