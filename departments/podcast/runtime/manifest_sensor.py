"""Observe guest manifest completeness from provider-fetched artifacts."""
from __future__ import annotations

import argparse
import json
import logging
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from factory import runrecord


REQUIRED = ("headshot", "links", "bio", "promo_assets")
LOGGER = logging.getLogger(__name__)


def _append(state_dir: Path, obs: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obs, sort_keys=True) + "\n")


def _obs(subject: str, status: str, evidence: str, detail: str, metrics: dict) -> dict:
    return {"ts": datetime.now(timezone.utc).isoformat(), "sensor": "manifest",
            "subject": subject, "status": status, "evidence": evidence,
            "detail": detail, "metrics": metrics}


def _run(state_dir: Path, sources: Path, today: date | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date()
    path = sources / "guest_manifests.json"
    if not path.is_file():
        obs = _obs("guest-manifests", "unknown", str(path), f"missing source: {path}", {})
        _append(state_dir, obs)
        return [obs]
    try:
        guests = json.loads(path.read_text(encoding="utf-8"))["guests"]
        if not isinstance(guests, list):
            raise TypeError("guests must be a list")
        if not all(isinstance(guest, dict) for guest in guests):
            raise TypeError("every guest must be an object")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        obs = _obs("guest-manifests", "unknown", str(path), f"unreadable source: {exc}", {})
        _append(state_dir, obs)
        return [obs]
    if not guests:
        obs = _obs(
            "manifest-coverage",
            "ok",
            str(path),
            "zero active guests in source",
            {"active_guests": 0},
        )
        _append(state_dir, obs)
        return [obs]
    emails = Counter(str(row.get("email") or "").strip().casefold()
                     for row in guests if isinstance(row, dict))
    observations = []
    for guest in guests:
        email = str(guest.get("email") or "").strip().casefold()
        if not email or emails[email] > 1:
            observations.append(_obs(email or str(guest.get("guest") or "unknown"), "unknown",
                                     str(path), "suppress + review", {}))
            continue
        fields = guest.get("fields") or {}
        present = [field for field in REQUIRED if bool((fields.get(field) or {}).get("present"))]
        missing = [field for field in REQUIRED if field not in present]
        fallback = {field: bool((fields.get(field) or {}).get("fallback_available", False))
                    for field in missing}
        metrics = {"required": list(REQUIRED), "present": present, "missing": missing,
                   "fallback_available": fallback,
                   "completeness_pct": round(100 * len(present) / len(REQUIRED), 2)}
        status = "ok"
        detail = "manifest complete"
        if missing:
            status = "warn"
            detail = "incomplete manifest"
            publish_value = guest.get("publish_date") or guest.get("episode_publish_date")
            try:
                days = (date.fromisoformat(str(publish_value)) - today).days
                if 0 <= days <= 7:
                    status = "fail"
                    detail = "incomplete manifest within 7 days of publish"
            except (TypeError, ValueError):
                pass
        observations.append(_obs(email, status, str(path), detail, metrics))
    for obs in observations:
        _append(state_dir, obs)
    return observations


def _emit_run_record(
    state_dir: Path,
    *,
    started: float,
    status: str,
    errors: list[str],
) -> None:
    try:
        runrecord.emit_record(
            state_dir,
            department="podcast",
            node="manifest_sensor",
            status=status,
            release=runrecord.read_release(state_dir.parent),
            trigger={
                "kind": "time",
                "id": "podcast-daily",
                "dedupe_key": (
                    f"{datetime.now(timezone.utc).date().isoformat()}-manifest_sensor"
                ),
            },
            duration_ms=int((time.perf_counter() - started) * 1000),
            errors=errors,
            artifacts=[str(state_dir / "observations.jsonl")]
            if (state_dir / "observations.jsonl").exists()
            else [],
            external_actions_taken=0,
        )
    except Exception:
        LOGGER.exception("manifest_sensor failed to append its runs-v2 record")
        raise


def run(state_dir: Path, sources: Path, today: date | None = None) -> list[dict]:
    state_dir = Path(state_dir)
    started = time.perf_counter()
    try:
        observations = _run(state_dir, Path(sources), today)
    except Exception as exc:
        _emit_run_record(
            state_dir,
            started=started,
            status="error",
            errors=[type(exc).__name__],
        )
        raise
    errors = [
        f"manifest:{row['subject']}:{row['status']}"
        for row in observations
        if row["status"] != "ok"
    ]
    _emit_run_record(
        state_dir,
        started=started,
        status="error" if errors else "ok",
        errors=errors,
    )
    return observations


def main() -> None:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=repo / "departments/podcast/state")
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    run(args.state_dir, args.sources)


if __name__ == "__main__":
    main()
