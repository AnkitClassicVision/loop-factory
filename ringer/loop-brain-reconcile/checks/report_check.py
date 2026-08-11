#!/usr/bin/env python3
"""Validate a research-lane report: sections present, substance, real citations.

Fails loudly with WHY: missing sections named, word count shown, citation
resolution listed. Exit 0 only when every gate passes.
"""
import argparse
import re
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--sections", required=True,
                    help="semicolon-separated section names, matched case-insensitively")
    ap.add_argument("--min-words", type=int, default=700)
    ap.add_argument("--min-citations", type=int, default=8)
    ap.add_argument("--roots", nargs="+", required=True,
                    help="repo roots that cited relative paths may resolve against")
    args = ap.parse_args()

    p = Path(args.report)
    if not p.is_file():
        print(f"FAIL: report file not found: {p}")
        return 1
    text = p.read_text(errors="replace")

    failures = []

    # 1. Sections — case-insensitive substring, tolerant of markdown/format.
    low = text.lower()
    missing = [s for s in args.sections.split(";") if s.strip().lower() not in low]
    if missing:
        failures.append("missing required sections: " + "; ".join(missing))

    # 2. Substance.
    words = len(text.split())
    if words < args.min_words:
        failures.append(f"too thin: {words} words < required {args.min_words}")

    # 3. Citations must resolve to real files in the allowed roots.
    tokens = set(re.findall(
        r"[A-Za-z0-9_][A-Za-z0-9_./\- ]*?\.(?:py|md|json|yaml|yml|sh|template|tmpl|toml|txt)\b",
        text))
    roots = [Path(r) for r in args.roots]
    resolved = set()
    for tok in tokens:
        tok = tok.strip().lstrip("(`'\"")
        # The regex starts at an alphanumeric, so absolute paths arrive with the
        # leading "/" stripped ("mnt/d_drive/..."). Try both spellings.
        candidates = [Path(tok), Path("/" + tok)]
        hit = None
        for cand in candidates:
            if cand.is_absolute():
                if cand.is_file() and any(str(cand).startswith(str(r)) for r in roots):
                    hit = str(cand)
                    break
            else:
                for r in roots:
                    if (r / tok).is_file():
                        hit = str(r / tok)
                        break
            if hit:
                break
        if hit:
            resolved.add(hit)
    if len(resolved) < args.min_citations:
        failures.append(
            f"only {len(resolved)} cited paths resolve to real files "
            f"(need {args.min_citations}). Resolved: "
            + (", ".join(sorted(resolved)[:10]) or "none")
            + f". Raw path-like tokens seen: {len(tokens)}")

    # 4. No secret files quoted.
    leaks = re.findall(r"credentials\.json|authorized_user\.json|\.env\b|token\.json",
                       text, flags=re.I)
    if leaks:
        failures.append(f"report references forbidden secret files: {sorted(set(leaks))}")

    if failures:
        print(f"FAIL ({p.name}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"PASS ({p.name}): {words} words, {len(resolved)} resolved citations, "
          f"all sections present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
