#!/usr/bin/env python3
"""Estate-side fetcher: HubSpot MEETING engagements -> sales calendar_events.json.

Writes `calendar_events.json` under the sales department's sources dir in
the frozen source shape (plan 2026-08-06-cards-v2-f3-sales, section B):

    {"events": [{"event_id", "attendee_email", "start", "minutes",
                 "attended", "decision_maker_present"}]}

Why HubSpot meetings and not Google Calendar: sales calls are booked
through HubSpot meetings links, and the podcast repo's headless Google
token carries YouTube scopes only (checked 2026-08-06). The HubSpot
MEETING engagement is the booking system of record and its
`hs_meeting_outcome` is set by a human rep, so `attended` derived from
outcome COMPLETED is confirmed evidence, not calendar inference.

Honesty floor (handoff decision 2026-08-06): nothing in HubSpot proves a
decision-maker sat in the call, so `decision_maker_present` is ALWAYS
false in v1 — held receipts stay gated until the cards-v2 held-confirm
loop exists. Do not relax this here; it is the held-call truth boundary.

PII minimization: only meetings whose associated contact email already
appears in a lane source file are emitted (other attendees — internal
staff, podcast guests — never enter the sales sources). Dropped counts
are reported, never silently.

Meeting outcome enum (portal 23344341): SCHEDULED, COMPLETED,
RESCHEDULED, NO_SHOW, CANCELED.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_BASE = "https://api.hubapi.com"
MEETINGS_SEARCH_URL = f"{API_BASE}/crm/v3/objects/meetings/search"
ASSOC_BATCH_URL = f"{API_BASE}/crm/v4/associations/meetings/contacts/batch/read"
CONTACTS_BATCH_URL = f"{API_BASE}/crm/v3/objects/contacts/batch/read"
DEFAULT_SECRET_NAME = "hubspot-daily/hubspot/private-app-token"
PAGE_SIZE = 200
DEFAULT_PAGE_CAP = 25
DEFAULT_LOOKBACK_DAYS = 90
BATCH_SIZE = 100

MEETING_PROPERTIES = [
    "hs_meeting_start_time",
    "hs_meeting_end_time",
    "hs_meeting_outcome",
]
LANE_FILES = ("icaregrow", "podcast_handoffs", "pfs_warm", "website_forms", "luma")


def read_token(secret_name: str) -> str:
    try:
        proc = subprocess.run(
            ["secret-get", secret_name],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "SECRET_CALLER": "fetch_hubspot_meetings"},
        )
    except FileNotFoundError:
        raise SystemExit("secret-get CLI not found on PATH")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[:200]
        raise SystemExit(f"secret-get {secret_name} failed rc={proc.returncode}: {detail}")
    token = proc.stdout.strip()
    if not token:
        raise SystemExit(f"secret-get {secret_name} returned an empty value")
    return token


def _post_json(url: str, token: str, payload) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise SystemExit(f"HubSpot {url.rsplit('/', 1)[-1]} HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"HubSpot unreachable: {exc.reason}")


def load_lane_emails(sources_dir: Path) -> set[str]:
    emails: set[str] = set()
    for lane in LANE_FILES:
        path = sources_dir / f"{lane}.json"
        if not path.is_file():
            continue
        rows = json.loads(path.read_text(encoding="utf-8")).get("rows", [])
        for row in rows:
            email = str(row.get("email") or "").strip().lower()
            if email:
                emails.add(email)
    return emails


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def fetch_meetings(search_page, since_iso: str, page_cap: int):
    """search_page(payload) -> page dict. Returns (meetings, truncated)."""
    meetings = []
    truncated = False
    after = None
    for _ in range(page_cap):
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "hs_meeting_start_time",
                            "operator": "GTE",
                            "value": since_iso,
                        }
                    ]
                }
            ],
            "properties": MEETING_PROPERTIES,
            "sorts": [
                {"propertyName": "hs_meeting_start_time", "direction": "ASCENDING"}
            ],
            "limit": PAGE_SIZE,
        }
        if after is not None:
            payload["after"] = after
        page = search_page(payload)
        meetings.extend(page.get("results", []))
        after = (page.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    else:
        truncated = True
    return meetings, truncated


def _minutes(props) -> int:
    start = props.get("hs_meeting_start_time")
    end = props.get("hs_meeting_end_time")
    if not start or not end:
        return 0
    try:
        start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return 0
    minutes = int((end_dt - start_dt).total_seconds() // 60)
    return max(minutes, 0)


def build_events(meetings, assoc_read, contacts_read, lane_emails):
    """Pure core. assoc_read(meeting_ids) -> {meeting_id: [contact_id, ...]};
    contacts_read(contact_ids) -> {contact_id: email}. Returns (events, summary).
    """
    summary = {
        "meetings_fetched": len(meetings),
        "events": 0,
        "no_contact_association": 0,
        "attendee_outside_lanes": 0,
        "contact_without_email": 0,
    }
    meeting_ids = [str(m.get("id")) for m in meetings]
    assoc = {}
    for chunk in _chunks(meeting_ids, BATCH_SIZE):
        assoc.update(assoc_read(chunk))
    contact_ids = sorted({cid for cids in assoc.values() for cid in cids})
    emails = {}
    for chunk in _chunks(contact_ids, BATCH_SIZE):
        emails.update(contacts_read(chunk))
    events = []
    for meeting in meetings:
        meeting_id = str(meeting.get("id"))
        props = meeting.get("properties") or {}
        start = props.get("hs_meeting_start_time")
        if not start:
            continue
        contact_ids_for_meeting = assoc.get(meeting_id, [])
        if not contact_ids_for_meeting:
            summary["no_contact_association"] += 1
            continue
        for contact_id in contact_ids_for_meeting:
            email = str(emails.get(contact_id) or "").strip().lower()
            if not email:
                summary["contact_without_email"] += 1
                continue
            if email not in lane_emails:
                summary["attendee_outside_lanes"] += 1
                continue
            events.append(
                {
                    "event_id": f"hs-{meeting_id}-{contact_id}",
                    "attendee_email": email,
                    "start": start,
                    "minutes": _minutes(props),
                    # human-confirmed outcome only; SCHEDULED/NO_SHOW/etc stay false
                    "attended": props.get("hs_meeting_outcome") == "COMPLETED",
                    # held-call truth boundary: never derivable from HubSpot in v1
                    "decision_maker_present": False,
                }
            )
    summary["events"] = len(events)
    return events, summary


def write_events_file(path: Path, events) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"events": events}, handle, ensure_ascii=False)
        handle.write("\n")
    tmp.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "--sources-dir",
        type=Path,
        default=repo_root / "departments" / "sales" / "state" / "sources",
    )
    parser.add_argument("--secret-name", default=DEFAULT_SECRET_NAME)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--page-cap", type=int, default=DEFAULT_PAGE_CAP)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and report counts, write nothing",
    )
    args = parser.parse_args(argv)

    if not args.sources_dir.is_dir():
        raise SystemExit(f"sources dir missing: {args.sources_dir}")

    lane_emails = load_lane_emails(args.sources_dir)
    token = read_token(args.secret_name)
    since = datetime.now(timezone.utc) - timedelta(days=args.lookback_days)
    since_iso = since.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def search_page(payload):
        return _post_json(MEETINGS_SEARCH_URL, token, payload)

    def assoc_read(meeting_ids):
        body = {"inputs": [{"id": mid} for mid in meeting_ids]}
        page = _post_json(ASSOC_BATCH_URL, token, body)
        out = {}
        for row in page.get("results", []):
            from_id = str((row.get("from") or {}).get("id"))
            out[from_id] = [str(to.get("toObjectId")) for to in row.get("to", [])]
        return out

    def contacts_read(contact_ids):
        body = {"properties": ["email"], "inputs": [{"id": cid} for cid in contact_ids]}
        page = _post_json(CONTACTS_BATCH_URL, token, body)
        return {
            str(row.get("id")): (row.get("properties") or {}).get("email")
            for row in page.get("results", [])
        }

    meetings, truncated = fetch_meetings(search_page, since_iso, args.page_cap)
    events, summary = build_events(meetings, assoc_read, contacts_read, lane_emails)
    summary["truncated"] = truncated
    summary["lookback_days"] = args.lookback_days
    summary["dry_run"] = args.dry_run
    if not args.dry_run:
        write_events_file(args.sources_dir / "calendar_events.json", events)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
