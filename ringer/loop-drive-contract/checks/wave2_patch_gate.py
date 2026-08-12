#!/usr/bin/env python3
"""Acceptance gate for an exported wave-2 patch.

Used as the Ringer `check` for the local-shell verification lanes, so the green
receipt attests to the ARTIFACT, not merely to a command having run. Prints WHY
on every failure.

Asserts: the patch exists and is non-empty; every file it touches is in the
allowed set; each required marker appears in the patch text; the exported
summary carries all five sections.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SECTIONS = ("# fix summary", "## summary", "## files changed", "## verification", "## assumptions")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--allowed", action="append", required=True)
    parser.add_argument("--require", action="append", default=[])
    args = parser.parse_args()

    failures: list[str] = []

    if not args.patch.is_file() or args.patch.stat().st_size == 0:
        failures.append(f"CHECK FAIL: patch missing or empty at {args.patch}")
    else:
        text = args.patch.read_text(encoding="utf-8", errors="replace")
        touched = set(re.findall(r"^diff --git a/(\S+)", text, re.M))
        if not touched:
            failures.append("CHECK FAIL: patch carries no diff headers")
        outside = touched - set(args.allowed)
        if outside:
            failures.append(f"CHECK FAIL: patch touches files outside ownership: {sorted(outside)}")
        for marker in args.require:
            if marker.lower() not in text.lower():
                failures.append(f"CHECK FAIL: patch lacks the required marker {marker!r}; "
                                "the contract's mechanism is not implemented")

    if not args.summary.is_file():
        failures.append(f"CHECK FAIL: exported summary missing at {args.summary}")
    else:
        body = args.summary.read_text(encoding="utf-8", errors="replace").lower()
        missing = [s for s in SECTIONS if s not in body]
        if missing:
            failures.append(f"CHECK FAIL: summary missing sections: {missing}")

    if failures:
        print("\n".join(failures))
        return 1
    print(f"CHECK PASS: {args.patch.name} confined to {args.allowed}, "
          f"required markers present, summary complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
