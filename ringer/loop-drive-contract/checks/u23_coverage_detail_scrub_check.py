#!/usr/bin/env python3
"""U23 executed check: a provider's exception text never reaches a receipt.

Found by the 2026-08-11 audit: the Gmail and HubSpot adapters copy raw
exception text into their coverage detail, and the candidate feeder writes that
detail into its reasons receipt — the artifact whose contract is aliases only.
A provider error routinely carries the request that failed: an email address in
a query string, a URL with parameters, sometimes a token fragment. One flaky
HTTP call and a real person's address lands in an artifact nothing else was
allowed to put it in. The LinkedIn adapter already scrubs to the exception
class name; the other two must match it.

The rule: a coverage detail may carry the exception CLASS and, when present, an
HTTP status — a status number is not sensitive and separates retry-in-a-moment
from never-going-to-work. It may never carry the exception's own prose.

Usage: u23_coverage_detail_scrub_check.py --worktree <tree> [--owned PATH ...]
                                          [--patch OUT] [--summary fix-summary.md]
                                          [--exported-summary OUT]
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")

SECRET_ADDRESS = "ada@example.invalid"
SECRET_QUERY = "q=from%3Aada%40example.invalid"
SECRET_TOKEN = "token-THIS-MUST-NEVER-APPEAR"

FAILURES: list[str] = []


def fail(where: str, why: str) -> None:
    FAILURES.append(f"FAIL [{where}]: {why}")


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def load(worktree: Path, relative: str, name: str):
    path = worktree / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(worktree))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        fail("import_error", f"{relative}: {exc.__class__.__name__}: {exc}")
        return None
    return module


class LeakyError(Exception):
    """An exception whose text carries everything a provider error can carry."""


def leaky() -> LeakyError:
    return LeakyError(
        f"HTTP Error 429: rate limited fetching https://api.example.invalid/search?"
        f"{SECRET_QUERY}&auth={SECRET_TOKEN} for {SECRET_ADDRESS}")


class AngryClient:
    def fetch(self, people):
        raise leaky()


def drive(module, source_label: str) -> None:
    people = [{"alias": "cand-1", "name": "Ada Lovelace", "email": SECRET_ADDRESS}]
    try:
        observations, coverage = module.observe(
            people, client=AngryClient(), now=datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001
        fail(f"{source_label}_raised", f"observe() let the client error escape: "
                                       f"{exc.__class__.__name__}")
        return
    if observations:
        fail(f"{source_label}_observations_after_failure",
             "a failed read still produced observations")
    if getattr(coverage, "reached", None) is not False:
        fail(f"{source_label}_reached_after_failure", "a failed read reported reached=True")
    detail = str(getattr(coverage, "detail", "") or "")
    if not detail.strip():
        fail(f"{source_label}_no_detail", "unreached with no reason at all")
    for secret, label in ((SECRET_ADDRESS, "an email address"),
                          (SECRET_QUERY, "a query string"),
                          (SECRET_TOKEN, "a token")):
        if secret.lower() in detail.lower():
            fail(f"{source_label}_detail_leaks",
                 f"the coverage detail carries {label} from the provider's exception text. This "
                 f"detail is written into the reasons receipt, whose contract is aliases only. "
                 f"Detail: {detail[:120]!r}")
    if "leakyerror" not in detail.lower() and "429" not in detail:
        fail(f"{source_label}_detail_useless",
             f"the detail names neither the exception class nor the HTTP status, so a human "
             f"cannot act on it: {detail!r}")


def check(worktree: Path) -> None:
    gmail = load(worktree, "server/pipeline/contact_sources/gmail_source.py", "u23_gmail")
    if gmail is not None:
        drive(gmail, "gmail")
    hubspot = load(worktree, "server/pipeline/contact_sources/hubspot_source.py", "u23_hubspot")
    if hubspot is not None:
        drive(hubspot, "hubspot")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--owned", action="append", default=[])
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    check(worktree)

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
        print("u23_coverage_detail_scrub_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u23_coverage_detail_scrub_check: PASS — a failing Gmail or HubSpot read reports "
          "unreached with the exception class and status only; no address, query string or token "
          "from the provider's error text can reach the receipts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
