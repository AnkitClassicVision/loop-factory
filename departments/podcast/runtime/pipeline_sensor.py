"""Observe the independently sourced podcast recording pipeline."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from factory.charter_loader import load_charter


def _append(state_dir: Path, observation: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, sort_keys=True) + "\n")


def _observation(status: str, evidence: str, detail: str, metrics: dict) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "sensor": "pipeline",
        "subject": "recording-pipeline",
        "status": status,
        "evidence": evidence,
        "detail": detail,
        "metrics": metrics,
    }


def _read_list(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("source must contain a JSON list")
    return [row for row in data if isinstance(row, dict)]


def _pipeline_setpoint(charter: dict) -> int:
    for item in charter.get("setpoints", {}).get("outcome_additional", []):
        if item.get("metric") == "pipeline_guests":
            return int(item["target"])
    raise ValueError("charter has no pipeline_guests setpoint")


def _key(row: dict) -> tuple[str, str]:
    return (str(row.get("email") or "").strip().casefold(),
            str(row.get("name") or row.get("guest") or "").strip().casefold())


def run(state_dir: Path, sources: Path, charter_path: Path) -> dict:
    calendar_path = sources / "calendar.json"
    contacts_path = sources / "hubspot_contacts.json"
    missing = next((path for path in (calendar_path, contacts_path) if not path.is_file()), None)
    if missing is not None:
        obs = _observation("unknown", str(missing), f"missing source: {missing}", {})
        _append(state_dir, obs)
        return obs
    try:
        calendar = _read_list(calendar_path)
        contacts = _read_list(contacts_path)
        setpoint = _pipeline_setpoint(load_charter(charter_path, expect_department="podcast"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        obs = _observation("unknown", f"{calendar_path},{contacts_path}",
                           f"unreadable source: {exc}", {})
        _append(state_dir, obs)
        return obs

    contacts_by_email = {email: row for row in contacts if (email := _key(row)[0])}
    contacts_by_name = {name: row for row in contacts if (name := _key(row)[1])}
    counted = []
    seen = set()
    for event in calendar:
        event_type = str(event.get("event_type") or "").casefold()
        if "record" not in event_type and "podcast" not in event_type:
            continue
        email, name = _key(event)
        contact = contacts_by_email.get(email) if email else contacts_by_name.get(name)
        if contact is None:
            continue
        status = str(contact.get("podcast_status") or "").strip().casefold()
        if status not in {"active", "booked", "recording_pipeline", "scheduled"}:
            continue
        identity = email or name
        if not identity or identity in seen:
            continue
        seen.add(identity)
        counted.append({
            "guest": event.get("guest") or contact.get("name") or identity,
            "source_fields": {
                "calendar.json": {
                    "guest": event.get("guest"), "email": event.get("email"),
                    "event_type": event.get("event_type"), "start_iso": event.get("start_iso"),
                },
                "hubspot_contacts.json": {
                    "email": contact.get("email"), "name": contact.get("name"),
                    "podcast_status": contact.get("podcast_status"),
                },
                "join": "email" if email and email == _key(contact)[0] else "name",
            },
        })
    count = len(counted)
    status = "ok" if count >= setpoint else "fail"
    metrics = {"count": count, "setpoint": setpoint, "counted_guests": counted}
    obs = _observation(status, f"{calendar_path},{contacts_path}",
                       f"recording pipeline has {count} of {setpoint} guests", metrics)
    _append(state_dir, obs)
    return obs


def main() -> None:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=repo / "departments/podcast/state")
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--charter", type=Path, default=repo / "departments/podcast/charter.yaml")
    parser.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    run(args.state_dir, args.sources, args.charter)


if __name__ == "__main__":
    main()
