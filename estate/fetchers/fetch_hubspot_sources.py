#!/usr/bin/env python3
"""Estate-side fetcher: HubSpot -> sales department source lanes.

Writes the three HubSpot-backed lane files under the sales department's
sources dir in the frozen source shape (plan 2026-08-06-cards-v2-f3-sales,
section B):

    icaregrow.json      competitive-analysis signups
    pfs_warm.json       exit signups (bcat_exit_* intake or exit conversions)
    website_forms.json  other website form conversions

Runs estate-side because departments hold no credentials (kernel
capabilities allowlist). The sources dir is the sanctioned location for
real contact data as INPUT; everything the department writes downstream
is opaque 16-hex subject ids. Lane files are written 0600.

The HubSpot private-app token is read at runtime via `secret-get` and is
never logged, printed, or persisted. The summary printed to stdout holds
counts only — no emails, no names.

Lane selectors (discovered 2026-08-06 against portal 23344341):
  - contact_role enum: decision_maker | champion | other
  - icp_tier enum: tier_1 | tier_2 | tier_3 | unknown
  - bcat_exit_horizon (any value) marks exit-intake contacts
  - conversion names observed: "Meetings Link: ankit98/competitive-analysis",
    "Exit-Ready Planner — What is your optometry practice worth?: #planner"
When the iCareGrow signup form goes live in HubSpot, add its distinctive
token to ICAREGROW_TOKENS below.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HUBSPOT_SEARCH_URL = "https://api.hubapi.com/crm/v3/objects/contacts/search"
DEFAULT_SECRET_NAME = "hubspot-daily/hubspot/private-app-token"
PAGE_SIZE = 200
DEFAULT_PAGE_CAP = 25

CONVERSION_PROPS = ("first_conversion_event_name", "recent_conversion_event_name")
# CONTAINS_TOKEN matches whole tokens inside the conversion event name.
ICAREGROW_TOKENS = ("competitive",)
EXIT_TOKENS = ("exit",)
EXIT_MARKER_PROP = "bcat_exit_horizon"
MEETINGS_PREFIX = "Meetings Link:"

# Field minimization (read_broker spirit): only what the frozen row shape
# and lane selectors need. Never request notes, bodies, or phone fields.
REQUESTED_PROPERTIES = [
    "email",
    "firstname",
    "lastname",
    "contact_role",
    "icp_tier",
    EXIT_MARKER_PROP,
    "first_conversion_event_name",
    "recent_conversion_event_name",
    "first_conversion_date",
    "recent_conversion_date",
    "createdate",
]

ICP_FIT_BY_TIER = {"tier_1": True, "tier_2": True, "tier_3": False}
MAX_NAME_LEN = 240


def _token_groups(tokens):
    return [
        {"filters": [{"propertyName": prop, "operator": "CONTAINS_TOKEN", "value": token}]}
        for prop in CONVERSION_PROPS
        for token in tokens
    ]


LANE_FILTER_GROUPS = {
    "icaregrow": _token_groups(ICAREGROW_TOKENS),
    "pfs_warm": _token_groups(EXIT_TOKENS)
    + [{"filters": [{"propertyName": EXIT_MARKER_PROP, "operator": "HAS_PROPERTY"}]}],
    "website_forms": [
        {"filters": [{"propertyName": prop, "operator": "HAS_PROPERTY"}]}
        for prop in CONVERSION_PROPS
    ],
}
# First lane to claim a contact wins; mirrors the intake dedup priority so
# selector overlap cannot fabricate double-touch alarms (sense gate B).
LANE_PRIORITY = ("icaregrow", "pfs_warm", "website_forms")


def read_token(secret_name: str) -> str:
    try:
        proc = subprocess.run(
            ["secret-get", secret_name],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "SECRET_CALLER": "fetch_hubspot_sources"},
        )
    except FileNotFoundError:
        raise SystemExit("secret-get CLI not found on PATH")
    if proc.returncode != 0:
        # stderr may describe AWS errors; the secret value is never on stderr
        detail = (proc.stderr or "").strip()[:200]
        raise SystemExit(f"secret-get {secret_name} failed rc={proc.returncode}: {detail}")
    token = proc.stdout.strip()
    if not token:
        raise SystemExit(f"secret-get {secret_name} returned an empty value")
    return token


def hubspot_search_page(token: str, filter_groups, after=None):
    payload = {
        "filterGroups": filter_groups,
        "properties": REQUESTED_PROPERTIES,
        "limit": PAGE_SIZE,
        "sorts": [{"propertyName": "createdate", "direction": "ASCENDING"}],
    }
    if after is not None:
        payload["after"] = after
    req = urllib.request.Request(
        HUBSPOT_SEARCH_URL,
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
        raise SystemExit(f"HubSpot search HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"HubSpot search unreachable: {exc.reason}")


def fetch_lane_contacts(search_page, lane: str, page_cap: int):
    """Collect all contacts for one lane. search_page(filter_groups, after) -> page dict."""
    contacts = []
    truncated = False
    after = None
    for _ in range(page_cap):
        page = search_page(LANE_FILTER_GROUPS[lane], after)
        contacts.extend(page.get("results", []))
        after = (page.get("paging") or {}).get("next", {}).get("after")
        if not after:
            break
    else:
        truncated = True
    return contacts, truncated


def _has_form_conversion(props) -> bool:
    """True when at least one conversion is a real form, not a meetings link."""
    for prop in CONVERSION_PROPS:
        value = props.get(prop)
        if value and not str(value).startswith(MEETINGS_PREFIX):
            return True
    return False


def build_row(props, lane: str):
    """Map a HubSpot contact's properties to the frozen source-row shape.

    Returns None when the row cannot honestly enter the lane (no email).
    """
    email = str(props.get("email") or "").strip().lower()
    if not email:
        return None
    name = f"{props.get('firstname') or ''} {props.get('lastname') or ''}".strip()
    ts = (
        props.get("recent_conversion_date")
        or props.get("first_conversion_date")
        or props.get("createdate")
    )
    if not ts:
        return None
    row = {
        "email": email,
        "name": name[:MAX_NAME_LEN],
        "role": str(props.get("contact_role") or "unknown"),
        "ts": ts,
    }
    icp_fit = ICP_FIT_BY_TIER.get(str(props.get("icp_tier") or ""))
    if icp_fit is not None:
        row["icp_fit"] = icp_fit
    if lane == "pfs_warm" or props.get(EXIT_MARKER_PROP):
        row["exit_intent"] = True
    return row


def build_lanes(search_page, page_cap: int = DEFAULT_PAGE_CAP):
    """Pure core: fetch every lane, apply exclusivity, map rows.

    Returns (lanes, summary) where lanes maps lane name -> row list.
    """
    lanes = {}
    summary = {"lane_counts": {}, "skipped_no_email_or_ts": 0, "truncated": {}}
    claimed = set()
    for lane in LANE_PRIORITY:
        contacts, truncated = fetch_lane_contacts(search_page, lane, page_cap)
        summary["truncated"][lane] = truncated
        rows = []
        for contact in contacts:
            contact_id = str(contact.get("id"))
            props = contact.get("properties") or {}
            if contact_id in claimed:
                continue
            if lane == "website_forms" and not _has_form_conversion(props):
                continue
            row = build_row(props, lane)
            if row is None:
                summary["skipped_no_email_or_ts"] += 1
                continue
            claimed.add(contact_id)
            rows.append(row)
        lanes[lane] = rows
        summary["lane_counts"][lane] = len(rows)
    return lanes, summary


def write_lane_file(path: Path, rows) -> None:
    """Atomic write, 0600 from creation — lane files hold real contact data."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"rows": rows}, handle, ensure_ascii=False)
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
    parser.add_argument("--page-cap", type=int, default=DEFAULT_PAGE_CAP)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and report counts, write nothing",
    )
    args = parser.parse_args(argv)

    if not args.sources_dir.is_dir():
        raise SystemExit(f"sources dir missing: {args.sources_dir}")

    token = read_token(args.secret_name)

    def search_page(filter_groups, after):
        return hubspot_search_page(token, filter_groups, after)

    lanes, summary = build_lanes(search_page, page_cap=args.page_cap)
    summary["dry_run"] = args.dry_run
    if not args.dry_run:
        for lane, rows in lanes.items():
            write_lane_file(args.sources_dir / f"{lane}.json", rows)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
