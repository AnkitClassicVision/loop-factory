from __future__ import annotations

import json
import re
from pathlib import Path

from factory import rollup
from factory.board import render_html, render_site
from factory.boardfeed import build_feed
from factory.runrecord import append_record, build_record


NOW = "2026-08-02T20:00:00+00:00"


def _department(root: Path, name: str = "alpha") -> Path:
    department = root / "departments" / name
    state = department / "state"
    state.mkdir(parents=True)
    (state / "STATE.json").write_text(
        json.dumps(
            {
                "department": name,
                "epoch": 1,
                "last_cycle_at": NOW,
                "autonomy_state": "shadow",
                "open_findings": [],
            }
        ),
        encoding="utf-8",
    )
    (state / "heartbeats.jsonl").write_text(
        json.dumps({"ts": NOW, "ok": True}) + "\n",
        encoding="utf-8",
    )
    estate = root / "estate" / "state"
    estate.mkdir(parents=True, exist_ok=True)
    (estate / "STATE.json").write_text(
        json.dumps({"ok": True, "last_cycle_at": NOW}), encoding="utf-8"
    )
    return department


def _run(department: Path, run_id: str, *, status: str = "ok") -> None:
    record = build_record(
        schema="run-record/v2",
        rev=2,
        run_id=run_id,
        department=department.name,
        node="daily",
        epoch=1,
        ts="2026-08-02T19:00:00+00:00",
        attempt=1,
        round=None,
        release=None,
        trigger={"kind": "time", "id": run_id, "dedupe_key": run_id},
        engine="codex",
        model="fixture",
        auth_class="oauth_cli",
        usage={
            "input_tokens": 10,
            "output_tokens": 4,
            "cache_read": 0,
            "cache_creation": 0,
        },
        cost={"lane": "flat_subscription", "model_calls": 1},
        duration_ms=10,
        status=status,
        errors=[],
        artifacts=[],
        receipts=[{"kind": "local"}],
        evaluator=None,
        approval=None,
        external_actions_taken=0,
    )
    append_record(department / "state", record)
    telemetry = {
        "schema_version": "step-telemetry/v1",
        "ts": "2026-08-02T19:00:00+00:00",
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": "fixture",
        "gen_ai.response.model": "fixture",
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 4,
        "gen_ai.response.finish_reasons": ["stop"],
        "duration_ms": 10,
        "error.type": None,
        "loopfactory.cost_usd": 0,
        "loopfactory.auth.route": "oauth_cli",
        "loopfactory.engine": "codex",
        "loopfactory.price.schema_version": "model-prices/v1",
        "loopfactory.price.effective_date": "2026-08-02",
        "loopfactory.telemetry.source": "runner_reported",
        "loopfactory.department": department.name,
        "loopfactory.run_id": run_id,
        "loopfactory.step_id": f"daily-{run_id}",
        "loopfactory.node": "daily",
        "estimated": False,
    }
    with (department / "state" / "telemetry.jsonl").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(json.dumps(telemetry) + "\n")


def _timers(root: Path, rows: list[tuple[str, str, str]]) -> Path:
    path = root / "timers.json"
    timers = []
    for unit, group, result in rows:
        timers.append(
            {
                "unit": unit,
                "service": unit.removesuffix(".timer") + ".service",
                "enabled": True,
                "next_run": NOW,
                "last_run": NOW,
                "last_result": result,
                "exit_status": 0 if result == "success" else 1,
                "group": group,
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema": "timers-snapshot/v1",
                "captured_at": NOW,
                "timers": timers,
            }
        ),
        encoding="utf-8",
    )
    return path


def _feed_row(row_id: str, kind: str, department: str, **data: object) -> dict:
    return {
        "id": row_id,
        "kind": kind,
        "ts": NOW,
        "department": department,
        "data": data,
    }


def _write_feed(root: Path, rows: list[dict]) -> Path:
    path = root / "board-feed.ndjson"
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _history_day(
    date: str,
    *,
    departments: dict[str, dict] | None = None,
    loops: dict[str, dict] | None = None,
) -> dict:
    return {
        "schema": "board-history/v1",
        "date": date,
        "departments": departments or {},
        "loops": loops or {},
    }


def _write_history(root: Path, days: list[dict]) -> Path:
    history = root / "history"
    history.mkdir(parents=True)
    for day in days:
        (history / f'{day["date"]}.json').write_text(
            json.dumps(day, sort_keys=True),
            encoding="utf-8",
        )
    return history


def _daily(runs: int | str, ok: int | str, error: int | str = 0) -> dict:
    return {
        "runs": runs,
        "ok": ok,
        "error": error,
        "blocked": 0,
        "tokens_in": 10,
        "tokens_out": 4,
        "model_calls": 1,
    }


def test_history_file_written_with_feed_daily_shape(tmp_path):
    department = _department(tmp_path)
    _run(department, "clean")
    history_dir = tmp_path / "archive"
    assert rollup.rebuild(tmp_path)["complete"] is True

    receipt = build_feed(tmp_path, now=NOW, history_dir=history_dir)

    history_path = history_dir / "2026-08-02.json"
    assert receipt["history"] == str(history_path)
    assert json.loads(history_path.read_text(encoding="utf-8")) == {
        "schema": "board-history/v1",
        "date": "2026-08-02",
        "departments": {
            "alpha": {
                "runs": 1,
                "ok": 1,
                "error": 0,
                "blocked": 0,
                "tokens_in": 10,
                "tokens_out": 4,
                "model_calls": 1,
            }
        },
        "loops": {},
    }


def test_same_day_history_rebuild_overwrites_with_latest_feed(tmp_path):
    department = _department(tmp_path)
    _run(department, "first")
    history_dir = tmp_path / "archive"
    assert rollup.rebuild(tmp_path)["complete"] is True
    build_feed(tmp_path, now=NOW, history_dir=history_dir)
    _run(department, "second", status="error")

    assert rollup.rebuild(tmp_path)["complete"] is True
    build_feed(tmp_path, now=NOW, history_dir=history_dir)

    saved = json.loads((history_dir / "2026-08-02.json").read_text(encoding="utf-8"))
    assert saved["departments"]["alpha"]["runs"] == 2
    assert saved["departments"]["alpha"]["ok"] == 1
    assert saved["departments"]["alpha"]["error"] == 1
    assert list(history_dir.iterdir()) == [history_dir / "2026-08-02.json"]


def test_unknown_daily_metrics_stay_unknown_in_history(tmp_path):
    _department(tmp_path)
    history_dir = tmp_path / "archive"

    build_feed(tmp_path, now=NOW, history_dir=history_dir)

    saved = json.loads((history_dir / "2026-08-02.json").read_text(encoding="utf-8"))
    assert saved["departments"] == {}


def test_history_counts_loop_failures_and_totals_per_group(tmp_path):
    timers = _timers(
        tmp_path,
        [
            ("a.timer", "group-a", "failure"),
            ("b.timer", "group-a", "success"),
            ("c.timer", "group-a", "failure"),
            ("d.timer", "group-b", "success"),
        ],
    )
    history_dir = tmp_path / "archive"

    build_feed(tmp_path, now=NOW, timers_path=timers, history_dir=history_dir)

    saved = json.loads((history_dir / "2026-08-02.json").read_text(encoding="utf-8"))
    assert saved["loops"] == {
        "group-a": {"total": 3, "failed": 2},
        "group-b": {"total": 1, "failed": 0},
    }


def test_department_tab_renders_seven_day_ok_rate_polyline(tmp_path):
    feed = _write_feed(
        tmp_path,
        [
            _feed_row("status", "dept_status", "alpha", ok=True),
            _feed_row("daily", "metrics", "alpha", runs=10, ok=8, error=2),
        ],
    )
    days = [
        _history_day(
            f"2026-07-{day:02d}",
            departments={"alpha": _daily(10, day - 20, 31 - day)},
        )
        for day in range(25, 32)
    ]
    history = _write_history(tmp_path, days)

    render_site(feed, tmp_path / "site", history_dir=history)

    page = (tmp_path / "site" / "alpha.html").read_text(encoding="utf-8")
    assert '<section aria-label="Seven days">' in page
    assert '<svg class="lc" viewBox="0 0 480 160"' in page
    assert '<polyline class="series"' in page
    assert 'aria-label="alpha ok rate over seven days"' in page
    assert "70%" in page
    assert "70 runs · 21 errors over window" in page


def test_unknown_history_day_creates_gap_instead_of_zero_point(tmp_path):
    records = [_feed_row("status", "dept_status", "alpha", ok=True)]
    days = [
        _history_day("2026-07-27", departments={"alpha": _daily(10, 8)}),
        _history_day(
            "2026-07-28",
            departments={"alpha": _daily("unknown", "unknown", "unknown")},
        ),
        _history_day("2026-07-29", departments={"alpha": _daily(10, 6)}),
    ]

    page = render_html(records, department="alpha", history=days)

    assert '<svg class="lc"' in page
    assert len(re.findall(r'<polyline class="series"', page)) == 2
    assert re.search(r'<circle[^>]+data-date="2026-07-28"', page) is None
    assert 'data-date="2026-07-28">07-28</text>' in page
    assert 'data-value="0"' not in page


def test_fewer_than_two_usable_days_shows_collecting_history(tmp_path):
    records = [_feed_row("status", "dept_status", "alpha", ok=True)]
    days = [
        _history_day("2026-07-28", departments={"alpha": _daily("unknown", "unknown")}),
        _history_day("2026-07-29", departments={"alpha": _daily(10, 9)}),
    ]

    page = render_html(records, department="alpha", history=days)

    assert "collecting history — 1 day so far" in page
    assert '<svg class="lc"' not in page


def test_loop_group_tab_renders_failed_count_trend(tmp_path):
    feed = _write_feed(
        tmp_path,
        [_feed_row("loop", "loop_status", "ops", unit="daily.timer", last_result="success")],
    )
    history = _write_history(
        tmp_path,
        [
            _history_day("2026-07-28", loops={"ops": {"total": 3, "failed": 2}}),
            _history_day("2026-07-29", loops={"ops": {"total": 3, "failed": 1}}),
        ],
    )

    render_site(feed, tmp_path / "site", history_dir=history)

    page = (tmp_path / "site" / "ops.html").read_text(encoding="utf-8")
    assert 'aria-label="ops failed loops over seven days"' in page
    assert 'data-value="2"' in page
    assert 'data-value="1"' in page
    assert "%" not in page[page.index('<section aria-label="Seven days">'):page.index('<section aria-label="Main actions">')]


def test_index_renders_estate_wide_ok_rate_chart(tmp_path):
    feed = _write_feed(
        tmp_path,
        [
            _feed_row("alpha", "dept_status", "alpha", ok=True),
            _feed_row("beta", "dept_status", "beta", ok=True),
        ],
    )
    history = _write_history(
        tmp_path,
        [
            _history_day(
                "2026-07-28",
                departments={"alpha": _daily(10, 8), "beta": _daily(2, 1)},
            ),
            _history_day(
                "2026-07-29",
                departments={"alpha": _daily(10, 5), "beta": _daily(10, 5)},
            ),
        ],
    )

    render_site(feed, tmp_path / "site", history_dir=history)

    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert 'aria-label="Estate ok rate over seven days"' in page
    assert 'data-value="75"' in page
    assert 'data-value="50"' in page
    assert "32 runs · 0 errors over window" in page


def test_site_history_renders_are_byte_identical(tmp_path):
    feed = _write_feed(
        tmp_path,
        [_feed_row("status", "dept_status", "alpha", ok=True)],
    )
    history = _write_history(
        tmp_path,
        [
            _history_day("2026-07-28", departments={"alpha": _daily(10, 8)}),
            _history_day("2026-07-29", departments={"alpha": _daily(10, 9)}),
        ],
    )

    first = render_site(feed, tmp_path / "first", history_dir=history)
    second = render_site(feed, tmp_path / "second", history_dir=history)

    assert first == second
    for filename in first:
        assert (tmp_path / "first" / filename).read_bytes() == (
            tmp_path / "second" / filename
        ).read_bytes()


def test_render_site_defaults_history_to_feed_sibling_directory(tmp_path):
    feed = _write_feed(
        tmp_path,
        [_feed_row("status", "dept_status", "alpha", ok=True)],
    )
    _write_history(
        tmp_path,
        [
            _history_day("2026-07-28", departments={"alpha": _daily(10, 7)}),
            _history_day("2026-07-29", departments={"alpha": _daily(10, 8)}),
        ],
    )

    render_site(feed, tmp_path / "site")

    page = (tmp_path / "site" / "alpha.html").read_text(encoding="utf-8")
    assert 'aria-label="alpha ok rate over seven days"' in page
