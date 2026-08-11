#!/usr/bin/env python3
"""U21 v2 executed check: the guest's own bio reaches bio_candidates; boilerplate does not.

Rewritten 2026-08-11 against the REAL production module. The first version of
this check invented a module API that did not exist and carried a real guest's
bio, email and phone as fixture data; both were defects. This version drives
`server.pipeline.guest_email_context.gather_guest_email_context` — the function
the episode DAG's guest_assets node actually calls — with a FICTIONAL guest
whose message has the exact byte-shape live mail proved: CRLF paragraph breaks,
double spaces after full stops, an Outlook-style "From:" quoted history carrying
the OWNER'S own words, a signature block, and a confidentiality footer.

The three defects this pins, each proven against live mail before the fix:

  D1  bio candidates REQUIRED first-person pronouns (_FIRST_PERSON_RE), so a
      third-person professional bio — "X, MBA, CEPA is a ..." — was rejected,
      while the owner's conversational ask and a legal footer (both first
      person) qualified. Professional bios are written in third person; the
      filter was exactly backwards.
  D2  _strip_quoted_reply knew only Gmail quoting ("On ... wrote:", ">" lines),
      so Outlook "From:/Sent:/To:/Subject:" history survived and the owner's
      outbound text was attributed to the guest. Live result: the three bio
      candidates for a real guest were a confidentiality footer, the owner's
      own request, and the owner's signature.
  D3  a message whose body is ONLY the bio is rejected as a full-body extract,
      so the cleanest possible guest reply produces nothing.

What must NOT change: headshot extraction, link classification, and every
existing test in tests/test_guest_email_context.py.

Usage: u21_guest_assets_check.py --worktree <tree> [--owned PATH ...]
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

GUEST_NAME = "Dana Kowalczyk"
GUEST_EMAIL = "dana@harborpeakadvisors.invalid"
OWNER_EMAIL = "owner@showhost.invalid"

# Third person throughout, credentials, no first-person pronoun anywhere.
GUEST_BIO = (
    "Dana Kowalczyk, MBA, CEPA is a Certified Exit Planning Advisor and the managing "
    "partner of Harbor Peak Advisors, an advisory firm focused on practice sales and "
    "ownership transitions.  Before Harbor Peak, Dana spent a decade leading M&A "
    "diligence for a regional healthcare group and now serves on the board of a youth "
    "running nonprofit in Fort Hill, SC."
)
OWNER_ASK = (
    "Can you reply with a few things so we make you look good: a headshot you like, a "
    "2-3 sentence bio, your social handles, and any links you want mentioned.  And tell "
    "me how you pronounce your last name so I don't butcher it on air."
)
SIGNATURE = "Dana Kowalczyk, MBA, CEPA\r\nManaging Partner\r\n(555) 014-7788\r\nwww.harborpeakadvisors.invalid"
FOOTER = (
    "The content of this email is confidential and intended for the recipient specified "
    "in this message only.  It is strictly forbidden to share any part of this message "
    "with any third party, without the sender's written consent.  If you received this "
    "message by mistake, please reply to this message and follow with its deletion, so "
    "that we can ensure such a mistake does not occur in the future."
)
HEADSHOT = "DSC_4410.JPG"

REPLY_BODY = (
    "Likewise, thanks for having me. \r\n\r\n"
    "Answers below and attached:\r\n\r\n"
    "Here is my LinkedIn profile - https://www.linkedin.com/in/dana-kowalczyk-cepa-000000/\r\n\r\n"
    "Here's a quick bio.  If you'd like something different or condensed, please let me "
    "know.  The sentences are a bit lengthy : - ) \r\n\r\n"
    f"{GUEST_BIO}\r\n\r\n"
    "The best way to pronounce my name is ko-VAL-chick.  We'll test ya' before the call ; - )\r\n\r\n"
    "Laptop and I will be ready to go.\r\n\r\nDK\r\n\r\n\r\n"
    f"{SIGNATURE}\r\n\r\n"
    f"{FOOTER}\r\n\r\n"
    "-----Original Message-----\r\n"
    f"From: Show Host <{OWNER_EMAIL}> \r\n"
    "Sent: Wednesday, August 5, 2026 6:08 PM\r\n"
    f"To: Dana Kowalczyk <{GUEST_EMAIL}>\r\n"
    "Subject: aug 12 recording, few quick things\r\n\r\n"
    f"Hey Dana,\r\n\r\n{OWNER_ASK}\r\n\r\nThanks,\r\nHost\r\n"
)

BIO_ONLY_BODY = GUEST_BIO + "\r\n"

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


def message(msg_id: str, body: str, *, attach: str | None = None) -> dict:
    parts = [{"mimeType": "text/plain", "body": {"data": encode(body)}}]
    if attach:
        parts.append({"mimeType": "image/jpeg", "filename": attach,
                      "body": {"attachmentId": f"att-{msg_id}", "size": 640000}})
    return {"id": msg_id,
            "payload": {"mimeType": "multipart/mixed",
                        "headers": [{"name": "From",
                                     "value": f"{GUEST_NAME} <{GUEST_EMAIL}>"},
                                    {"name": "To", "value": OWNER_EMAIL}],
                        "parts": parts}}


class _Ready:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _Attachments:
    def get(self, **kwargs):
        return _Ready({"data": encode("\xff\xd8\xffJPEGDATA")})


class FakeMessages:
    def __init__(self, store: dict):
        self._store = store

    def list(self, **kwargs):
        return _Ready({"messages": [{"id": key} for key in self._store]})

    def get(self, **kwargs):
        return _Ready(self._store[kwargs["id"]])

    def attachments(self):
        return _Attachments()


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
    spec = importlib.util.spec_from_file_location("u21_guest_email_context", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["u21_guest_email_context"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        fail("import_error", f"{exc.__class__.__name__}: {exc}")
        return None
    return module


def check(worktree: Path) -> None:
    module = load(worktree)
    if module is None:
        return

    # Scenario 1: the realistic reply — bio + Outlook history + signature + footer.
    service = FakeService({"m1": message("m1", REPLY_BODY, attach=HEADSHOT)})
    try:
        context = module.gather_guest_email_context(service, GUEST_EMAIL)
    except Exception as exc:  # noqa: BLE001
        fail("gather_raised", f"{exc.__class__.__name__}: {exc}")
        return
    bios = [norm(candidate) for candidate in (context.get("bio_candidates") or [])]

    if norm(GUEST_BIO) not in bios:
        fail("D1_third_person_bio_missing",
             f"the guest's own third-person bio is not among {len(bios)} bio candidate(s). "
             "Professional bios are written in third person; requiring first-person pronouns "
             "rejects exactly the paragraph the guest sent to be read on air")
    if any(norm(OWNER_ASK)[:60] in candidate for candidate in bios):
        fail("D2_owner_text_attributed",
             "the OWNER'S own request, quoted below an Outlook 'From:' header in the guest's "
             "reply, qualified as the guest's bio. Outlook history must be stripped before "
             "paragraph classification")
    if any("strictly forbidden" in candidate or "confidential" in candidate for candidate in bios):
        fail("D2_footer_qualified",
             "the confidentiality footer qualified as a bio candidate; legal boilerplate must "
             "never be offered as a person's biography")
    if any(norm("Managing Partner (555) 014-7788") in candidate for candidate in bios):
        fail("D2_signature_qualified", "the signature block qualified as a bio candidate")

    shots = context.get("headshots") or []
    if not any(shot.get("filename") == HEADSHOT for shot in shots):
        fail("headshot_regressed",
             f"the attached {HEADSHOT} was not found; headshot extraction must not change")

    # Scenario 2: the cleanest reply — body IS the bio, nothing else.
    service = FakeService({"m2": message("m2", BIO_ONLY_BODY)})
    context = module.gather_guest_email_context(service, GUEST_EMAIL)
    bios = [norm(candidate) for candidate in (context.get("bio_candidates") or [])]
    if norm(GUEST_BIO) not in bios:
        fail("D3_bio_only_rejected",
             "a message whose entire body is the bio produced no bio candidate. The cleanest "
             "possible guest reply must not be the one the extractor throws away")

    # Scenario 3 (regression): a first-person bio still qualifies.
    first_person = ("I own three practices in Ohio and started as a technician before "
                    "buying my first location in 2011.  My focus now is training owners "
                    "to step out of the exam lane and run the business.")
    service = FakeService({"m3": message("m3", "Here's a quick bio.\r\n\r\n" + first_person + "\r\n")})
    context = module.gather_guest_email_context(service, GUEST_EMAIL)
    bios = [norm(candidate) for candidate in (context.get("bio_candidates") or [])]
    if norm(first_person) not in bios:
        fail("first_person_regressed", "a first-person bio no longer qualifies; the fix for "
                                       "third-person bios must widen the filter, not move it")


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
        print("u21_guest_assets_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u21_guest_assets_check: PASS — the guest's third-person bio survives extraction "
          "verbatim, Outlook-quoted owner text, signatures and legal footers never qualify as a "
          "bio, a bio-only message is accepted, first-person bios still qualify, and headshot "
          "extraction is unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
