"""Verify provider-fetched publish-day evidence without live API access."""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path


def _append(state_dir: Path, obs: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obs, sort_keys=True) + "\n")


def _obs(subject: str, status: str, evidence: str, detail: str, metrics: dict) -> dict:
    return {"ts": datetime.now(timezone.utc).isoformat(), "sensor": "publishday",
            "subject": subject, "status": status, "evidence": evidence,
            "detail": detail, "metrics": metrics}


def run(state_dir: Path, sources: Path, today: date | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date()
    schedule_path = sources / "publish_schedule.json"
    if not schedule_path.is_file():
        obs = _obs("publish-schedule", "unknown", str(schedule_path),
                   f"missing source: {schedule_path}", {})
        _append(state_dir, obs)
        return [obs]
    try:
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        due = [row for row in schedule["episodes"] if row.get("publish_date") == today.isoformat()]
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        obs = _obs("publish-schedule", "unknown", str(schedule_path),
                   f"unreadable source: {exc}", {})
        _append(state_dir, obs)
        return [obs]
    if not due:
        obs = _obs("publish-schedule", "ok", str(schedule_path), "no publish due", {})
        _append(state_dir, obs)
        return [obs]

    provider_paths = [sources / "rss.xml", sources / "youtube_status.json",
                      sources / "social_receipts.json"]
    missing_path = next((path for path in provider_paths if not path.is_file()), None)
    if missing_path is not None:
        observations = [_obs(str(row.get("episode_id", "unknown")), "unknown",
                             str(missing_path), f"missing source: {missing_path}", {}) for row in due]
        for obs in observations:
            _append(state_dir, obs)
        return observations
    try:
        rss_root = ET.parse(provider_paths[0]).getroot()
        rss_text = " ".join(text.strip() for text in rss_root.itertext() if text.strip())
        youtube = json.loads(provider_paths[1].read_text(encoding="utf-8"))
        social = json.loads(provider_paths[2].read_text(encoding="utf-8"))
    except (OSError, ValueError, ET.ParseError, json.JSONDecodeError) as exc:
        observations = [_obs(str(row.get("episode_id", "unknown")), "unknown",
                             ",".join(map(str, provider_paths)), f"unreadable source: {exc}", {}) for row in due]
        for obs in observations:
            _append(state_dir, obs)
        return observations

    youtube_rows = youtube.get("episodes", youtube) if isinstance(youtube, dict) else youtube
    if isinstance(youtube_rows, dict):
        youtube_rows = [dict(value, episode_id=key) if isinstance(value, dict) else
                        {"episode_id": key, "status": value}
                        for key, value in youtube_rows.items()]
    social_rows = social.get("receipts", social) if isinstance(social, dict) else social
    if isinstance(social_rows, dict):
        social_rows = [{"receipt_id": key, "value": value}
                       for key, value in social_rows.items()]
    observations = []
    for row in due:
        episode_id = str(row.get("episode_id", ""))
        expected = row.get("expected") or {}
        rss_ok = episode_id in rss_text or str(expected.get("rss_url") or "") in rss_text
        yt_row = next((item for item in youtube_rows if str(item.get("episode_id")) == episode_id), {})
        youtube_ok = bool(yt_row) and (yt_row.get("public") is True or
                                      str(yt_row.get("status", "")).casefold() == "public")
        expected_receipts = expected.get("social_receipts") or []
        present_ids = {str(item.get("receipt_id", item.get("id"))) if isinstance(item, dict)
                       else str(item) for item in social_rows}
        missing_receipts = [str(item) for item in expected_receipts if str(item) not in present_ids]
        failures = ([] if rss_ok else ["rss"]) + ([] if youtube_ok else ["youtube"]) + missing_receipts
        metrics = {"rss_present": rss_ok, "youtube_public": youtube_ok,
                   "expected_social_receipts": expected_receipts,
                   "missing_social_receipts": missing_receipts}
        observations.append(_obs(episode_id, "ok" if not failures else "fail",
                                 ",".join(map(str, provider_paths)),
                                 "publish verified" if not failures else "missing: " + ", ".join(failures),
                                 metrics))
    for obs in observations:
        _append(state_dir, obs)
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
