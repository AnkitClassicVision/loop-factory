#!/usr/bin/env python3
"""Validate a one-true-master review report.

Strict on substance, tolerant on format. The report must:

  1. exist and carry real analysis (length floor),
  2. cite at least N distinct <file>:<line> locations,
  3. have EVERY citation resolve to a real file at a real line in the
     reviewed source tree (this is what catches invented findings),
  4. mention every required subject the lane was asked about.

Every failure prints exactly what broke and the offending values, because
the failure text is what the retry prompt gets.

Usage:
  review_check.py REPORT SOURCE_ROOT MIN_CITATIONS KEYWORD [KEYWORD ...]
"""
import os
import re
import sys

CITATION = re.compile(r"([A-Za-z0-9_./\-]+\.py):(\d+)")
MIN_CHARS = 800


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def main():
    if len(sys.argv) < 5:
        fail(
            "checker misuse: expected REPORT SOURCE_ROOT MIN_CITATIONS KEYWORD..., got "
            + repr(sys.argv[1:])
        )

    report_path = sys.argv[1]
    source_root = sys.argv[2]
    try:
        min_citations = int(sys.argv[3])
    except ValueError:
        fail("checker misuse: MIN_CITATIONS is not an integer: " + repr(sys.argv[3]))
    keywords = sys.argv[4:]

    if not os.path.isdir(source_root):
        fail("source tree does not exist, cannot verify citations: " + source_root)

    if not os.path.isfile(report_path):
        siblings = []
        parent = os.path.dirname(os.path.abspath(report_path)) or "."
        if os.path.isdir(parent):
            siblings = sorted(os.listdir(parent))[:25]
        fail(
            "report was never written: %s\nfiles present in %s: %s"
            % (report_path, parent, siblings or "(none)")
        )

    with open(report_path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    if len(text) < MIN_CHARS:
        fail(
            "report is %d chars, below the %d floor. This lane asks for a real map of a "
            "timing surface; a stub does not answer it.\n--- report ---\n%s"
            % (len(text), MIN_CHARS, text[:600])
        )

    # ---- citations resolve to real lines -------------------------------
    raw = CITATION.findall(text)
    seen = []
    for path, line in raw:
        key = (path, int(line))
        if key not in seen:
            seen.append(key)

    if len(seen) < min_citations:
        fail(
            "found %d distinct <file>:<line> citations, need at least %d. "
            "Findings must be anchored to real code.\nfound: %s"
            % (len(seen), min_citations, seen or "(none)")
        )

    # Build a filename index so a worker may cite either a repo-relative
    # path or a bare filename without being punished for the choice.
    index = {}
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__"}]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, source_root)
            index.setdefault(rel, full)
            index.setdefault(name, full)

    line_counts = {}
    bad_file = []
    bad_line = []
    for path, line in seen:
        target = index.get(path)
        if target is None:
            target = index.get(os.path.basename(path))
        if target is None:
            bad_file.append("%s:%d" % (path, line))
            continue
        if target not in line_counts:
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                line_counts[target] = sum(1 for _ in handle)
        if line < 1 or line > line_counts[target]:
            bad_line.append(
                "%s:%d (file has %d lines)" % (path, line, line_counts[target])
            )

    if bad_file:
        fail(
            "these citations name files that do not exist under %s: %s\n"
            "Cite real paths from the tree you were given."
            % (source_root, ", ".join(bad_file))
        )

    if bad_line:
        fail(
            "these citations point past the end of the file: %s\n"
            "A line number that cannot exist means the finding was not read off the code."
            % ", ".join(bad_line)
        )

    # ---- required subjects ---------------------------------------------
    lowered = text.lower()
    missing = [word for word in keywords if word.lower() not in lowered]
    if missing:
        fail(
            "the report never mentions these required subjects: %s\n"
            "Each one is part of what this lane was asked to map."
            % ", ".join(missing)
        )

    print(
        "PASS: %d chars, %d distinct citations, all resolve to real lines, "
        "all %d required subjects covered."
        % (len(text), len(seen), len(keywords))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
