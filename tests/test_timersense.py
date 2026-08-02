import json
from pathlib import Path

import pytest

from factory import timersense


LISTING = """Sun 2026-08-02 15:00:00 EDT 1h left Sat 2026-08-01 15:00:00 EDT 23h ago podcast-loop-guests.timer podcast-loop-guests.service
Mon 2026-08-03 08:30:00 EDT 18h left Sun 2026-08-02 08:30:00 EDT 5h ago seo-loop-daily.timer seo-loop-daily.service
"""
SHOW_OK = """ActiveState=inactive
SubState=dead
Result=success
ExecMainStatus=0
ExecMainExitTimestamp=Sun 2026-08-02 08:31:00 EDT
UnitFileState=enabled
"""


def fixture_runner(listing=LISTING, show=SHOW_OK):
    def run(argv):
        return listing if "list-timers" in argv else show
    return run


def test_list_timers_parses_units_and_timestamps(monkeypatch):
    monkeypatch.setattr(timersense, "_run", fixture_runner())
    rows = timersense.parse_list_timers(timersense._run(["list-timers"]))
    assert [row["unit"] for row in rows] == ["podcast-loop-guests.timer", "seo-loop-daily.timer"]
    assert rows[0]["next_run"].startswith("2026-08-02T")
    assert rows[0]["last_run"].startswith("2026-08-01T")


def test_show_parsing_yields_enabled_result_and_exit(monkeypatch):
    monkeypatch.setattr(timersense, "_run", fixture_runner())
    timer = timersense.collect(["podcast*"])[0]
    assert (timer["enabled"], timer["last_result"], timer["exit_status"]) == (True, "success", 0)


def test_failure_result_maps_to_failure(monkeypatch):
    failed = SHOW_OK.replace("Result=success", "Result=exit-code").replace("ExecMainStatus=0", "ExecMainStatus=17")
    monkeypatch.setattr(timersense, "_run", fixture_runner(show=failed))
    timer = timersense.collect(["seo*"])[0]
    assert timer["last_result"] == "failure"
    assert timer["exit_status"] == 17


def test_unparseable_timestamp_becomes_null(monkeypatch):
    broken = SHOW_OK.replace("Sun 2026-08-02 08:31:00 EDT", "not-a-timestamp")
    monkeypatch.setattr(timersense, "_run", fixture_runner(show=broken))
    assert timersense.collect(["seo*"])[0]["last_run"] is None


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("podcast-loop-guest-acquisition.timer", "podcast"),
        ("seo-loop-daily.timer", "seo"),
        ("finance-os-daily-loop.timer", "finance"),
        ("social-escalations.timer", "social"),
        ("mystery-job.timer", "other"),
    ],
)
def test_grouping_heuristic(monkeypatch, unit, expected):
    monkeypatch.setattr(timersense, "_run", fixture_runner())
    assert timersense.group_for_unit(unit) == expected


def test_grouping_accepts_explicit_override_map(monkeypatch):
    monkeypatch.setattr(timersense, "_run", fixture_runner())
    assert timersense.group_for_unit("custom-nightly.timer", {"custom-*": "custom"}) == "custom"


def test_pattern_filtering(monkeypatch):
    monkeypatch.setattr(timersense, "_run", fixture_runner())
    timers = timersense.collect(["seo-*", "*-missing-*"])
    assert [timer["unit"] for timer in timers] == ["seo-loop-daily.timer"]


def test_tolerate_missing_writes_empty_valid_snapshot(monkeypatch, tmp_path):
    def unavailable(argv):
        raise FileNotFoundError("systemctl")
    monkeypatch.setattr(timersense, "_run", unavailable)
    out = tmp_path / "timers.json"
    assert timersense.main(["--out", str(out), "--tolerate-missing"]) == 0
    payload = json.loads(out.read_text())
    assert payload["timers"] == []
    assert payload["note"] == "systemctl unavailable"


def test_atomic_write_leaves_valid_json_and_no_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(timersense, "_run", fixture_runner())
    out = tmp_path / "nested" / "timers.json"
    timersense.snapshot(out)
    assert json.loads(out.read_text())["schema"] == "timers-snapshot/v1"
    assert list(out.parent.iterdir()) == [out]


def test_receipt_shape(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(timersense, "_run", fixture_runner())
    out = tmp_path / "timers.json"
    assert timersense.main(["--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "timers": 2,
        "groups": ["podcast", "seo"],
        "out": str(out),
    }


def test_missing_without_tolerance_returns_one(monkeypatch, tmp_path, capsys):
    def unavailable(argv):
        raise FileNotFoundError("systemctl")
    monkeypatch.setattr(timersense, "_run", unavailable)
    assert timersense.main(["--out", str(tmp_path / "no.json")]) == 1
    assert capsys.readouterr().out == ""
