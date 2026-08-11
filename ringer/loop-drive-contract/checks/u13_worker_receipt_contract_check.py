#!/usr/bin/env python3
"""U13 — hold a worker receipt to the mission contract before the loop trusts it.

The deadlock, measured live 2026-08-10. Three consecutive guest-acquisition runs
wrote `sends: 0` into the loop-drive-v1 block while listing candidates as
`eligible: true` with no disqualifier. The verdict computer refuses that shape,
correctly, so the run re-enters. The deterministic producer sits BEHIND the
re-entry loop, so it was never reached. Three re-entries burned 59.8 minutes of
worker budget against a 28-minute weekly cap and produced no draft, while the
funnel read zero on a day when there was exactly one person to write to.

Nothing was broken. Every component did its job. The worker and the verdict
computer simply disagreed about what the `sends` field MEANS: the worker read
its "do not send" boundary as "always write 0", and the runbook never defined
the field it was being asked to fill.

That is a contract defect, not a code defect, and a contract defect needs a
check or it comes back the next time someone rewrites a prompt.

Two violations, both fatal to driving:

  found-work-did-nothing  sends == 0 while a candidate is eligible with no
                          disqualifier. The loop can never exit re-entry.
  unaccounted-candidate   a candidate is neither counted in sends nor
                          disqualified with evidence. A zero nobody can audit.

Exit 0 PASS, 1 FAIL, 2 the receipt could not be read.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


BLOCK = re.compile(r"```loop-drive-v1\n(.*?)\n```", re.S)


def extract_block(receipt_text: str) -> dict:
    match = BLOCK.search(receipt_text)
    if not match:
        raise ValueError("receipt has no loop-drive-v1 block")
    return json.loads(match.group(1))


def violations(block: dict) -> list[str]:
    """Return every way this receipt would stall the loop. Empty means drivable."""
    found: list[str] = []
    sends = block.get("sends")
    if not isinstance(sends, int) or isinstance(sends, bool) or sends < 0:
        return [f"sends is {sends!r}; it must be a non-negative integer count of PROPOSALS"]

    candidates = block.get("candidates")
    if not isinstance(candidates, list):
        return [f"candidates is {type(candidates).__name__}, expected a list"]

    eligible_open = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            found.append(f"candidate {index} is not an object")
            continue
        alias = str(candidate.get("alias") or f"candidate-{index}")
        eligible = candidate.get("eligible")
        disqualifier = candidate.get("disqualifier")
        evidence = candidate.get("evidence")
        has_disqualifier = isinstance(disqualifier, str) and disqualifier.strip() != ""
        has_evidence = isinstance(evidence, str) and evidence.strip() != ""

        if eligible is True:
            eligible_open.append(alias)
            continue
        if not has_disqualifier:
            found.append(
                f"unaccounted-candidate: {alias} is not eligible and carries no "
                "disqualifier, so this zero cannot be audited")
        elif not has_evidence:
            found.append(
                f"unaccounted-candidate: {alias} is disqualified as "
                f"{disqualifier.strip()[:60]!r} with no evidence path to prove it")

    if sends == 0 and eligible_open:
        found.append(
            "found-work-did-nothing: sends is 0 while "
            f"{len(eligible_open)} candidate(s) are eligible with no disqualifier "
            f"({', '.join(eligible_open[:4])}). The verdict computer refuses this, the "
            "loop re-enters, and the producer behind that loop is never reached. "
            "`sends` counts PROPOSALS handed to the producer, not messages you sent.")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True, type=Path,
                        help="a worker receipt containing a loop-drive-v1 block")
    parser.add_argument("--expect", choices=("clean", "violation"), default="clean",
                        help="'violation' inverts the exit code, for watching it fail")
    args = parser.parse_args()

    try:
        block = extract_block(args.receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"u13_worker_receipt_contract_check: UNREADABLE — {exc}")
        return 2

    found = violations(block)
    if args.expect == "violation":
        if found:
            print("u13_worker_receipt_contract_check: PASS — the expected violation was "
                  "detected:")
            for line in found:
                print(f"  - {line}")
            return 0
        print("u13_worker_receipt_contract_check: FAIL — expected a contract violation "
              "and the receipt was clean; the check cannot see the defect it exists for")
        return 1

    if found:
        print("u13_worker_receipt_contract_check: FAIL — this receipt cannot drive the loop")
        for line in found:
            print(f"  - {line}")
        return 1
    print(f"u13_worker_receipt_contract_check: PASS — sends={block.get('sends')} and every "
          f"one of {len(block.get('candidates') or [])} candidate(s) is either counted or "
          "disqualified with evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
