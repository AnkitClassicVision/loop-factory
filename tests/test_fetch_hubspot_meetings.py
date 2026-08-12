from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "fetch_hubspot_meetings", REPO / "estate" / "fetchers" / "fetch_hubspot_meetings.py"
)
fetcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetcher)


def _meeting(mid, start, end=None, outcome=None):
    props = {"hs_meeting_start_time": start}
    if end:
        props["hs_meeting_end_time"] = end
    if outcome:
        props["hs_meeting_outcome"] = outcome
    return {"id": mid, "properties": props}


def test_build_events_maps_outcome_and_gates_decision_maker():
    meetings = [
        _meeting("1", "2026-08-01T14:00:00Z", "2026-08-01T14:25:00Z", "COMPLETED"),
        _meeting("2", "2026-08-02T15:00:00Z", "2026-08-02T15:30:00Z", "NO_SHOW"),
        _meeting("3", "2026-09-01T15:00:00Z"),  # future booking, no end/outcome
    ]
    assoc = {"1": ["10"], "2": ["11"], "3": ["10"]}
    emails = {"10": "Owner@X.Example", "11": "other@x.example"}
    lane_emails = {"owner@x.example", "other@x.example"}
    events, summary = fetcher.build_events(
        meetings, lambda ids: {i: assoc.get(i, []) for i in ids},
        lambda ids: {i: emails.get(i) for i in ids}, lane_emails,
    )
    assert summary["events"] == 3
    by_id = {e["event_id"]: e for e in events}
    held_candidate = by_id["hs-1-10"]
    assert held_candidate["attended"] is True
    assert held_candidate["minutes"] == 25
    assert held_candidate["attendee_email"] == "owner@x.example"
    # held-call truth boundary: never true in v1
    assert all(e["decision_maker_present"] is False for e in events)
    assert by_id["hs-2-11"]["attended"] is False
    assert by_id["hs-3-10"]["minutes"] == 0 and by_id["hs-3-10"]["attended"] is False


def test_build_events_minimizes_to_lane_emails():
    meetings = [_meeting("1", "2026-08-01T14:00:00Z"), _meeting("2", "2026-08-01T15:00:00Z")]
    assoc = {"1": ["10"], "2": []}
    emails = {"10": "internal@mybcat.example"}
    events, summary = fetcher.build_events(
        meetings, lambda ids: {i: assoc.get(i, []) for i in ids},
        lambda ids: {i: emails.get(i) for i in ids}, {"someone@else.example"},
    )
    assert events == []
    assert summary["attendee_outside_lanes"] == 1
    assert summary["no_contact_association"] == 1


def test_fetch_meetings_pagination_and_truncation():
    pages = [
        {"results": [{"id": "1"}], "paging": {"next": {"after": 1}}},
        {"results": [{"id": "2"}]},
    ]
    calls = []

    def search_page(payload):
        calls.append(payload.get("after"))
        return pages[len(calls) - 1]

    meetings, truncated = fetcher.fetch_meetings(search_page, "2026-05-01T00:00:00Z", page_cap=5)
    assert [m["id"] for m in meetings] == ["1", "2"]
    assert truncated is False
    assert calls == [None, 1]

    calls.clear()
    endless = lambda payload: {"results": [], "paging": {"next": {"after": 1}}}
    _, truncated = fetcher.fetch_meetings(endless, "2026-05-01T00:00:00Z", page_cap=2)
    assert truncated is True


def test_load_lane_emails_and_write_perms(tmp_path):
    (tmp_path / "icaregrow.json").write_text(
        json.dumps({"rows": [{"email": "A@B.Example"}, {"email": ""}]})
    )
    (tmp_path / "luma.json").write_text(json.dumps({"rows": []}))
    assert fetcher.load_lane_emails(tmp_path) == {"a@b.example"}
    target = tmp_path / "calendar_events.json"
    fetcher.write_events_file(target, [])
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"events": []}
