from __future__ import annotations

import json

from factory.board import main, render_site


TS = "2026-08-02T16:00:00Z"


def _row(row_id, kind, department, **data):
    return {
        "id": row_id,
        "kind": kind,
        "ts": TS,
        "department": department,
        "data": data,
    }


def _feed(tmp_path, rows):
    path = tmp_path / "board-feed.ndjson"
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _rows():
    return [
        _row("alpha-status", "dept_status", "alpha", ok=True, epoch=3),
        _row("alpha-metric", "metrics", "alpha", group="signals", alpha_finding="alpha-only"),
        _row("alpha-andon", "andon", "alpha", fault="alpha fault", detail="alpha-only-andon"),
        _row("beta-status", "dept_status", "beta", ok=False, epoch=4),
        _row("beta-metric", "metrics", "beta", group="signals", beta_finding="beta-only"),
        _row("podcast-loop", "loop_status", "podcast", unit="podcast-daily.timer", last_result="success"),
        _row("seo-loop", "loop_status", "SEO Ops", unit="seo-refresh.timer", last_result="failure"),
        _row("seo-andon", "andon", "SEO Ops", fault="timer failed", detail="seo-only-andon"),
    ]


def _render(tmp_path, rows=None):
    feed = _feed(tmp_path, _rows() if rows is None else rows)
    site = tmp_path / "boards"
    render_site(feed, site)
    return feed, site


def test_render_site_writes_index_and_department_pages(tmp_path):
    _, site = _render(tmp_path)

    assert (site / "index.html").is_file()
    assert (site / "alpha.html").is_file()
    assert (site / "beta.html").is_file()


def test_render_site_writes_one_page_per_loop_group(tmp_path):
    _, site = _render(tmp_path)

    assert (site / "podcast.html").is_file()
    assert (site / "seo-ops.html").is_file()


def test_slugging_handles_spaces_and_odd_characters(tmp_path):
    rows = [_row("odd", "dept_status", "Sales & Ops!", ok=True)]
    _, site = _render(tmp_path, rows)

    assert (site / "sales---ops-.html").is_file()


def test_slug_collisions_do_not_overwrite_tab_pages(tmp_path):
    rows = [
        _row("one", "dept_status", "A B", ok=True),
        _row("two", "dept_status", "A@B", ok=True),
    ]
    _, site = _render(tmp_path, rows)

    assert (site / "a-b.html").is_file()
    assert (site / "a-b-2.html").is_file()


def test_every_site_page_links_to_estate_and_all_tabs(tmp_path):
    _, site = _render(tmp_path)
    expected = ('href="index.html"', 'href="alpha.html"', 'href="beta.html"', 'href="podcast.html"', 'href="seo-ops.html"')

    for page_path in site.glob("*.html"):
        page = page_path.read_text(encoding="utf-8")
        assert '<nav class="tabs" aria-label="Board tabs">' in page
        assert all(link in page for link in expected)


def test_current_tab_is_marked_only_on_its_own_page(tmp_path):
    _, site = _render(tmp_path)
    alpha = (site / "alpha.html").read_text(encoding="utf-8")
    beta = (site / "beta.html").read_text(encoding="utf-8")
    index = (site / "index.html").read_text(encoding="utf-8")

    assert '<a class="tab active" href="alpha.html" aria-current="page">alpha</a>' in alpha
    assert '<a class="tab" href="beta.html">beta</a>' in alpha
    assert '<a class="tab active" href="beta.html" aria-current="page">beta</a>' in beta
    assert '<a class="tab" href="alpha.html">alpha</a>' in beta
    assert '<a class="tab active" href="index.html" aria-current="page">Estate</a>' in index


def test_department_page_excludes_other_department_content(tmp_path):
    _, site = _render(tmp_path)
    alpha = (site / "alpha.html").read_text(encoding="utf-8")

    assert "alpha-only" in alpha
    assert "alpha-only-andon" in alpha
    assert "beta-only" not in alpha
    assert "seo-only-andon" not in alpha


def test_group_page_shows_loop_status_roster_and_andon(tmp_path):
    _, site = _render(tmp_path)
    seo = (site / "seo-ops.html").read_text(encoding="utf-8")

    assert "seo-refresh" in seo
    assert "seo-only-andon" in seo
    assert "podcast-daily" not in seo


def test_index_page_shows_all_departments_and_loop_groups(tmp_path):
    _, site = _render(tmp_path)
    index = (site / "index.html").read_text(encoding="utf-8")

    for marker in ("alpha-only", "beta-only", "podcast-daily", "seo-refresh", "seo-only-andon"):
        assert marker in index


def test_out_mode_still_renders_single_page(tmp_path):
    feed = _feed(tmp_path, _rows())
    output = tmp_path / "single.html"

    assert main(["--feed", str(feed), "--out", str(output), "--department", "alpha"]) == 0
    page = output.read_text(encoding="utf-8")
    assert "alpha-only" in page
    assert "beta-only" not in page
    assert '<nav class="tabs"' not in page


def test_two_site_renders_are_byte_identical(tmp_path):
    feed = _feed(tmp_path, list(reversed(_rows())))
    first = tmp_path / "first"
    second = tmp_path / "second"

    render_site(feed, first)
    render_site(feed, second)

    assert sorted(path.name for path in first.iterdir()) == sorted(path.name for path in second.iterdir())
    for first_path in first.iterdir():
        assert first_path.read_bytes() == (second / first_path.name).read_bytes()
