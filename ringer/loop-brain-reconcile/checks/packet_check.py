#!/usr/bin/env python3
"""Validate a red-team lane: packet.json shape + findings.md substance/citations.

Prints WHY on every failure. Exit 0 only when all gates pass.
"""
import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED_KEYS = [
    "agent", "recommendation", "confidence", "evidence", "assumptions",
    "unique_catches", "risks_or_failure_modes", "proof_slots",
    "needs_human_or_owner_gate",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-catches", type=int, default=2)
    ap.add_argument("--min-words", type=int, default=400)
    ap.add_argument("--min-citations", type=int, default=5)
    ap.add_argument("--roots", nargs="+", required=True)
    args = ap.parse_args()

    failures = []

    pkt_path = Path("packet.json")
    if not pkt_path.is_file():
        print("FAIL: packet.json not found")
        return 1
    try:
        pkt = json.loads(pkt_path.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: packet.json does not parse: {e}")
        return 1

    for k in REQUIRED_KEYS:
        if k not in pkt:
            failures.append(f"packet.json missing key: {k}")
    if isinstance(pkt.get("recommendation"), str) and len(pkt["recommendation"].split()) < 10:
        failures.append("recommendation is under 10 words — too thin to act on")
    c = pkt.get("confidence")
    if not isinstance(c, (int, float)) or not 0 <= c <= 1:
        failures.append(f"confidence must be a number in [0,1], got: {c!r}")
    for k, floor in [("evidence", 3), ("risks_or_failure_modes", 3),
                     ("unique_catches", args.min_catches), ("proof_slots", 2)]:
        v = pkt.get(k)
        if not isinstance(v, list) or len(v) < floor:
            failures.append(f"{k} must be a list with >= {floor} items, got: "
                            f"{len(v) if isinstance(v, list) else type(v).__name__}")
    if not isinstance(pkt.get("needs_human_or_owner_gate"), bool):
        failures.append("needs_human_or_owner_gate must be a boolean")

    f_path = Path("findings.md")
    if not f_path.is_file():
        failures.append("findings.md not found")
    else:
        text = f_path.read_text(errors="replace")
        words = len(text.split())
        if words < args.min_words:
            failures.append(f"findings.md too thin: {words} words < {args.min_words}")
        tokens = set(re.findall(
            r"[A-Za-z0-9_][A-Za-z0-9_./\- ]*?\.(?:py|md|json|yaml|yml|sh|template|tmpl|toml|txt)\b",
            text))
        roots = [Path(r) for r in args.roots]
        resolved = set()
        for tok in tokens:
            tok = tok.strip().lstrip("(`'\"")
            for cand in (Path(tok), Path("/" + tok)):
                if cand.is_absolute():
                    if cand.is_file() and any(str(cand).startswith(str(r)) for r in roots):
                        resolved.add(str(cand))
                        break
                else:
                    hit = next((r / tok for r in roots if (r / tok).is_file()), None)
                    if hit:
                        resolved.add(str(hit))
                        break
        if len(resolved) < args.min_citations:
            failures.append(
                f"only {len(resolved)} cited paths resolve (need {args.min_citations}); "
                f"resolved: {', '.join(sorted(resolved)[:8]) or 'none'}")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"PASS: packet.json well-formed ({len(pkt.get('unique_catches', []))} catches, "
          f"confidence {c}), findings.md cited and substantive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
