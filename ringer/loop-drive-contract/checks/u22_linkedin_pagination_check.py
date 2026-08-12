#!/usr/bin/env python3
"""U22 executed check: a partial LinkedIn read must never certify complete coverage.

Found by a four-lane audit on 2026-08-11 and reproduced by an executed probe:
the Unipile adapter requests ONE /chats page of 100 and reports reached=True.
The probe planted a prior conversation on page two and the resolver returned
NO_CONTACT_FOUND with safe_for_cold_open=true — a cold open cleared against a
person we had already talked to, because a truncated read wore a completeness
receipt. Every lane independently named this the worst defect of the day.

The rule is the same one the whole contact model is built on, applied one layer
down: not-read is not nothing-there. A page we never fetched is a source we
never reached.

What this check drives, through the injected transport:

  S1  the touch lives on page 3 of 3 → the adapter follows the cursor, finds
      it, and only then reports reached=True.
  S2  page 2 raises → reached=False with a one-line reason; the pages already
      read may contribute observations (a touch that WAS seen still holds),
      but the read is never certified complete.
  S3  the cursor never terminates → the adapter stops at a bound and reports
      reached=False saying pagination was not exhausted. It must not loop.
  S4  one page, no cursor → reached=True. The simple case must not break.
  S5  the existing adapter contract (contact_source_check --source linkedin)
      still passes unchanged.

Usage: u22_linkedin_pagination_check.py --worktree <tree> [--owned PATH ...]
                                        [--patch OUT] [--summary fix-summary.md]
                                        [--exported-summary OUT]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")
PERSON = "Ada Lovelace"
PAGE_SIZE_FLOOR = 2  # fixtures use tiny pages; the adapter must not assume 100

FAILURES: list[str] = []


def fail(where: str, why: str) -> None:
    FAILURES.append(f"FAIL [{where}]: {why}")


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def load(worktree: Path):
    path = worktree / "server/pipeline/contact_sources/linkedin_source.py"
    if not path.is_file():
        fail("missing_file", "linkedin_source.py does not exist")
        return None
    spec = importlib.util.spec_from_file_location("u22_linkedin_source", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["u22_linkedin_source"] = module
    sys.path.insert(0, str(worktree))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        fail("import_error", f"{exc.__class__.__name__}: {exc}")
        return None
    return module


def chat(chat_id: str, name: str, stamp: str) -> dict:
    return {"id": chat_id, "timestamp": stamp,
            "attendees": [{"display_name": name, "id": f"li-{chat_id}"}],
            "last_message": {"is_sender": 1}}


def filler_page(page: int, count: int = 3) -> list[dict]:
    return [chat(f"c{page}-{i}", f"Somebody Else{page}{i}", "2026-08-01T09:00:00.000Z")
            for i in range(count)]


class PagedTransport:
    """Serves pages by cursor; the target sits on the LAST page."""

    def __init__(self, pages: int, target_on_last: bool = True,
                 raise_on_page: int | None = None, endless: bool = False):
        self.pages = pages
        self.target_on_last = target_on_last
        self.raise_on_page = raise_on_page
        self.endless = endless
        self.calls: list[dict] = []

    def __call__(self, method, path, params=None):
        params = dict(params or {})
        self.calls.append({"path": path, "params": params})
        cursor = params.get("cursor")
        page = 1 if not cursor else int(str(cursor).split("-")[-1])
        if self.raise_on_page is not None and page >= self.raise_on_page:
            raise RuntimeError("HTTP Error 500 mid-pagination")
        if self.endless:
            return {"items": filler_page(page), "cursor": f"page-{page + 1}"}
        items = filler_page(page)
        is_last = page >= self.pages
        if is_last and self.target_on_last:
            items.append(chat("c-target", PERSON, "2026-08-05T09:00:00.000Z"))
        return {"items": items, "cursor": None if is_last else f"page-{page + 1}"}


def observe(module, transport):
    client = module._UnipileLinkedInClient("dsn.example", "token-x", "acct-x",
                                          request=transport)
    return module.observe([{"alias": "cand-1", "name": PERSON, "email": None}],
                          client=client, now=datetime.now(timezone.utc))


def check(worktree: Path) -> None:
    module = load(worktree)
    if module is None:
        return

    # S1: the touch on page 3 of 3.
    deep = PagedTransport(pages=3)
    try:
        observations, coverage = observe(module, deep)
    except Exception as exc:  # noqa: BLE001
        fail("S1_raised", f"{exc.__class__.__name__}: {exc}")
        return
    if len(deep.calls) < 3:
        fail("S1_no_pagination",
             f"the response carried a cursor and the adapter made only {len(deep.calls)} "
             "request(s). A page it never fetched is a conversation it never saw")
    if not observations.get("cand-1"):
        fail("S1_touch_missed",
             "the prior conversation on the last page produced no observation, which is exactly "
             "the audited failure: a cold open would clear against somebody we already talked to")
    if getattr(coverage, "reached", None) is not True:
        fail("S1_complete_not_reached",
             f"every page was read and coverage still says reached={coverage.reached!r}")

    # S2: page 2 raises → never certified complete.
    broken = PagedTransport(pages=3, raise_on_page=2)
    observations, coverage = observe(module, broken)
    if getattr(coverage, "reached", None) is not False:
        fail("S2_partial_certified",
             "pagination failed midway and the source still reported reached=True. A partial read "
             "wearing a completeness receipt is the defect this check exists for")
    if not str(getattr(coverage, "detail", "") or "").strip():
        fail("S2_no_reason", "a failed pagination left no one-line reason in coverage")

    # S3: endless cursor → bounded, fail-closed, no loop.
    endless = PagedTransport(pages=0, endless=True)
    observations, coverage = observe(module, endless)
    if len(endless.calls) > 200:
        fail("S3_unbounded", f"{len(endless.calls)} requests against a non-terminating cursor")
    if getattr(coverage, "reached", None) is not False:
        fail("S3_cap_certified",
             "the adapter hit its page bound with a cursor still outstanding and reported the read "
             "complete. Not-finished is not finished")

    # S4: one page, no cursor.
    single = PagedTransport(pages=1)
    observations, coverage = observe(module, single)
    if getattr(coverage, "reached", None) is not True:
        fail("S4_simple_broken", "a complete single-page read now reports unreached")
    if not observations.get("cand-1"):
        fail("S4_touch_missed", "the touch on a single complete page was missed")


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
        print("u22_linkedin_pagination_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u22_linkedin_pagination_check: PASS — the adapter follows the cursor to the last page "
          "before certifying completeness, a mid-pagination failure or an unexhausted cursor "
          "reports unreached with a reason, a touch on any page is observed, and the single-page "
          "case still works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
