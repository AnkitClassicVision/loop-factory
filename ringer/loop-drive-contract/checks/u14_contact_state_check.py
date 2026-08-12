#!/usr/bin/env python3
"""U14 — the owner's contact-state authority model, proven by execution.

Ankit's ruling, 2026-08-10: actual data wins; ledgers are recordings and second
order; Gmail is first order unless the contact happened outside Gmail; a phone
call captured by Bee after a Gmail thread is the later truth; timestamps
arbitrate; every ledger row needs grounding.

The case he named himself is case 2 below, and it is the one a naive
source-ranking gets wrong: rank Gmail above Bee and a system will tell you a
prospect is untouched on the day after you spoke to them on the phone.

Seven cases, each driven through the real resolver:

  1  gmail alone                          -> CONTACTED via gmail
  2  gmail Monday, bee call Tuesday       -> CONTACTED via BEE, the later one
  3  bee Monday, gmail Tuesday            -> CONTACTED via gmail, symmetry
  4  ledger says contacted, no grounding  -> ignored, recorded as a discrepancy
  5  ledger grounded in gmail             -> gmail still resolves it
  6  nothing found, bee unreachable       -> UNKNOWN, never NO_CONTACT_FOUND
  7  nothing found, everything reached    -> NO_CONTACT_FOUND, cold path clear

Case 6 is the one that decides whether this whole step is an improvement. A
reconcile that reaches three of four sources, finds nothing, and reports "no
prior contact" is worse than no reconcile at all: it launders absence of
evidence into evidence of absence and hands it over with an authoritative
receipt. Only case 7 may clear a cold open.

Exit 0 PASS, 1 FAIL.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("/mnt/d_drive/repos/podcast"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.repo.resolve()))

    from server.pipeline.contact_state import (
        CONTACTED, NO_CONTACT_FOUND, UNKNOWN, Observation, SourceCoverage, resolve,
    )

    def when(day: int, hour: int = 9) -> datetime:
        return datetime(2026, 8, day, hour, tzinfo=timezone.utc)

    def all_reached(*, bee: bool = True) -> list:
        return [
            SourceCoverage("gmail", True, when(10)),
            SourceCoverage("linkedin", True, when(10)),
            SourceCoverage("bee", bee, when(10) if bee else None,
                           "" if bee else "bee CLI unavailable this run"),
        ]

    failures: list[str] = []

    def expect(name: str, observations, coverage, want_verdict, want_source=None,
               want_safe=None):
        got = resolve(observations, coverage)
        if got.verdict != want_verdict:
            failures.append(f"{name}: expected {want_verdict}, got {got.verdict} — {got.reason}")
            return got
        if want_source is not None and got.last_touch_source != want_source:
            failures.append(
                f"{name}: expected the winning source to be {want_source!r}, got "
                f"{got.last_touch_source!r} — {got.reason}")
        if want_safe is not None and got.safe_for_cold_open is not want_safe:
            failures.append(
                f"{name}: safe_for_cold_open is {got.safe_for_cold_open}, expected {want_safe}")
        return got

    # 1. The simple case.
    expect("gmail-alone",
           [Observation("gmail", when(5), evidence_ref="thread:abc")],
           all_reached(), CONTACTED, "gmail", False)

    # 2. THE CASE ANKIT NAMED. Email first, then a phone call Bee recorded.
    # A system that ranks Gmail over Bee reports the Gmail date and looks
    # confident while being a day stale on a conversation that actually happened.
    expect("bee-call-after-gmail",
           [Observation("gmail", when(5), evidence_ref="thread:abc"),
            Observation("bee", when(6), evidence_ref="bee:conversation-ref")],
           all_reached(), CONTACTED, "bee", False)

    # 3. Symmetry. The rule is "latest", not "Bee is special".
    expect("gmail-after-bee-call",
           [Observation("bee", when(5), evidence_ref="bee:conversation-ref"),
            Observation("gmail", when(6), evidence_ref="thread:def")],
           all_reached(), CONTACTED, "gmail", False)

    # 4. An ungrounded recording cannot establish contact — and cannot be waved
    # away into a clean bill either. It is a contradiction, so it HOLDS.
    # Clearing here would repeat the 2026-08-10 incident with the polarity
    # flipped: a human-entered claim of contact overruled by an empty
    # structured read, and a cold open sent to someone mid-conversation.
    ungrounded = expect("ledger-ungrounded-holds",
                        [Observation("funnel_ledger", when(4))],
                        all_reached(), UNKNOWN, None, False)
    if not ungrounded.discrepancies:
        failures.append(
            "ledger-ungrounded-holds: the claim was dropped without being recorded as a "
            "discrepancy; the drift becomes invisible to a human")

    # 4b. Grounded but uncorroborated is the same contradiction. This one is
    # sneakier: the grounding makes it LOOK verified, so an implementation that
    # only checks for the presence of a grounding string clears it.
    expect("ledger-grounded-but-uncorroborated-holds",
           [Observation("funnel_ledger", when(4), grounding="thread:missing")],
           all_reached(), UNKNOWN, None, False)

    # 5. A grounded recording corroborates; the first-order source still resolves.
    expect("ledger-grounded-by-gmail",
           [Observation("gmail", when(5), evidence_ref="thread:abc"),
            Observation("funnel_ledger", when(5), grounding="thread:abc")],
           all_reached(), CONTACTED, "gmail", False)

    # 6. THE SAFETY CASE. Nothing found, but Bee was unreachable.
    blind = expect("unreached-source-blocks-clean-bill",
                   [], all_reached(bee=False), UNKNOWN, None, False)
    if "bee" not in blind.unreached:
        failures.append("unreached-source-blocks-clean-bill: bee was not reported unreached")

    # 7. Full coverage, genuinely nothing. The only state that clears a cold open.
    expect("full-coverage-genuine-zero", [], all_reached(), NO_CONTACT_FOUND, None, True)

    if failures:
        print("u14_contact_state_check: FAIL")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("u14_contact_state_check: PASS — latest first-order observation wins across "
          "gmail/linkedin/bee, a Bee-recorded call after a Gmail thread is the current "
          "truth, an uncorroborated ledger claim holds rather than clearing, and an "
          "unreachable source yields UNKNOWN instead of a clean bill of health")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
