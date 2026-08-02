from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

from factory.board import render_board
from factory.boardfeed import build_feed


NOW = "2026-08-02T20:00:00+00:00"
CAPTURED_AT = "2026-08-02T18:37:00+00:00"


def _timer(
    unit: str,
    *,
    group: str = "alpha",
    enabled: bool = True,
    last_result: str = "success",
    last_run: str | None = "2026-08-02T18:30:00+00:00",
    next_run: str | None = "2026-08-02T18:45:00+00:00",
    exit_status: int | str | None = 0,
) -> dict:
    return {
        "unit": unit,
        "service": unit.removesuffix(".timer") + ".service",
        "enabled": enabled,
        "next_run": next_run,
        "last_run": last_run,
        "last_result": last_result,
        "exit_status": exit_status,
        "group": group,
    }


def _write_timers(root: Path, timers: list[dict]) -> Path:
    path = root / "estate" / "state" / "timers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "timers-snapshot/v1",
                "captured_at": CAPTURED_AT,
                "timers": timers,
            }
        ),
        encoding="utf-8",
    )
    return path


def _build(root: Path, *, timers_path: Path | None = None) -> tuple[list[dict], dict, Path]:
    out = root / "board-feed.ndjson"
    receipt = build_feed(root, out=out, now=NOW, timers_path=timers_path)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    return rows, receipt, out


def _kind(rows: list[dict], kind: str) -> list[dict]:
    return [row for row in rows if row["kind"] == kind]


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def _text(page: str) -> str:
    parser = _TextParser()
    parser.feed(page)
    return " ".join(parser.parts)


def test_loop_status_rows_are_emitted_from_timer_snapshot(tmp_path):
    timers_path = _write_timers(tmp_path, [_timer("alpha-daily.timer")])

    rows, receipt, _ = _build(tmp_path, timers_path=timers_path)

    loops = _kind(rows, "loop_status")
    expected_data = _timer("alpha-daily.timer")
    expected_data.pop("group")
    assert len(loops) == 1
    assert loops[0] == {
        "id": "loop_status:alpha:alpha-daily.timer:2026-08-02T18:00:00Z",
        "kind": "loop_status",
        "ts": "2026-08-02T18:37:00+00:00",
        "department": "alpha",
        "data": expected_data,
    }
    assert receipt["loops"] == 1


def test_timer_group_becomes_loop_status_department(tmp_path):
    timers_path = _write_timers(
        tmp_path,
        [_timer("first.timer", group="alpha"), _timer("second.timer", group="beta")],
    )

    rows, _, _ = _build(tmp_path, timers_path=timers_path)

    assert {
        row["data"]["unit"]: row["department"] for row in _kind(rows, "loop_status")
    } == {"first.timer": "alpha", "second.timer": "beta"}


def test_failure_timer_emits_andon_and_loop_status(tmp_path):
    timers_path = _write_timers(
        tmp_path,
        [_timer("failed.timer", last_result="failure", exit_status=7)],
    )

    rows, receipt, _ = _build(tmp_path, timers_path=timers_path)

    assert [row["data"]["unit"] for row in _kind(rows, "loop_status")] == [
        "failed.timer"
    ]
    failures = [
        row for row in _kind(rows, "andon") if row["data"].get("code") == "LOOP_FAILED"
    ]
    assert len(failures) == 1
    assert failures[0]["data"] == {
        "code": "LOOP_FAILED",
        "severity": "breach",
        "detail": "failed.timer last run failed (exit 7)",
        "observed": "failure",
        "setpoint": "success",
    }
    assert receipt["loops"] == 1


def test_disabled_timer_is_visible_without_andon(tmp_path):
    disabled = _timer(
        "paused.timer",
        enabled=False,
        last_result="unknown",
        last_run=None,
        next_run=None,
        exit_status=None,
    )
    timers_path = _write_timers(tmp_path, [disabled])

    rows, _, _ = _build(tmp_path, timers_path=timers_path)

    assert _kind(rows, "loop_status")[0]["data"]["enabled"] is False
    assert _kind(rows, "loop_status")[0]["data"]["next_run"] is None
    assert not _kind(rows, "andon")


def test_absent_timer_snapshot_is_tolerated_with_zero_loops(tmp_path):
    rows, receipt, _ = _build(tmp_path)

    assert not _kind(rows, "loop_status")
    assert not _kind(rows, "andon")
    assert receipt["loops"] == 0
    assert receipt["malformed"] == 0


def test_malformed_or_wrong_schema_timer_snapshot_counts_feed_health(tmp_path):
    timers_path = tmp_path / "timers.json"
    timers_path.write_text("{not-json}", encoding="utf-8")

    rows, receipt, _ = _build(tmp_path, timers_path=timers_path)

    assert receipt["malformed"] == 1
    assert rows[-1]["kind"] == "feed_health"
    assert rows[-1]["data"] == {"malformed": 1}

    timers_path.write_text(
        json.dumps({"schema": "timers-snapshot/v0", "captured_at": CAPTURED_AT, "timers": []}),
        encoding="utf-8",
    )
    rows, receipt, _ = _build(tmp_path, timers_path=timers_path)
    assert receipt["malformed"] == 1
    assert rows[-1]["data"] == {"malformed": 1}


def test_renderer_shows_all_loops_groups_units_status_dots_and_unknowns(tmp_path):
    timers_path = _write_timers(
        tmp_path,
        [
            _timer("clean.timer", group="alpha"),
            _timer("broken.timer", group="beta", last_result="failure", exit_status=2),
            _timer(
                "paused.timer",
                group="beta",
                enabled=False,
                last_result="unknown",
                last_run=None,
                next_run=None,
                exit_status=None,
            ),
        ],
    )
    _, _, feed = _build(tmp_path, timers_path=timers_path)

    page = render_board(feed, tmp_path / "board.html")
    text = _text(page)

    assert "All loops" in text
    assert "alpha" in text and "beta" in text
    assert "clean" in text and "broken" in text and "paused" in text
    assert "clean.timer" not in text and "paused.timer" not in text
    assert "disabled" in text and "unknown" in text
    assert 'class="loop-dot success"' in page
    assert 'class="loop-dot failure"' in page
    assert 'class="loop-dot muted"' in page


def test_renderer_shows_loop_telemetry_empty_state(tmp_path):
    _, _, feed = _build(tmp_path)

    page = render_board(feed, tmp_path / "board.html")

    assert "no loop telemetry — run timersense" in _text(page)


def test_timer_feed_build_is_byte_deterministic(tmp_path):
    timers_path = _write_timers(
        tmp_path,
        [_timer("z.timer", group="beta"), _timer("a.timer", group="alpha")],
    )
    first = tmp_path / "first.ndjson"
    second = tmp_path / "second.ndjson"

    build_feed(tmp_path, out=first, now=NOW, timers_path=timers_path)
    build_feed(tmp_path, out=second, now=NOW, timers_path=timers_path)

    assert first.read_bytes() == second.read_bytes()


def test_zone_two_renders_loop_failed_andon_content(tmp_path):
    timers_path = _write_timers(
        tmp_path,
        [_timer("failed.timer", last_result="failure", exit_status="status-1")],
    )
    _, _, feed = _build(tmp_path, timers_path=timers_path)

    page = render_board(feed, tmp_path / "board.html")
    zone_two = page[page.index("2 · Main actions") : page.index("3 · Activity")]

    assert "LOOP_FAILED" in zone_two
    assert "failed.timer last run failed (exit status-1)" in zone_two
