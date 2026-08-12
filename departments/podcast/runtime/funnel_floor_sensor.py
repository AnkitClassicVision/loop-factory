"""Measure the podcast funnel floors from deterministic local evidence."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import runrecord
from factory.charter_loader import load_charter


DEFAULT_PIPELINE_REPO = Path("/mnt/d_drive/repos/podcast")
DEFAULT_CHARTER_PATH = REPO_ROOT / "departments" / "podcast" / "charter.yaml"
FLOORS = (
    "active_warm_threads",
    "live_replies",
    "prep_done_awaiting_recording",
    "recordings_booked_future",
    "stale_touches",
    "expired_holds_unactioned",
)
FINAL_STAGES = frozenset({"published", "closed", "rejected"})
LIVE_REPLY_STAGES = frozenset({"responded", "in-conversation"})
PREP_DONE_STAGES = frozenset({"prep-call-done", "prep-done", "prep-done-awaiting-recording"})
LOGGER = logging.getLogger(__name__)


def _normalise(value: Any) -> str:
    return str(value or "").strip().casefold().replace("_", "-")


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any) -> date | None:
    parsed = _parse_datetime(value)
    if parsed is not None:
        return parsed.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _last_touch(person: Mapping[str, Any]) -> tuple[datetime | None, str]:
    touch = person.get("last_touch")
    if not isinstance(touch, Mapping):
        return None, ""
    return _parse_datetime(touch.get("at")), _normalise(touch.get("direction"))


def _recording_date(episode: Mapping[str, Any]) -> date | None:
    for key in ("recording_date", "recording_at", "scheduled_recording_at", "publish_date"):
        value = episode.get(key)
        if isinstance(value, Mapping):
            value = value.get("at") or value.get("date")
        parsed = _parse_date(value)
        if parsed is not None:
            return parsed
    recording = episode.get("recording")
    if isinstance(recording, Mapping):
        for key in ("at", "date", "scheduled_at"):
            parsed = _parse_date(recording.get(key))
            if parsed is not None:
                return parsed
    return None


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    ledger = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(ledger, dict) or ledger.get("schema") != "funnel-ledger/v1":
        raise ValueError("expected schema funnel-ledger/v1")
    people = ledger.get("people")
    if not isinstance(people, list) or not all(isinstance(row, dict) for row in people):
        raise ValueError("people must be a list of objects")
    return people


def _future_recordings(pipeline_repo: Path, today: date) -> tuple[int | None, str]:
    episodes_dir = pipeline_repo / "episodes"
    try:
        paths = sorted(episodes_dir.glob("*/episode.json"))
    except OSError as exc:
        return None, f"unreadable recordings source: {episodes_dir}: {exc}"
    booked = 0
    dated = 0
    for path in paths:
        try:
            episode = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(episode, dict):
                raise ValueError("episode.json must contain an object")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return None, f"unreadable recordings source: {path}: {exc}"
        if _normalise(episode.get("stage")) != "recording-booked":
            continue
        scheduled = _recording_date(episode)
        if scheduled is None:
            continue
        dated += 1
        if scheduled >= today:
            booked += 1
    if dated == 0:
        return None, "no recording-booked episode has usable date evidence"
    return booked, f"read {dated} dated recording-booked episode records"


def _measure(
    people: list[dict[str, Any]], pipeline_repo: Path, now: datetime
) -> tuple[dict[str, int | None], dict[str, str]]:
    today = now.date()
    warm_cutoff = now - timedelta(days=7)
    reply_cutoff = now - timedelta(days=14)
    active = [
        person
        for person in people
        if person.get("stage") is not None and _normalise(person.get("stage")) not in FINAL_STAGES
    ]
    values: dict[str, int | None] = {
        "active_warm_threads": sum(
            (touched := _last_touch(person)[0]) is not None and touched >= warm_cutoff
            for person in active
        ),
        "live_replies": sum(
            _normalise(person.get("stage")) in LIVE_REPLY_STAGES
            or (
                (touch := _last_touch(person))[1] == "inbound"
                and touch[0] is not None
                and touch[0] >= reply_cutoff
            )
            for person in active
        ),
        "prep_done_awaiting_recording": sum(
            _normalise(person.get("stage")) in PREP_DONE_STAGES for person in people
        ),
        "stale_touches": sum(
            (touched := _last_touch(person)[0]) is None or touched < warm_cutoff
            for person in active
        ),
        "expired_holds_unactioned": sum(
            (expiry := _parse_date((person.get("hold") or {}).get("next_action_on"))) is not None
            and expiry < today
            and ((touched := _last_touch(person)[0]) is None or touched.date() < expiry)
            for person in people
            if isinstance(person.get("hold"), Mapping)
        ),
    }
    future, future_detail = _future_recordings(pipeline_repo, today)
    values["recordings_booked_future"] = future
    return values, {"recordings_booked_future": future_detail}


def _thresholds(charter: dict[str, Any]) -> tuple[dict[str, int], int, int, int]:
    objectives = ((charter.get("setpoints") or {}).get("objectives") or {})
    floors: dict[str, int] = {}
    for name in FLOORS:
        row = objectives.get(name)
        if not isinstance(row, dict) or not isinstance(row.get("setpoint"), int):
            raise ValueError(f"missing integer setpoint for {name}")
        floors[name] = row["setpoint"]
    drive = (charter.get("thresholds") or {}).get("funnel_drive") or {}
    steady = drive.get("new_outreach_per_week_steady")
    rebuild = drive.get("new_outreach_per_week_rebuild")
    hopper = objectives.get("hopper_depth") or {}
    rebuild_below = hopper.get("target")
    if not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (steady, rebuild, rebuild_below)
    ):
        raise ValueError("funnel drive quotas and hopper target must be integers")
    return floors, steady, rebuild, rebuild_below


def _existing_hopper_depth(state_dir: Path) -> int | float | None:
    try:
        observed = json.loads((state_dir / "objectives_observed.json").read_text(encoding="utf-8"))
        value = observed["values"].get("hopper_depth")
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _atomic_write_objectives(state_dir: Path, ts: str, measured: dict[str, int]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "objectives_observed.json"
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema") != "objectives-observed/v1" or not isinstance(document.get("values"), dict):
            raise ValueError("existing objectives_observed.json has an invalid schema")
        values = {key: value for key, value in document["values"].items() if key not in FLOORS}
    else:
        values = {}
    values.update(measured)
    output = {"schema": "objectives-observed/v1", "ts": ts, "values": values}
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=state_dir, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(output, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _append(state_dir: Path, observation: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, sort_keys=True) + "\n")


def _status(name: str, count: int, floor: int) -> str:
    if name in {"stale_touches", "expired_holds_unactioned"}:
        return "ok" if count <= floor else "alarm"
    return "ok" if count >= floor else "alarm"


def run(
    state_dir: Path,
    sources: Path,
    pipeline_repo: Path = DEFAULT_PIPELINE_REPO,
    *,
    now: datetime | None = None,
    charter_path: Path = DEFAULT_CHARTER_PATH,
) -> list[dict[str, Any]]:
    del sources
    state_dir = Path(state_dir)
    pipeline_repo = Path(pipeline_repo)
    started = time.perf_counter()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    ts = current.isoformat()
    ledger_path = pipeline_repo / "episodes" / "FUNNEL-LEDGER.json"
    values: dict[str, int | None] = {name: None for name in FLOORS}
    details: dict[str, str] = {}
    floor_lines: dict[str, int] = {}
    steady = rebuild = rebuild_below = None
    try:
        people = _read_ledger(ledger_path)
        values, details = _measure(people, pipeline_repo, current)
        floor_lines, steady, rebuild, rebuild_below = _thresholds(
            load_charter(charter_path, expect_department="podcast")
        )
        ledger_error = None
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        ledger_error = f"unreadable source: {ledger_path}: {exc}"
        people = []

    measured = {name: value for name, value in values.items() if value is not None}
    try:
        _atomic_write_objectives(state_dir, ts, measured)
    except Exception as exc:
        LOGGER.exception("funnel sensor could not update objectives")
        ledger_error = f"objectives update failed: {type(exc).__name__}"
        values = {name: None for name in FLOORS}

    observations: list[dict[str, Any]] = []
    for name in FLOORS:
        value = values[name]
        floor = floor_lines.get(name)
        detail = ledger_error or details.get(name, f"read {ledger_path}")
        observation = {
            "ts": ts,
            "sensor": "funnel",
            "subject": name,
            "status": "unknown" if value is None or floor is None else _status(name, value, floor),
            "evidence": str(ledger_path),
            "detail": detail,
            "metrics": {"count": value, "floor": floor},
        }
        _append(state_dir, observation)
        observations.append(observation)

    hopper_depth = _existing_hopper_depth(state_dir)
    quota = (
        rebuild
        if hopper_depth is not None
        and rebuild_below is not None
        and hopper_depth < rebuild_below
        else steady
    )
    monday = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    outbound = sum(
        (touch := _last_touch(person))[1] == "outbound"
        and touch[0] is not None
        and touch[0] >= monday
        for person in people
    )
    quota_unknown = ledger_error is not None or quota is None or hopper_depth is None
    quota_observation = {
        "ts": ts,
        "sensor": "funnel",
        "subject": "new_outreach_this_week",
        "status": "unknown" if quota_unknown else ("ok" if outbound >= quota else "alarm"),
        "evidence": str(ledger_path),
        "detail": ledger_error or f"hopper_depth={hopper_depth}; week starts {monday.isoformat()}",
        "metrics": {"count": None if ledger_error else outbound, "floor": quota},
    }
    _append(state_dir, quota_observation)
    observations.append(quota_observation)

    errors = [f"{row['subject']}:{row['status']}" for row in observations if row["status"] != "ok"]
    try:
        runrecord.emit_record(
            state_dir,
            department="podcast",
            node="funnel_floor_sensor",
            status="error" if errors else "ok",
            release=runrecord.read_release(state_dir.parent),
            trigger={"kind": "time", "id": "podcast-daily", "dedupe_key": f"{current.date().isoformat()}-funnel_floor_sensor"},
            duration_ms=int((time.perf_counter() - started) * 1000),
            errors=errors,
            artifacts=[str(path) for path in (state_dir / "objectives_observed.json", state_dir / "observations.jsonl") if path.exists()],
            external_actions_taken=0,
        )
    except Exception:
        LOGGER.exception("funnel_floor_sensor failed to append its runs-v2 record")
    return observations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=REPO_ROOT / "departments/podcast/state")
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--pipeline-repo", type=Path, default=DEFAULT_PIPELINE_REPO)
    parser.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    try:
        run(args.state_dir, args.sources, args.pipeline_repo)
    except Exception:
        LOGGER.exception("funnel_floor_sensor refused to crash the daily chain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
