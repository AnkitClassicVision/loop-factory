#!/usr/bin/env python3
"""Validate a read-only scout report against its frozen evidence packet.

Contract (also stated in the worker spec):
  - report.md exists, is substantive, and has the mode's required sections.
  - The packet was NOT modified (sha256 manifest recomputed and compared).
  - Every `QUOTE: <text> (packet/<path>)` line is a verbatim substring of the
    named packet file — the anti-hallucination gate.
  - Mode `diagnosis` additionally requires ROOT CAUSE: and PROPOSED FIX: lines
    and >=4 packet/ path citations.
  - Mode `ledger` additionally requires a strict `SINGLE_LEDGER: yes|no` line
    and >=2 distinct ANK-<n> card ids that really occur in the ledger excerpt.

Every failure prints WHY, so the retry prompt teaches the worker.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

SECTIONS = {
    "diagnosis": ["root cause", "evidence chain", "what still works",
                  "proposed minimal fix", "confidence"],
    "ledger": ["verdict", "writers", "re-arm coverage", "gaps",
               "evidence quotes"],
}


def fail(msg: str) -> None:
    print("FAIL: " + msg)
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--packet", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--mode", required=True, choices=sorted(SECTIONS))
    args = ap.parse_args()

    report_path = Path(args.report)
    packet = Path(args.packet)
    if not report_path.is_file():
        fail(f"{args.report} does not exist. The report is the only deliverable.")
    text = report_path.read_text(errors="replace")
    if len(text) < 1500:
        fail(f"report is only {len(text)} chars; a real diagnosis is longer. "
             "Write the full report per the OUTPUT CONTRACT in the spec.")

    # ---- packet integrity: the scout is read-only -------------------------
    sha_file = Path(args.sha)
    if not sha_file.is_file():
        fail(f"sha manifest missing: {args.sha} (harness setup problem, not the worker)")
    tampered = []
    for line in sha_file.read_text().splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        p = Path(rel)
        if not p.is_file():
            tampered.append(f"{rel}: deleted")
            continue
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual != digest.strip():
            tampered.append(f"{rel}: modified")
    if tampered:
        fail("the packet is read-only but these files changed: " + ", ".join(tampered))

    # ---- sections ---------------------------------------------------------
    lower = text.lower()
    missing = [s for s in SECTIONS[args.mode]
               if not re.search(r"^#{1,4}\s*.*" + re.escape(s), lower, re.M)]
    if missing:
        fail("missing required section heading(s): " + ", ".join(missing) +
             ". Use the exact section names from the OUTPUT CONTRACT.")

    # ---- QUOTE lines: verbatim evidence -----------------------------------
    quotes = re.findall(r"^QUOTE:\s*(.+?)\s*\((packet/[^)]+)\)\s*$", text, re.M)
    if len(quotes) < 3:
        fail(f"only {len(quotes)} well-formed QUOTE lines found; need >=3. "
             "Format: QUOTE: <one verbatim line> (packet/<relative-path>)")
    for raw, rel in quotes:
        q = raw.strip().strip("`\"'").strip()
        if len(q) < 8:
            fail(f"QUOTE too short to be evidence: {q!r}")
        src = Path(rel)
        if not src.is_file():
            fail(f"QUOTE cites a file that is not in the packet: {rel}")
        if q not in src.read_text(errors="replace"):
            fail(f"QUOTE is not a verbatim substring of {rel}: {q!r}. "
                 "Copy the line byte-for-byte from the packet file.")

    # ---- mode-specific substance ------------------------------------------
    if args.mode == "diagnosis":
        if not re.search(r"^ROOT CAUSE:\s*\S+", text, re.M):
            fail("no non-empty 'ROOT CAUSE:' line. Name the mechanism on that line.")
        if not re.search(r"^PROPOSED FIX:\s*\S+", text, re.M):
            fail("no non-empty 'PROPOSED FIX:' line.")
        cites = len(re.findall(r"packet/[A-Za-z0-9_\-./]+", text))
        if cites < 4:
            fail(f"only {cites} packet/ path citations; need >=4. Every claim "
                 "about code or logs cites its packet file.")
    else:  # ledger
        verdicts = re.findall(r"^SINGLE_LEDGER:\s*(yes|no)\s*$", text, re.M)
        if len(verdicts) != 1:
            fail("need exactly one line 'SINGLE_LEDGER: yes' or 'SINGLE_LEDGER: no' "
                 f"(found {len(verdicts)}).")
        cards = set(re.findall(r"\bANK-\d+\b", text))
        ledger_text = (packet / "card-ledger-tail.jsonl").read_text(errors="replace")
        real = {c for c in cards if c in ledger_text}
        if len(real) < 2:
            fail(f"need >=2 card ids cited from packet/card-ledger-tail.jsonl as "
                 f"worked examples; found {len(real)} that actually occur there "
                 f"(cited: {sorted(cards) or 'none'}).")

    print(f"PASS: {args.mode} report validated — {len(quotes)} verbatim quotes, "
          f"packet untouched, sections complete.")


if __name__ == "__main__":
    main()
