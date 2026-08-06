from __future__ import annotations

import importlib.util
import json
import stat
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "fetch_hubspot_sources", REPO / "estate" / "fetchers" / "fetch_hubspot_sources.py"
)
fetcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetcher)


def _contact(cid, **props):
    return {"id": cid, "properties": props}


def _fake_search(pages_by_lane):
    """Return a search_page(filter_groups, after) that serves per-lane fixtures."""

    def search_page(filter_groups, after):
        for lane, groups in fetcher.LANE_FILTER_GROUPS.items():
            if filter_groups == groups:
                pages = pages_by_lane.get(lane, [{"results": []}])
                index = after or 0
                page = dict(pages[index])
                if index + 1 < len(pages):
                    page["paging"] = {"next": {"after": index + 1}}
                return page
        raise AssertionError("unknown filter groups")

    return search_page


def test_lane_exclusivity_and_row_shape():
    exit_contact = _contact(
        "1",
        email="Owner@Practice.Example",
        firstname="A",
        lastname="B",
        contact_role="decision_maker",
        icp_tier="tier_1",
        bcat_exit_horizon="already_looking",
        recent_conversion_date="2026-08-01T10:00:00.000Z",
    )
    pages = {
        # same contact matches both selectors; icaregrow has priority
        "icaregrow": [{"results": [exit_contact]}],
        "pfs_warm": [{"results": [exit_contact]}],
        "website_forms": [{"results": []}],
    }
    lanes, summary = fetcher.build_lanes(_fake_search(pages))
    assert summary["lane_counts"] == {"icaregrow": 1, "pfs_warm": 0, "website_forms": 0}
    row = lanes["icaregrow"][0]
    assert row["email"] == "owner@practice.example"
    assert row["role"] == "decision_maker"
    assert row["icp_fit"] is True
    # bcat exit marker carries exit_intent even outside the pfs lane
    assert row["exit_intent"] is True
    # ts must parse the way intake_sensor parses it
    datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))


def test_website_lane_drops_meetings_only_and_missing_email():
    pages = {
        "website_forms": [
            {
                "results": [
                    _contact(
                        "10",
                        email="form@x.example",
                        first_conversion_event_name="Some Page: Contact Form",
                        createdate="2026-08-02T09:00:00.000Z",
                    ),
                    _contact(
                        "11",
                        email="meet@x.example",
                        first_conversion_event_name="Meetings Link: ankit98/1x1",
                        createdate="2026-08-02T09:00:00.000Z",
                    ),
                    _contact(
                        "12",
                        first_conversion_event_name="Some Page: Contact Form",
                        createdate="2026-08-02T09:00:00.000Z",
                    ),
                ]
            }
        ],
    }
    lanes, summary = fetcher.build_lanes(_fake_search(pages))
    assert summary["lane_counts"]["website_forms"] == 1
    assert lanes["website_forms"][0]["email"] == "form@x.example"
    assert summary["skipped_no_email_or_ts"] == 1


def test_icp_fit_mapping_and_role_default():
    row = fetcher.build_row({"email": "x@y.example", "createdate": "2026-08-01T00:00:00Z"}, "icaregrow")
    assert row["role"] == "unknown"
    assert "icp_fit" not in row and "exit_intent" not in row
    assert fetcher.build_row(
        {"email": "x@y.example", "icp_tier": "tier_3", "createdate": "2026-08-01T00:00:00Z"},
        "icaregrow",
    )["icp_fit"] is False
    # pfs lane always carries exit_intent
    assert fetcher.build_row(
        {"email": "x@y.example", "createdate": "2026-08-01T00:00:00Z"}, "pfs_warm"
    )["exit_intent"] is True


def test_role_falls_back_to_hc_classifier():
    base = {"email": "x@y.example", "createdate": "2026-08-01T00:00:00Z"}
    # human-set contact_role wins over the LLM classifier
    assert fetcher.build_row(
        {**base, "contact_role": "champion", "hc_contact_role": "Decision Maker"},
        "icaregrow",
    )["role"] == "champion"
    # LLM fallback is normalized so "Decision Maker" meets the qualify bar
    assert fetcher.build_row(
        {**base, "hc_contact_role": "Decision Maker"}, "icaregrow"
    )["role"] == "decision_maker"
    assert fetcher.build_row(
        {**base, "hc_contact_role": "End User"}, "icaregrow"
    )["role"] == "end_user"
    assert fetcher.build_row(base, "icaregrow")["role"] == "unknown"


def test_pagination_truncation_flag():
    pages = {"icaregrow": [{"results": []} for _ in range(3)]}
    contacts, truncated = fetcher.fetch_lane_contacts(
        _fake_search(pages), "icaregrow", page_cap=2
    )
    assert truncated is True
    _, all_pages = fetcher.fetch_lane_contacts(_fake_search(pages), "icaregrow", page_cap=5)
    assert all_pages is False


def test_write_lane_file_atomic_0600(tmp_path):
    target = tmp_path / "icaregrow.json"
    fetcher.write_lane_file(target, [{"email": "a@b.example", "ts": "2026-08-01T00:00:00Z"}])
    mode = stat.S_IMODE(target.stat().st_mode)
    assert mode == 0o600
    assert json.loads(target.read_text())["rows"][0]["email"] == "a@b.example"
    assert not target.with_name("icaregrow.json.tmp").exists()
