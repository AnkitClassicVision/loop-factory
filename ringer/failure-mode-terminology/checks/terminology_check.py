#!/usr/bin/env python3
"""Executed check for a failure-mode terminology report.

The risk this check exists to catch is a model inventing authoritative-sounding
vocabulary — exactly what prompted the round (the coordinator coined "hollow
automation" and the owner asked whether it was real). So the check does not
grade prose quality. It enforces that every term is presented with the four
things that let a human decide whether it is real:

    TERM / DEFINITION / PROVENANCE (who named it, where it comes from) /
    FIT (why it does or does not describe our incident)

and that the report contains an explicit honesty section naming what it could
NOT find established vocabulary for. A report with no such section is claiming
total coverage, which is the failure being studied.

Usage: terminology_check.py --report <path> [--min-terms N]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIRED_SECTIONS = ("established terms", "no established", "mapping")
FIELD_PATTERNS = {
    "DEFINITION": re.compile(r"^\s*[-*]?\s*\**definition\**\s*[:\-]", re.I | re.M),
    "PROVENANCE": re.compile(r"^\s*[-*]?\s*\**(provenance|origin|source)\**\s*[:\-]", re.I | re.M),
    "FIT": re.compile(r"^\s*[-*]?\s*\**(fit|fits|applies)\**\s*[:\-]", re.I | re.M),
}
TERM_HEADING = re.compile(r"^#{2,4}\s+.*\S", re.M)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--min-terms", type=int, default=6)
    args = parser.parse_args()

    failures: list[str] = []
    if not args.report.is_file():
        # Workers write relative to their task directory; accept the report there
        # too rather than failing a good artifact over a path convention.
        alt = args.report.parent / args.report.stem / args.report.name
        if alt.is_file():
            args.report = alt
        else:
            print(f"CHECK FAIL: report missing at {args.report} and at {alt}")
            return 1
    text = args.report.read_text(encoding="utf-8", errors="replace")
    lower = text.lower()

    if len(text) < 1500:
        failures.append(f"CHECK FAIL: report is {len(text)} bytes; too thin to carry sourced terminology")

    for section in REQUIRED_SECTIONS:
        if section not in lower:
            failures.append(f"CHECK FAIL: report lacks a section mentioning {section!r}")

    for field, pattern in FIELD_PATTERNS.items():
        found = len(pattern.findall(text))
        if found < args.min_terms:
            failures.append(
                f"CHECK FAIL: only {found} {field} lines; every one of the {args.min_terms}+ terms "
                f"needs its own {field} line so a human can judge whether the term is real")

    headings = len(TERM_HEADING.findall(text))
    if headings < args.min_terms:
        failures.append(f"CHECK FAIL: {headings} term headings found, need at least {args.min_terms}")

    # An honesty section that is a single empty sentence defeats its purpose.
    idx = lower.find("no established")
    if idx != -1 and len(text[idx:idx + 400].strip()) < 120:
        failures.append("CHECK FAIL: the 'no established vocabulary' section is a stub; name what "
                        "you could not find a term for, or say plainly that everything mapped")

    if failures:
        print("\n".join(failures))
        return 1
    print(f"CHECK PASS: {headings} terms, each with definition, provenance and fit, plus an "
          f"explicit gap section ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
