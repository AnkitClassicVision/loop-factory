"""Observe owner objectives from deterministic, read-only pipeline evidence.

``hopper_depth`` is intentionally a weaker proxy than "every recording that has
not been published."  The pipeline has old, unscheduled and overdue episode
directories whose local stages do not prove that they remain release inventory.
Counting those directories made the known empty hopper look full.  This sensor
therefore counts ``future_scheduled_recordings_with_media_not_published``:

* ``episodes/<id>/episode.json`` has a publish date on or after the UTC reading
  date;
* its stage timestamps contain ``recording-done``;
* a non-empty audio/video asset exists under ``raw/`` or ``final/``; and
* neither the stage history nor provider/readback data proves publication.

This is deterministic and makes a real empty result distinguishable from a
missing source.  Its limit is deliberate: an unscheduled recording, or a
scheduled row without both the stage receipt and media, is not counted.  If the
episodes source is absent or any episode record is unreadable/ambiguous, the
sensor emits no ``hopper_depth`` value rather than inventing zero.

``publish_reliability`` reuses same-UTC-day ``publish_verifier`` observations
for every episode due in ``publish_schedule.json``.  It is omitted when there
are no episodes due or complete verifier evidence is unavailable; historical
on-time reliability cannot be reconstructed from current public-state evidence.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import runrecord


DEFAULT_PIPELINE_REPO = Path("/mnt/d_drive/repos/podcast")
OBJECTIVES_EVIDENCE_CONFIG = Path(__file__).with_name("estate.json")
MEDIA_SUFFIXES = frozenset(
    {".aac", ".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4", ".wav", ".webm"}
)
PUBLISHED_STAGES = frozenset(
    {
        "published",
        "promo-scheduled",
        "promo-kit-sent",
        "promo-sent",
        "followup-sent",
        "complete",
    }
)
OWNED_VALUES = frozenset(
    {
        "hopper_depth",
        "publish_reliability",
        "hopper_interviews_ready",
        "state_drift",
        "unledgered_inbound",
    }
)
LOGGER = logging.getLogger(__name__)


def _append(state_dir: Path, observation: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, sort_keys=True) + "\n")


def _normalise(value: Any) -> str:
    return str(value or "").strip().casefold().replace("_", "-")


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None


def _mapping_proves_published(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("published") is True or value.get("verified_public") is True:
        return True
    if _normalise(value.get("status")) == "published":
        return True
    if (
        _normalise(value.get("privacy_status")) == "public"
        and (value.get("observed_at") or value.get("verified_at"))
    ):
        return True
    return any(
        _mapping_proves_published(item)
        for item in value.values()
        if isinstance(item, Mapping)
    )


def _proves_published(episode: dict[str, Any]) -> bool:
    timestamps = episode.get("stage_timestamps")
    if isinstance(timestamps, Mapping) and timestamps.get("published"):
        return True
    if _normalise(episode.get("stage")) in PUBLISHED_STAGES:
        return True
    return any(
        _mapping_proves_published(episode.get(key))
        for key in ("publish_status", "publish_verification", "publish_receipt")
    )


def _has_recording_receipt(episode: dict[str, Any]) -> bool:
    timestamps = episode.get("stage_timestamps")
    return bool(isinstance(timestamps, Mapping) and timestamps.get("recording-done"))


def _has_media(episode_dir: Path) -> bool:
    for root_name in ("raw", "final"):
        root = episode_dir / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                if (
                    path.is_file()
                    and path.suffix.casefold() in MEDIA_SUFFIXES
                    and path.stat().st_size > 0
                ):
                    return True
            except OSError:
                return False
    return False


def _hopper_depth(
    pipeline_repo: Path, today: date
) -> tuple[int | None, str, dict[str, Any]]:
    episodes_dir = pipeline_repo / "episodes"
    if not episodes_dir.is_dir():
        return None, f"missing source: {episodes_dir}", {}

    episode_paths = sorted(
        path / "episode.json"
        for path in episodes_dir.iterdir()
        if path.is_dir() and (path / "episode.json").is_file()
    )
    counted: list[str] = []
    scheduled_candidates = 0
    for episode_path in episode_paths:
        try:
            episode = json.loads(episode_path.read_text(encoding="utf-8"))
            if not isinstance(episode, dict):
                raise ValueError("episode.json must contain an object")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            return None, f"unreadable source: {episode_path}: {exc}", {}

        raw_publish_date = episode.get("publish_date")
        if raw_publish_date in (None, ""):
            continue
        publish_date = _parse_date(raw_publish_date)
        if publish_date is None:
            return None, f"unreadable publish_date: {episode_path}", {}
        if publish_date < today or _proves_published(episode):
            continue
        scheduled_candidates += 1
        if _has_recording_receipt(episode) and _has_media(episode_path.parent):
            counted.append(episode_path.parent.name)

    metrics = {
        "proxy": "future_scheduled_recordings_with_media_not_published",
        "scheduled_candidates": scheduled_candidates,
        "counted_episode_ids": counted,
    }
    return len(counted), f"scanned {episodes_dir}", metrics


def _observation_date(row: dict[str, Any]) -> date | None:
    value = row.get("ts")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date()


def _publish_reliability(
    state_dir: Path, sources: Path, today: date
) -> tuple[float | None, str, dict[str, Any], bool]:
    schedule_path = sources / "publish_schedule.json"
    if not schedule_path.is_file():
        return None, f"missing source: {schedule_path}", {}, True
    try:
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        rows = schedule["episodes"]
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise TypeError("episodes must be a list of objects")
        due_ids = [
            str(row.get("episode_id") or "").strip()
            for row in rows
            if row.get("publish_date") == today.isoformat()
        ]
        if any(not episode_id for episode_id in due_ids):
            raise ValueError("due episode is missing episode_id")
        if len(set(due_ids)) != len(due_ids):
            raise ValueError("due episode_id values must be unique")
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return None, f"unreadable source: {schedule_path}: {exc}", {}, True
    if not due_ids:
        return None, "no publish due today", {"due_episodes": 0}, False

    observations_path = state_dir / "observations.jsonl"
    if not observations_path.is_file():
        return None, f"missing verifier evidence: {observations_path}", {}, True
    try:
        observation_rows = [
            json.loads(line)
            for line in observations_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not all(isinstance(row, dict) for row in observation_rows):
            raise TypeError("observation rows must be objects")
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return None, f"unreadable verifier evidence: {exc}", {}, True

    latest: dict[str, dict[str, Any]] = {}
    for row in observation_rows:
        subject = str(row.get("subject") or "")
        if (
            row.get("sensor") == "publishday"
            and subject in due_ids
            and _observation_date(row) == today
        ):
            current = latest.get(subject)
            if current is None or str(row.get("ts", "")) >= str(current.get("ts", "")):
                latest[subject] = row
    missing = sorted(set(due_ids) - set(latest))
    if missing:
        return (
            None,
            "missing same-day publish_verifier evidence: " + ", ".join(missing),
            {"due_episodes": len(due_ids), "missing_episode_ids": missing},
            True,
        )
    verified = sum(row.get("status") == "ok" for row in latest.values())
    value = round(100 * verified / len(due_ids), 2)
    return (
        value,
        f"{verified} of {len(due_ids)} due episodes verified live",
        {"due_episodes": len(due_ids), "verified_live": verified},
        False,
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("top-level JSON value must be an object")
    return value


def _configured_evidence_paths(config_path: Path) -> tuple[dict[str, str], str | None]:
    try:
        config = _read_json_object(config_path)
        evidence = config["objectives_evidence"]
        if not isinstance(evidence, dict):
            raise TypeError("objectives_evidence must be an object")
        paths: dict[str, str] = {}
        for objective in (
            "hopper_interviews_ready",
            "state_drift",
            "unledgered_inbound",
        ):
            value = evidence.get(objective)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"objectives_evidence.{objective} must be a path")
            if not Path(value).is_absolute():
                raise ValueError(f"objectives_evidence.{objective} must be absolute")
            paths[objective] = value
        return paths, None
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {}, f"unreadable objectives evidence config: {config_path}: {exc}"


def _resolve_evidence(pattern: str) -> tuple[Path | None, str]:
    matches = sorted(Path(item) for item in glob.glob(pattern) if Path(item).is_file())
    if not matches:
        return None, f"missing evidence artifact: {pattern}"
    return matches[-1], f"read {matches[-1]}"


def _hopper_interviews_ready(path: Path) -> int:
    ledger = _read_json_object(path)
    if ledger.get("schema") != "funnel-ledger/v1":
        raise ValueError("expected schema funnel-ledger/v1")
    people = ledger.get("people")
    if not isinstance(people, list) or not all(isinstance(row, dict) for row in people):
        raise ValueError("people must be a list of objects")
    return sum(
        row.get("stage") == "recorded" and row.get("kind") != "solo"
        for row in people
    )


def _reconcile_count(path: Path, field: str) -> int:
    receipt = _read_json_object(path)
    if receipt.get("subcommand") not in {"rebuild", "drift"}:
        raise ValueError("expected a funnel ledger reconcile receipt")
    counts = receipt.get("counts")
    if not isinstance(counts, dict):
        raise ValueError("counts must be an object")
    value = counts.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"counts.{field} must be a non-negative integer")
    return value


def _additional_objectives(config_path: Path) -> tuple[dict[str, int], dict[str, str]]:
    configured, config_error = _configured_evidence_paths(config_path)
    if config_error:
        return {}, {
            objective: config_error
            for objective in (
                "hopper_interviews_ready",
                "state_drift",
                "unledgered_inbound",
            )
        }

    readers = {
        "hopper_interviews_ready": lambda path: _hopper_interviews_ready(path),
        "state_drift": lambda path: _reconcile_count(path, "drift"),
        "unledgered_inbound": lambda path: _reconcile_count(path, "unledgered_added"),
    }
    values: dict[str, int] = {}
    details: dict[str, str] = {}
    for objective, reader in readers.items():
        path, resolution = _resolve_evidence(configured[objective])
        if path is None:
            details[objective] = resolution
            continue
        try:
            values[objective] = reader(path)
            details[objective] = resolution
        except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            details[objective] = f"unreadable evidence artifact: {path}: {exc}"
    return values, details


def _read_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "objectives-observed/v1", "values": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "objectives-observed/v1":
        raise ValueError("existing objectives_observed.json has an invalid schema")
    if not isinstance(value.get("values"), dict):
        raise ValueError("existing objectives_observed.json values must be an object")
    return value


def _atomic_write_objectives(
    state_dir: Path, *, ts: str, owned_values: dict[str, int | float]
) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "objectives_observed.json"
    existing = _read_existing(path)
    values = {
        key: value
        for key, value in existing["values"].items()
        if key not in OWNED_VALUES
    }
    values.update(owned_values)
    document = {"schema": "objectives-observed/v1", "ts": ts, "values": values}

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=state_dir,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(document, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        directory_fd = os.open(state_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
    return path


def _emit_run_record(
    state_dir: Path, *, started: float, status: str, errors: list[str]
) -> None:
    artifacts = [
        state_dir / "objectives_observed.json",
        state_dir / "observations.jsonl",
    ]
    try:
        runrecord.emit_record(
            state_dir,
            department="podcast",
            node="hopper_sensor",
            status=status,
            release=runrecord.read_release(state_dir.parent),
            trigger={
                "kind": "time",
                "id": "podcast-daily",
                "dedupe_key": (
                    f"{datetime.now(timezone.utc).date().isoformat()}-hopper_sensor"
                ),
            },
            duration_ms=int((time.perf_counter() - started) * 1000),
            errors=errors,
            artifacts=[str(path) for path in artifacts if path.exists()],
            external_actions_taken=0,
        )
    except Exception:
        LOGGER.exception("hopper_sensor failed to append its runs-v2 record")
        raise


def _run(
    state_dir: Path,
    sources: Path,
    pipeline_repo: Path,
    today: date,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    hopper, hopper_detail, hopper_metrics = _hopper_depth(pipeline_repo, today)
    reliability, reliability_detail, reliability_metrics, reliability_unknown = (
        _publish_reliability(state_dir, sources, today)
    )
    evidence_config = (
        OBJECTIVES_EVIDENCE_CONFIG
        if pipeline_repo == DEFAULT_PIPELINE_REPO
        else pipeline_repo / "estate.json"
    )
    additional_values, additional_details = _additional_objectives(evidence_config)
    values: dict[str, int | float] = {}
    if hopper is not None:
        values["hopper_depth"] = hopper
    if reliability is not None:
        values["publish_reliability"] = reliability
    values.update(additional_values)

    _atomic_write_objectives(state_dir, ts=now, owned_values=values)
    status = "unknown" if hopper is None or reliability_unknown else "ok"
    observation = {
        "ts": now,
        "sensor": "hopper",
        "subject": "owner-objectives",
        "status": status,
        "evidence": f"{pipeline_repo / 'episodes'},{sources / 'publish_schedule.json'}",
        "detail": (
            f"hopper: {hopper_detail}; publish reliability: {reliability_detail}; "
            + "; ".join(
                f"{objective}: {detail}"
                for objective, detail in additional_details.items()
            )
        ),
        "metrics": {
            "values": values,
            "hopper": hopper_metrics,
            "publish_reliability": reliability_metrics,
            "objectives_evidence": additional_details,
        },
    }
    _append(state_dir, observation)
    return observation


def run(
    state_dir: Path,
    sources: Path,
    pipeline_repo: Path = DEFAULT_PIPELINE_REPO,
    today: date | None = None,
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    started = time.perf_counter()
    try:
        observation = _run(
            state_dir,
            Path(sources),
            Path(pipeline_repo),
            today or datetime.now(timezone.utc).date(),
        )
    except Exception as exc:
        _emit_run_record(
            state_dir,
            started=started,
            status="error",
            errors=[type(exc).__name__],
        )
        raise
    errors = [] if observation["status"] == "ok" else ["hopper:unknown"]
    _emit_run_record(
        state_dir,
        started=started,
        status="error" if errors else "ok",
        errors=errors,
    )
    return observation


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state-dir", type=Path, default=repo / "departments/podcast/state"
    )
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--pipeline-repo", type=Path, default=DEFAULT_PIPELINE_REPO)
    parser.add_argument(
        "--shadow", action=argparse.BooleanOptionalAction, default=True
    )
    args = parser.parse_args(argv)
    run(args.state_dir, args.sources, args.pipeline_repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
