from __future__ import annotations

import json
from html.parser import HTMLParser

from factory.board import main, render_board


TS = "2026-08-02T16:00:00Z"


def _row(row_id, kind, department="alpha", ts=TS, **data):
    return {
        "id": row_id,
        "kind": kind,
        "ts": ts,
        "department": department,
        "data": data,
    }


def _write_feed(tmp_path, rows, *, malformed=()):
    feed = tmp_path / "board-feed.ndjson"
    lines = [json.dumps(row, sort_keys=True) for row in rows]
    lines.extend(malformed)
    feed.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return feed


def _render(tmp_path, rows, **kwargs):
    feed = _write_feed(tmp_path, rows)
    out = tmp_path / "board.html"
    return render_board(feed, out, **kwargs)


def _full_rows():
    return [
        _row("status", "dept_status", ok=True, epoch=42, autonomy_state="shadow"),
        _row(
            "objective",
            "metrics",
            objective_id="depth",
            label="work ready",
            setpoint="keep work ready",
            minimum=2,
            target=6,
            observed=4,
            unit=" items",
        ),
        _row(
            "daily",
            "metrics",
            runs=10,
            ok=8,
            blocked=1,
            error=1,
            evaluator_pass_rate=90,
        ),
        _row("andon", "andon", code="AUTH_EXPIRED", detail="three jobs paused", age="42 min"),
        _row("approval", "approval", status="awaiting review", age="3 h", card_ref="ANK-17"),
        _row("run", "active_run", node="daily_cycle", status="running"),
        _row(
            "telemetry",
            "metrics",
            lane="codex",
            model="gpt-example",
            auth_class="oauth_cli",
            calls=3,
            tokens_in=100,
            tokens_out=20,
            period="today",
        ),
        _row("f1", "funnel_stage", funnel_id="pipeline", stage="started", order=1, count=8, period="week"),
        _row("f2", "funnel_stage", funnel_id="pipeline", stage="finished", order=2, count=3, period="week"),
    ]


def test_full_fixture_has_fixed_zone_order_and_honesty_label(tmp_path):
    page = _render(tmp_path, _full_rows())

    headings = ["1 · Metrics", "2 · Main actions", "3 · Activity", "4 · Loop-specific"]
    positions = [page.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "LIVE — rendered from board-feed.ndjson" in page


def test_generated_html_parse_checks_with_standard_parser(tmp_path):
    class Checker(HTMLParser):
        def __init__(self):
            super().__init__()
            self.sections = 0
            self.alerts = 0

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "section":
                self.sections += 1
            if attributes.get("role") == "alert":
                self.alerts += 1

    checker = Checker()
    checker.feed(_render(tmp_path, _full_rows()))

    assert checker.sections == 4
    assert checker.alerts == 1


def test_objective_with_minimum_and_target_renders_html_bullet_ticks(tmp_path):
    rows = [
        _row(
            "objective",
            "metrics",
            objective_id="queue",
            label="queue depth",
            minimum=2,
            target=6,
            observed=3,
            unit=" jobs",
        )
    ]

    page = _render(tmp_path, rows)

    assert '<div class="bullet"' in page
    assert "min 2" in page
    assert "target 6" in page
    assert "<svg" not in page


def test_unknown_observed_is_literal_unknown_not_zero(tmp_path):
    rows = [
        _row(
            "objective",
            "metrics",
            objective_id="quality",
            label="quality rate",
            target="unknown",
            observed="unknown",
            unit="%",
        )
    ]

    page = _render(tmp_path, rows)
    objective = page[page.index('<article class="obj"'):page.index("</article>")]

    assert '<span class="unknown">unknown</span>' in objective
    assert ">0<" not in objective
    assert "0%" not in objective


def test_andon_is_red_block_and_removes_no_andon_message(tmp_path):
    page = _render(
        tmp_path,
        [_row("incident", "andon", fault="receipt gap", detail="next step blocked", age="12 min")],
    )

    assert '<article class="andon" role="alert">' in page
    assert "receipt gap" in page
    assert "alpha · 12 min" in page
    assert "next step blocked" in page
    assert "no andons — nothing needs you" not in page


def test_metered_api_telemetry_is_alert_not_stat_row(tmp_path):
    rows = [
        _row(
            "metered",
            "metrics",
            lane="metered-api",
            model="provider-model",
            auth_class="api",
            calls=1,
            tokens_in=40,
            tokens_out=5,
        )
    ]

    page = _render(tmp_path, rows)
    alert_start = page.index('<article class="andon telemetry-alert"')
    alert_end = page.index("</article>", alert_start)
    alert = page[alert_start:alert_end]

    assert "Metered/API lane detected" in alert
    assert "metered-api" in alert
    assert 'class="t-row"' not in alert
    assert 'class="route api"' not in page


def test_funnel_stages_are_sorted_by_order_and_show_counts(tmp_path):
    rows = [
        _row("last", "funnel_stage", funnel_id="intake", stage="third stage", order=3, count=2),
        _row("first", "funnel_stage", funnel_id="intake", stage="first stage", order=1, count=12),
        _row("middle", "funnel_stage", funnel_id="intake", stage="second stage", order=2, count=7),
    ]

    page = _render(tmp_path, rows)

    assert page.index("first stage") < page.index("second stage") < page.index("third stage")
    assert '<span class="f-count num">12</span>' in page
    assert '<span class="f-count num">7</span>' in page
    assert '<span class="f-count num">2</span>' in page


def test_unknown_metrics_group_falls_back_to_labeled_key_value_list(tmp_path):
    rows = [
        _row(
            "custom",
            "metrics",
            group="audience signals",
            weekly_downloads=1994,
            shares=41,
            editorial_note="rising",
        )
    ]

    page = _render(tmp_path, rows)

    assert "alpha · audience signals" in page
    assert "weekly downloads" in page and "1,994" in page
    assert "shares" in page and "41" in page
    assert "editorial note" in page and "rising" in page


def test_empty_feed_renders_no_departments_message(tmp_path):
    page = _render(tmp_path, [])

    assert "no departments reporting" in page
    assert "no objectives declared — charter setpoints missing" in page


def test_malformed_nonblank_lines_are_skipped_and_counted_in_footer(tmp_path):
    feed = _write_feed(
        tmp_path,
        [_row("status", "dept_status", ok=True, epoch=1)],
        malformed=("not-json", json.dumps({"id": "missing-shape"})),
    )
    out = tmp_path / "board.html"

    page = render_board(feed, out)

    assert "2 malformed feed lines" in page
    assert "1 department" in page


def test_same_input_renders_byte_identical_output(tmp_path):
    feed = _write_feed(tmp_path, list(reversed(_full_rows())))
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"

    render_board(feed, first)
    render_board(feed, second)

    assert first.read_bytes() == second.read_bytes()


def test_department_cli_filter_excludes_other_department_content(tmp_path):
    rows = [
        _row("alpha-status", "dept_status", department="alpha", ok=True, epoch=2),
        _row("beta-status", "dept_status", department="beta", ok=False, epoch=3),
        _row("alpha-custom", "metrics", department="alpha", group="signals", unique_alpha="visible"),
        _row("beta-custom", "metrics", department="beta", group="signals", unique_beta="hidden"),
    ]
    feed = _write_feed(tmp_path, rows)
    out = tmp_path / "alpha.html"

    assert main(["--feed", str(feed), "--out", str(out), "--department", "alpha", "--title", "Alpha control"]) == 0
    page = out.read_text(encoding="utf-8")

    assert "Alpha control" in page
    assert "unique alpha" in page and "visible" in page
    assert "unique beta" not in page and "hidden" not in page
    assert "1 department" in page


def test_output_is_self_contained_without_external_assets(tmp_path):
    page = _render(tmp_path, _full_rows())

    assert "<link" not in page
    assert "<script" not in page
    assert "<img" not in page
    assert "url(http" not in page
