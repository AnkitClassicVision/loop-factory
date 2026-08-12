#!/usr/bin/env python3
"""Hold a contact-source adapter to the contract, then export its patch.

One adapter per run. Each adapter turns raw records from ONE channel into
`Observation`s for `server/pipeline/contact_state.py`, plus a `SourceCoverage`
saying whether the channel could actually be read.

The properties below are the entire reason this layer exists. P3 is the one
that decides whether the reconcile step is an improvement or a liability: an
adapter that swallows an error and returns an empty list looks exactly like a
channel with nothing in it. The resolver would then see "reached, no contact",
report NO_CONTACT_FOUND on partial coverage, and clear a cold open on somebody
we may well have emailed last week. Absence of evidence must never be
serialisable as evidence of absence.

  P1 shape        SOURCE_NAME matches, and map_records(records, people) exists
  P2 mapping      known records -> observations with the right alias, a
                  timezone-aware UTC timestamp, direction, and an evidence ref
  P3 failure      a raising client -> coverage.reached False AND zero
                  observations. Never a quiet empty list.
  P4 honest empty a working client with nothing to say -> reached True, zero
                  observations
  P5 privacy      no subject, body, summary or utterance text reaches any
                  observation field. Who, when, which channel, and a reference.
  P6 ordering     timestamps are timezone-aware, or cross-source arbitration
                  silently mis-ranks a Bee call against a Gmail thread

Exit 0 PASS, 1 FAIL. Every failure prints what was expected and what happened.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


PEOPLE = [
    {"alias": "cand-aaa", "name": "Ada Lovelace", "email": "ada@example.invalid"},
    {"alias": "cand-bbb", "name": "Grace Hopper", "email": "grace@example.invalid"},
]

SECRETS = ("Confidential subject line", "body text that must not travel",
           "he said he was interested in coming on the show")

# Raw record shapes per channel, with a planted secret in the field an adapter
# might lazily copy into evidence_ref.
FIXTURES = {
    "gmail": [
        {"id": "msg-1", "threadId": "thread-1", "from": "ankit@mybcat.com",
         "to": "ada@example.invalid", "internalDate": "1786060800000",
         "subject": SECRETS[0], "snippet": SECRETS[1]},
    ],
    "linkedin": [
        {"name": "Ada Lovelace", "sent_at": "2026-08-05T09:00:00+00:00",
         "direction": "outbound", "thread_ref": "li-thread-1", "note": SECRETS[1]},
    ],
    "bee": [
        {"id": "bee-1", "start_time": "2026-08-06T09:00:00+00:00",
         "participants": ["Ada Lovelace"], "summary": SECRETS[2]},
    ],
    "hubspot": [
        {"engagementId": "eng-1", "type": "EMAIL", "timestamp": 1786060800000,
         "contactEmail": "ada@example.invalid", "bodyPreview": SECRETS[1]},
    ],
}

MODULES = {
    "gmail": "server/pipeline/contact_sources/gmail_source.py",
    "linkedin": "server/pipeline/contact_sources/linkedin_source.py",
    "bee": "server/pipeline/contact_sources/bee_source.py",
    "hubspot": "server/pipeline/contact_sources/hubspot_source.py",
}

SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")


class Boom(Exception):
    pass


class RaisingClient:
    def fetch(self, people):  # noqa: ARG002
        raise Boom("channel unavailable: simulated auth/network failure")


class EmptyClient:
    def fetch(self, people):  # noqa: ARG002
        return []


class FixtureClient:
    def __init__(self, records):
        self.records = records

    def fetch(self, people):  # noqa: ARG002
        return list(self.records)


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"adapter_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(worktree: Path, source: str) -> list[str]:
    failures: list[str] = []
    module_path = worktree / MODULES[source]
    if not module_path.is_file():
        return [f"FAIL [missing_module]: {MODULES[source]} was not created"]

    sys.path.insert(0, str(worktree))
    try:
        from server.pipeline.contact_state import Observation, SourceCoverage  # noqa: F401
    except Exception as exc:
        return [f"FAIL [contact_state_import]: {exc}"]

    try:
        adapter = load_module(module_path)
    except Exception as exc:
        return [f"FAIL [import_error]: {MODULES[source]} does not import: {exc}"]

    # P1 shape
    if getattr(adapter, "SOURCE_NAME", None) != source:
        failures.append(
            f"FAIL [source_name]: SOURCE_NAME is {getattr(adapter, 'SOURCE_NAME', None)!r}, "
            f"expected {source!r}; the resolver keys coverage off this")
    if not callable(getattr(adapter, "observe", None)):
        failures.append("FAIL [no_observe]: the adapter must expose observe(people, *, client=None, now=None)")
        return failures

    # P2 mapping
    try:
        observations, coverage = adapter.observe(PEOPLE, client=FixtureClient(FIXTURES[source]))
    except Exception as exc:
        failures.append(f"FAIL [observe_raised]: a working client must not raise: {exc!r}")
        return failures

    flat = [o for group in (observations.values() if isinstance(observations, dict) else [observations])
            for o in group]
    if not flat:
        failures.append(
            "FAIL [no_observation]: the fixture contains one real contact with "
            f"{PEOPLE[0]['name']} and the adapter produced nothing")
    else:
        first = flat[0]
        if getattr(first, "source", None) != source:
            failures.append(f"FAIL [obs_source]: observation.source is {getattr(first,'source',None)!r}")
        at = getattr(first, "at", None)
        if not isinstance(at, datetime):
            failures.append(f"FAIL [obs_timestamp]: observation.at is {at!r}, expected a datetime")
        elif at.tzinfo is None:
            failures.append(
                "FAIL [naive_timestamp]: observation.at is timezone-naive. Cross-source "
                "arbitration ranks a Bee call against a Gmail thread by timestamp; a naive "
                "value silently mis-ranks them")
        if not str(getattr(first, "evidence_ref", "") or "").strip():
            failures.append("FAIL [no_evidence_ref]: every observation must cite where to verify it")

    if not isinstance(coverage, object) or getattr(coverage, "reached", None) is not True:
        failures.append(
            f"FAIL [coverage_success]: a working client must report reached=True, got "
            f"{getattr(coverage, 'reached', None)!r}")

    # P5 privacy
    blob = " ".join(
        f"{getattr(o,'source','')}|{getattr(o,'direction','')}|{getattr(o,'evidence_ref','')}|"
        f"{getattr(o,'grounding','')}" for o in flat)
    for secret in SECRETS:
        if secret.casefold() in blob.casefold():
            failures.append(
                "FAIL [privacy_leak]: message content reached an observation field. An "
                "observation carries who, when, which channel and a reference — never a "
                f"subject, body, or conversation summary. Leaked: {secret[:40]!r}")

    # P3 failure — the property the whole layer stands on
    try:
        obs_fail, cov_fail = adapter.observe(PEOPLE, client=RaisingClient())
    except Boom:
        failures.append(
            "FAIL [error_escapes]: the adapter let the client's exception escape. It must "
            "catch it and report coverage.reached=False, so the resolver can return UNKNOWN "
            "instead of a clean bill of health")
        obs_fail, cov_fail = None, None
    except Exception as exc:
        failures.append(f"FAIL [error_wrong_type]: unexpected exception {exc!r}")
        obs_fail, cov_fail = None, None

    if cov_fail is not None:
        if getattr(cov_fail, "reached", None) is not False:
            failures.append(
                "FAIL [failure_reported_as_reached]: the channel FAILED and the adapter "
                f"reported reached={getattr(cov_fail,'reached',None)!r}. This is the defect "
                "the whole reconcile step exists to prevent: a failed read that looks like "
                "an empty inbox lets the resolver clear a cold open for someone we may have "
                "emailed last week")
        flat_fail = [o for g in (obs_fail.values() if isinstance(obs_fail, dict) else [obs_fail or []])
                     for o in g]
        if flat_fail:
            failures.append(
                f"FAIL [observations_on_failure]: {len(flat_fail)} observation(s) returned "
                "from a failed read; partial results from a broken channel are not evidence")
        if not str(getattr(cov_fail, "detail", "") or "").strip():
            failures.append(
                "FAIL [no_failure_detail]: coverage.detail must say WHY the channel could "
                "not be read, or a human cannot fix it")

    # P4 honest empty
    try:
        obs_empty, cov_empty = adapter.observe(PEOPLE, client=EmptyClient())
        if getattr(cov_empty, "reached", None) is not True:
            failures.append(
                "FAIL [empty_not_reached]: a channel that was read successfully and simply "
                "had nothing must report reached=True; otherwise a genuinely clean prospect "
                "can never be cleared")
        flat_empty = [o for g in (obs_empty.values() if isinstance(obs_empty, dict) else [obs_empty or []])
                      for o in g]
        if flat_empty:
            failures.append(f"FAIL [phantom_observations]: {len(flat_empty)} observation(s) from no records")
    except Exception as exc:
        failures.append(f"FAIL [empty_raised]: an empty channel must not raise: {exc!r}")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--source", required=True, choices=sorted(MODULES))
    parser.add_argument("--owned", action="append", required=True)
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    owned = list(args.owned)
    failures = validate(worktree, args.source)

    if args.exported_summary:
        if not args.summary.is_file():
            failures.append(f"FAIL [summary_missing]: {args.summary} was not written")
        else:
            text = args.summary.read_text(encoding="utf-8")
            missing = [s for s in SUMMARY_SECTIONS if s.lower() not in text.lower()]
            if missing:
                failures.append(f"FAIL [summary_sections]: fix-summary.md is missing {', '.join(missing)}")
            else:
                args.exported_summary.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.summary, args.exported_summary)

    if args.patch:
        add = git(worktree, "add", "--", *owned)
        if add.returncode != 0:
            failures.append(f"FAIL [git_add_failed]: {add.stderr.strip()}")
        for line in git(worktree, "status", "--porcelain").stdout.splitlines():
            code, path = line[:2], line[3:].strip('"')
            if code != "??" and path not in owned:
                failures.append(f"FAIL [outside_owned_files]: {path} changed; this task owns {owned}")
        diff = git(worktree, "diff", "--cached", "--binary", "--", *owned)
        if not diff.stdout.strip():
            failures.append("FAIL [empty_patch]: nothing staged; no owned file was edited")
        else:
            args.patch.parent.mkdir(parents=True, exist_ok=True)
            args.patch.write_text(diff.stdout, encoding="utf-8")

    if failures:
        print(f"contact_source_check[{args.source}]: FAIL")
        for line in failures:
            print(f"  {line}")
        return 1
    print(f"contact_source_check[{args.source}]: PASS — maps records to timestamped "
          "observations, keeps message content out of them, and reports reached=False with a "
          "reason when the channel cannot be read instead of a silent empty result")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
