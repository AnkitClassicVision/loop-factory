#!/usr/bin/env python3
"""Executable check for enrichment-model-bakeoff lanes.

Validates ./results.json (in the task cwd) against the frozen packet set:
  - exactly one result per packet pid, no extras, no dupes
  - contact_role from the 6-value enum
  - confidence is a number in [0, 1]
  - evidence is a VERBATIM quote from that packet (whitespace/case tolerant),
    or the literal "insufficient evidence" only when role is Unknown
Prints every violation with its pid; exit 1 on any violation.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import os

PACKETS_PATH = Path(
    os.environ.get("BAKEOFF_PACKETS")
    or "/mnt/d_drive/repos/loop-factory/ringer/enrichment-model-bakeoff/packets/packets.json"
)
RESULTS_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "results.json")
ROLES = {"Decision Maker", "Gatekeeper", "Influencer", "Champion", "End User", "Unknown"}
MAX_EVIDENCE = 300
MAX_REASONING = 400


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def main() -> int:
    packets = {p["pid"]: p for p in json.loads(PACKETS_PATH.read_text())["packets"]}
    problems = []
    try:
        doc = json.loads(RESULTS_PATH.read_text())
    except FileNotFoundError:
        print(f"FAIL: {RESULTS_PATH} not found in task dir")
        return 1
    except ValueError as exc:
        print(f"FAIL: results.json is not valid JSON: {exc}")
        return 1

    rows = doc.get("results")
    if not isinstance(rows, list):
        print('FAIL: top-level shape must be {"results": [...]}')
        return 1

    seen = set()
    for row in rows:
        pid = str(row.get("pid", "?"))
        if pid not in packets:
            problems.append(f"{pid}: pid not in packet set")
            continue
        if pid in seen:
            problems.append(f"{pid}: duplicate result")
            continue
        seen.add(pid)
        role = row.get("contact_role")
        if role not in ROLES:
            problems.append(f"{pid}: contact_role {role!r} not in {sorted(ROLES)}")
        confidence = row.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            problems.append(f"{pid}: confidence {confidence!r} not a number in [0,1]")
        evidence = str(row.get("evidence") or "")
        if not evidence:
            problems.append(f"{pid}: evidence missing")
        elif len(evidence) > MAX_EVIDENCE:
            problems.append(f"{pid}: evidence longer than {MAX_EVIDENCE} chars")
        elif norm(evidence) == "insufficient evidence":
            if role != "Unknown":
                problems.append(f"{pid}: 'insufficient evidence' only allowed with Unknown")
            elif int(packets[pid].get("activity_count") or 0) >= 3:
                # degenerate-pass guard (r1 lesson): a packet with real activity
                # always has something quotable — even an honest Unknown must
                # quote the ambiguous evidence it read
                problems.append(
                    f"{pid}: packet has activity_count>=3; evidence must be a "
                    f"verbatim quote, not 'insufficient evidence'"
                )
        else:
            packet = packets[pid]
            haystack = norm(
                " | ".join(
                    str(packet.get(k, ""))
                    for k in ("activity_summary", "jobtitle", "company", "lifecyclestage")
                )
            )
            if norm(evidence) not in haystack:
                problems.append(f"{pid}: evidence is not a verbatim quote from the packet")
        reasoning = str(row.get("reasoning") or "")
        if len(reasoning) > MAX_REASONING:
            problems.append(f"{pid}: reasoning longer than {MAX_REASONING} chars")

    missing = sorted(set(packets) - seen)
    if missing:
        problems.append(f"missing results for: {', '.join(missing)}")

    if problems:
        print(f"FAIL ({len(problems)} violations):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"PASS: {len(seen)} results valid against {len(packets)} packets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
