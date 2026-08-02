#!/usr/bin/env python3
"""Executed check for the estate-andon-board design task.

Validates substance, tolerant on format:
  - board.html parses, is self-contained (no external http(s) assets),
    contains inline SVG graphs, and covers the four required content zones.
  - design-spec.md exists with real sections.
Prints WHY on every failure. Exit 0 = PASS.
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

FAILS = []


def fail(msg):
    FAILS.append(msg)


def main():
    task_dir = Path(".")
    board = task_dir / "board.html"
    spec = task_dir / "design-spec.md"

    if not board.exists():
        fail("board.html missing")
    if not spec.exists():
        fail("design-spec.md missing")
    if FAILS:
        report()

    html = board.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", " ", html).lower()

    class P(HTMLParser):
        def error(self, message):
            fail(f"board.html parse error: {message}")

    try:
        P().feed(html)
    except Exception as e:  # noqa: BLE001
        fail(f"board.html failed to parse: {e}")

    # self-contained: no external fetches
    ext = re.findall(r'(?:src|href)\s*=\s*["\'](https?://[^"\']+)', html, re.I)
    ext = [u for u in ext if not u.startswith("https://linear.app")]  # card links out are content, not assets
    bad = [u for u in ext if re.search(r"\.(css|js|png|jpg|jpeg|svg|woff2?)([?#]|$)", u, re.I) or "fonts." in u or "cdn" in u]
    if bad:
        fail(f"external assets found (must be self-contained): {bad[:5]}")

    # graphs: inline svg (charts), at least 3 distinct svg elements
    svg_count = len(re.findall(r"<svg\b", html, re.I))
    if svg_count < 3:
        fail(f"expected >=3 inline <svg> graphs, found {svg_count} — an ops metrics board needs actual graphs")

    # required content zones (case-insensitive, flexible phrasing)
    zones = {
        "goal-progress zone": r"(goal|% to goal|progress|burn.?in|relative to)",
        "status/where-we-are zone": r"(status|where we are|state|department|lane)",
        "errors/issues zone": r"(error|issue|failure|blocked|andon|alert)",
        "metrics/indicators zone": r"(metric|rate|token|runs|first.?try|pass)",
    }
    for name, pat in zones.items():
        if not re.search(pat, text):
            fail(f"missing {name}: no match for /{pat}/ in rendered text")

    # synthetic-data honesty: page must label itself prototype/synthetic
    if not re.search(r"(synthetic|prototype|sample data)", text):
        fail("board must visibly label itself as prototype/synthetic data")

    # design spec substance
    spec_txt = spec.read_text(encoding="utf-8", errors="replace")
    sections = re.findall(r"^#{1,3}\s+\S", spec_txt, re.M)
    if len(sections) < 4:
        fail(f"design-spec.md has {len(sections)} headed sections, need >=4 (layout, hierarchy, indicators/graphs, color)")
    for topic, pat in {
        "layout rationale": r"(layout|grid|structure)",
        "visual hierarchy": r"(hierarch|priorit|first glance|scan)",
        "indicator/graph choices": r"(graph|chart|indicator|sparkline|gauge|bar)",
    }.items():
        if not re.search(pat, spec_txt, re.I):
            fail(f"design-spec.md missing {topic} (no match for /{pat}/)")

    report()


def report():
    if FAILS:
        print("BOARD CHECK: FAIL")
        for f in FAILS:
            print(f"  - {f}")
        sys.exit(1)
    print("BOARD CHECK: PASS — self-contained, >=3 svg graphs, all four zones present, spec substantive")
    sys.exit(0)


if __name__ == "__main__":
    main()
