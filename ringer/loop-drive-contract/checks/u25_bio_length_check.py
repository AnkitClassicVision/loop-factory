#!/usr/bin/env python3
"""U25 executed check: a real-length professional bio survives the word ceiling.

Found live on 2026-08-11, minutes after r32 landed: the real guest's bio
paragraph measures 123 words by the module's own _WORD_RE — three words over
the 25..120 candidate window — so every r32 signal fired (third-person name,
bio-cue predecessor, boilerplate rejection all correct) and the bio was STILL
excluded. The r32 fixture bio was ~60 words: kinder than production, exactly
the fixture-law defect this pins down. Professional bios routinely run
120-250 words.

The contract this check enforces on
`server.pipeline.guest_email_context.gather_guest_email_context`:

  L1  a third-person professional bio of >120 words (real live shape: CRLF,
      double spaces, "Here's a quick bio" cue paragraph before it) MUST be a
      bio candidate. Strong signals earn a higher ceiling.
  L2  a third-person bio of >120 words qualifies even WITHOUT the cue
      paragraph — the name-plus-credentials shape alone is a strong signal.
  L3  guard: a long (>120 words) conversational paragraph with NO bio signal
      beyond first-person pronouns must STILL be excluded. The fix must raise
      the ceiling for strong signals, not delete it.
  L4  guard: the 25-word floor is unchanged; a two-word paragraph never
      qualifies.

Fixtures are self-validating: if a fixture paragraph's word count leaves its
intended band (per the module's own _WORD_RE), the check fails as
fixture_invalid instead of silently testing the wrong thing.

Usage: u25_bio_length_check.py --worktree <tree> [--owned PATH ...]
                               [--patch OUT] [--summary fix-summary.md]
                               [--exported-summary OUT]
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")

GUEST_NAME = "Marisol Etchevarria"
GUEST_EMAIL = "marisol@cordillerapracticegroup.invalid"
OWNER_EMAIL = "owner@showhost.invalid"

# Third person, credentials, no first-person pronouns, and LONG — mirrors the
# live defect where the real bio measured 123 words. This one must land in the
# 121..250 band or the check rejects its own fixture.
LONG_BIO = (
    "Marisol Etchevarria, OD, MBA is the founding partner of Cordillera Practice "
    "Group, a consultancy that guides independent optometry owners through "
    "acquisitions, associate buy-ins, and multi-location expansion across the "
    "mountain west.  Before founding Cordillera, Marisol spent eleven years as the "
    "managing optometrist of a four-location group in Colorado, where she built the "
    "hiring, scheduling, and inventory systems that carried the group from one "
    "storefront to a regional brand.  Marisol serves on the advisory board of a "
    "nonprofit that places refurbished exam lanes in rural clinics, teaches a "
    "practice-finance elective at a college of optometry, and speaks nationally on "
    "succession planning, associate development, and the economics of independent "
    "practice.  Her work has been featured in three industry publications and two "
    "national podcasts, and she still sees patients one morning a week to stay "
    "close to the exam lane her clients live in."
)

# Long, first person, conversational, zero bio shape — must stay excluded.
LONG_CHATTER = (
    "I wanted to give you the full picture of our travel plans before the "
    "recording so nothing surprises us on the day.  We land the night before "
    "around nine, and I expect we will grab dinner near the hotel before turning "
    "in early, because the drive from the airport is nearly an hour when the "
    "toll road is busy.  In the morning I plan to test my laptop, my backup "
    "laptop, and my podcast microphone before breakfast, and then we will walk "
    "the convention floor for a couple of hours so my notes are fresh.  If "
    "anything runs long I will text you from the floor, and we can slide the "
    "session by thirty minutes without hurting anyone's schedule, since our "
    "flight home does not leave until the following afternoon and the hotel gave "
    "us a generous late checkout window."
)

TINY_PARAGRAPH = "Cheers, MG"

CUE_PARAGRAPH = (
    "Here's a quick bio.  If you'd like something different or condensed, please "
    "let me know.  The sentences are a bit lengthy : - ) "
)

FAILURES: list[str] = []


def fail(where: str, why: str) -> None:
    FAILURES.append(f"FAIL [{where}]: {why}")


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def message(msg_id: str, body: str) -> dict:
    return {"id": msg_id,
            "payload": {"mimeType": "multipart/mixed",
                        "headers": [{"name": "From",
                                     "value": f"{GUEST_NAME} <{GUEST_EMAIL}>"},
                                    {"name": "To", "value": OWNER_EMAIL}],
                        "parts": [{"mimeType": "text/plain",
                                   "body": {"data": encode(body)}}]}}


class _Ready:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class FakeMessages:
    def __init__(self, store: dict):
        self._store = store

    def list(self, **kwargs):
        return _Ready({"messages": [{"id": key} for key in self._store]})

    def get(self, **kwargs):
        return _Ready(self._store[kwargs["id"]])


class FakeUsers:
    def __init__(self, store):
        self._store = store

    def messages(self):
        return FakeMessages(self._store)


class FakeService:
    def __init__(self, store):
        self._store = store

    def users(self):
        return FakeUsers(self._store)


def load(worktree: Path):
    sys.path.insert(0, str(worktree))
    path = worktree / "server/pipeline/guest_email_context.py"
    spec = importlib.util.spec_from_file_location("u25_guest_email_context", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["u25_guest_email_context"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        fail("import_error", f"{exc.__class__.__name__}: {exc}")
        return None
    return module


def gather(module, body: str):
    service = FakeService({"m1": message("m1", body)})
    return module.gather_guest_email_context(service, GUEST_EMAIL)


def check(worktree: Path) -> None:
    module = load(worktree)
    if module is None:
        return

    words = lambda text: len(module._WORD_RE.findall(re.sub(r"\s+", " ", text).strip()))  # noqa: E731

    bio_words = words(LONG_BIO)
    if not 121 <= bio_words <= 250:
        fail("fixture_invalid",
             f"LONG_BIO measures {bio_words} words; this check needs it in 121..250 "
             "to model the live defect (real bio: 123 words)")
    chatter_words = words(LONG_CHATTER)
    if chatter_words <= 120:
        fail("fixture_invalid",
             f"LONG_CHATTER measures {chatter_words} words; the guard needs >120")
    if FAILURES:
        return

    # L1: long third-person bio behind the real cue paragraph, CRLF shape.
    body = f"Likewise, thanks for having me. \r\n\r\n{CUE_PARAGRAPH}\r\n\r\n{LONG_BIO}\r\n"
    bios = [norm(c) for c in (gather(module, body).get("bio_candidates") or [])]
    if norm(LONG_BIO) not in bios:
        fail("L1_long_bio_with_cue_missing",
             f"a {bio_words}-word third-person bio introduced by 'Here's a quick bio' "
             "is not a candidate. The live defect exactly: every strong signal fires "
             "and the word ceiling still throws the bio away")

    # L2: same bio, no cue paragraph — third-person shape alone must carry it.
    bios = [norm(c) for c in (gather(module, LONG_BIO + "\r\n").get("bio_candidates") or [])]
    if norm(LONG_BIO) not in bios:
        fail("L2_long_bio_without_cue_missing",
             f"a {bio_words}-word third-person bio without a cue paragraph is not a "
             "candidate; name-plus-credential shape is a strong signal on its own")

    # L3 guard: long first-person chatter must NOT ride the raised ceiling.
    body = f"Quick logistics note.\r\n\r\n{LONG_CHATTER}\r\n"
    bios = [norm(c) for c in (gather(module, body).get("bio_candidates") or [])]
    if norm(LONG_CHATTER) in bios:
        fail("L3_chatter_qualified",
             f"a {chatter_words}-word conversational paragraph qualified as a bio; "
             "the ceiling must rise only for strong bio signals, not vanish")

    # L4 guard: the floor is untouched.
    body = f"{CUE_PARAGRAPH}\r\n\r\n{TINY_PARAGRAPH}\r\n"
    bios = [norm(c) for c in (gather(module, body).get("bio_candidates") or [])]
    if norm(TINY_PARAGRAPH) in bios:
        fail("L4_floor_regressed", "a two-word paragraph qualified as a bio candidate")


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
        print("u25_bio_length_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u25_bio_length_check: PASS — a real-length (>120 word) third-person bio is a "
          "candidate with or without a cue paragraph, long conversational prose still "
          "never qualifies, and the 25-word floor is unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
