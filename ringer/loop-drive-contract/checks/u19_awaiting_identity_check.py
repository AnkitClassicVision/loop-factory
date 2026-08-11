#!/usr/bin/env python3
"""U19 executed check: a candidate with no email is a queue, not a discard.

Measured in production on 2026-08-11: `considered 14, selected 0`, and 13 of
those dropped as "record has no usable email address". Those 13 are real people
the intake stage scored and kept. They were being discarded for lacking a field
their intended channel does not need — the charter routes a COLD candidate to
LinkedIn or a postcard, and this repo names the lane
`cold_linkedin_dms_a3_a4_via_unipile_thanks_io_postcards`.

Owner decision, Ankit 2026-08-11: route cold candidates to LinkedIn.

This is the first step of that and it deliberately touches nothing that sends.
A cold candidate with no email is no longer collapsed into the same drop bucket
as a record with a malformed address. It is reported as awaiting a LinkedIn
identity, with the alias and the fact that a name is all we have — a work queue
someone can act on, instead of a number that reads as "nobody was any good".

What the check refuses:

  1. Losing them. Every considered record must still be accounted for.
  2. Putting them on the send path. They carry no address and no resolved
     profile; a candidate the producer could act on must never come out of here
     without something to send to.
  3. Promoting a WARM candidate this way. Warm means email or text by charter;
     a warm candidate with no email is a genuine data gap, not a LinkedIn
     candidate.
  4. Naming them. The report is receipts-grade: aliases only.

Usage: u19_awaiting_identity_check.py --worktree <tree> [--owned PATH ...]
                                      [--patch OUT] [--summary fix-summary.md]
                                      [--exported-summary OUT]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")

COLD_NAME = "Ada Lovelace"          # no warm marker in the note
WARM_NAME = "Grace Hopper"          # referral, so warm by charter
EMAILED_NAME = "Katherine Johnson"  # has an address, must be unaffected
ADDRESS = "katherine@example.invalid"

FAILURES: list[str] = []


def fail(where: str, why: str) -> None:
    FAILURES.append(f"FAIL [{where}]: {why}")


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def load(worktree: Path, relative: str, name: str):
    path = worktree / relative
    if not path.is_file():
        fail("missing_file", f"{relative} does not exist")
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail("unimportable", f"{relative} could not be loaded")
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        fail("import_error", f"{relative} raised {exc.__class__.__name__}: {exc}")
        return None
    return module


class CleanIndex:
    """Every channel read, nobody known. The gate is not what is under test."""

    def __init__(self, contact_state):
        self._contact_state = contact_state
        self.coverage = [
            contact_state.SourceCoverage(source=source, reached=True,
                                         checked_at=datetime.now(timezone.utc))
            for source in ("gmail", "linkedin", "bee", "hubspot")]
        self.observations: dict = {}

    def resolve(self, alias, extra_observations=()):
        return self._contact_state.resolve(list(extra_observations), self.coverage)


def check(worktree: Path, contact_state) -> None:
    feeder = load(worktree, "scripts/guest_candidate_feed.py", "u19_guest_candidate_feed")
    if feeder is None:
        return

    inbox = {"candidates": [
        # Cold: nothing in the note marks it warm, and no address.
        {"name": COLD_NAME, "email": None, "fit_score": 9,
         "source": "guest-acquisition-receipt", "note": "multi-location practice, strong operator"},
        # Warm by referral, but also no address. This is a data gap, not LinkedIn.
        {"name": WARM_NAME, "email": None, "fit_score": 8,
         "source": "guest-acquisition-receipt", "note": "referred by a past guest"},
        # Reachable today, and must be entirely unaffected by this change.
        {"name": EMAILED_NAME, "email": ADDRESS, "fit_score": 7,
         "source": "guest-acquisition-receipt", "note": "referred by a past guest"},
    ]}
    ledger = {"people": [
        {"name": name, "stage": "new_inbound", "last_touch": {"at": None}}
        for name in (COLD_NAME, WARM_NAME, EMAILED_NAME)]}

    try:
        selected, report = feeder.build_candidates(
            inbox, ledger, date(2026, 8, 11), 5, contact_index=CleanIndex(contact_state))
    except Exception as exc:  # noqa: BLE001
        fail("feeder_raised", f"{exc.__class__.__name__}: {exc}")
        return

    cold_alias = feeder.alias_for(feeder.normalized_name(COLD_NAME))
    warm_alias = feeder.alias_for(feeder.normalized_name(WARM_NAME))
    email_alias = feeder.alias_for(feeder.normalized_name(EMAILED_NAME))

    # 1. The reachable candidate is untouched.
    if [entry.get("alias") for entry in selected] != [email_alias]:
        fail("send_path_changed",
             f"the only candidate with an address should still be the only one selected; got "
             f"{[entry.get('alias') for entry in selected]}")

    # 2. Nothing on the send path without something to send to.
    for entry in selected:
        if not str(entry.get("to") or "").strip():
            fail("unsendable_selected",
                 f"{entry.get('alias')} reached the producer with no destination. A candidate "
                 "the producer can act on must always carry one")

    awaiting = report.get("awaiting_identity")
    if not isinstance(awaiting, list):
        fail("no_queue",
             "the report has no awaiting_identity list, so 13 real people in production remain a "
             "number in a drop bucket rather than a queue anybody can work")
        return

    aliases = {entry.get("alias") for entry in awaiting if isinstance(entry, dict)}

    # 3. The cold one is queued for LinkedIn.
    if cold_alias not in aliases:
        fail("cold_not_queued",
             f"the cold candidate with no address is not in awaiting_identity: {sorted(aliases)}")
    else:
        entry = next(e for e in awaiting if e.get("alias") == cold_alias)
        blob = json.dumps(entry).lower()
        if "linkedin" not in blob:
            fail("channel_unnamed",
                 f"the queued entry does not say which channel it is waiting on: {entry!r}")

    # 4. The warm one is NOT — warm means email or text by charter.
    if warm_alias in aliases:
        fail("warm_queued_as_linkedin",
             "a warm candidate with no email was queued for LinkedIn. Warm routes to email or "
             "text; a warm record with no address is a data gap to fix at intake, and treating it "
             "as a LinkedIn prospect quietly rewrites the channel rule")

    # 5. Everybody is still accounted for.
    dropped = report.get("dropped", [])
    covered = aliases | {entry.get("alias") for entry in dropped if isinstance(entry, dict)}
    covered |= {entry.get("alias") for entry in selected}
    if len(covered) != report.get("considered"):
        fail("accounting_lost",
             f"{report.get('considered')} records considered but only {len(covered)} accounted for "
             "across selected, dropped and awaiting_identity")

    # 6. Receipts-grade.
    blob = json.dumps(report)
    for secret in (COLD_NAME, WARM_NAME, EMAILED_NAME, ADDRESS):
        if secret.lower() in blob.lower():
            fail("pii_in_report", f"the report contains {secret!r}; it names records by alias only")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--owned", action="append", default=[])
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    sys.path.insert(0, str(worktree))
    contact_state = load(worktree, "server/pipeline/contact_state.py",
                         "server.pipeline.contact_state")
    if contact_state is not None:
        check(worktree, contact_state)

    if args.exported_summary:
        if not args.summary.is_file():
            fail("summary_missing", f"{args.summary} was not written")
        else:
            body = args.summary.read_text(encoding="utf-8").lower()
            missing = [s for s in SUMMARY_SECTIONS if s not in body]
            if missing:
                fail("summary_sections", f"fix-summary.md is missing {', '.join(missing)}")
            else:
                args.exported_summary.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.summary, args.exported_summary)

    if args.patch and args.owned:
        add = git(worktree, "add", "--", *args.owned)
        if add.returncode != 0:
            fail("git_add_failed", add.stderr.strip())
        for line in git(worktree, "status", "--porcelain").stdout.splitlines():
            code, path = line[:2], line[3:].strip('"')
            if code != "??" and path not in args.owned:
                fail("outside_owned_files", f"{path} changed; this task owns {args.owned}")
        diff = git(worktree, "diff", "--cached", "--binary", "--", *args.owned)
        if not diff.stdout.strip():
            fail("empty_patch", "nothing staged; no owned file was edited")
        else:
            args.patch.parent.mkdir(parents=True, exist_ok=True)
            args.patch.write_text(diff.stdout, encoding="utf-8")

    if FAILURES:
        print("u19_awaiting_identity_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u19_awaiting_identity_check: PASS — a cold candidate with no address becomes a named "
          "LinkedIn queue entry instead of a silent discard, a warm one without an address stays a "
          "data gap, nothing reaches the producer without a destination, every considered record "
          "is still accounted for, and the report names nobody")
    return 0


if __name__ == "__main__":
    sys.exit(main())
