#!/usr/bin/env python3
"""U16 executed check: the LinkedIn channel reads LinkedIn, not a file nobody writes.

The adapter shipped on 2026-08-10 reading a JSONL handoff at
/mnt/d_drive/repos/open-engine/departments/sales/state/handoff/podcast_guest_linkedin.jsonl.
That file has never existed. The producer that would write it fail-closes on an
IAM grant its own launcher documents as not yet made, so the channel reported
reached=False on every run — correctly, and permanently. Because incomplete
first-order coverage can never clear a cold open, ONE unwritable file silently
held the entire outreach loop shut.

Owner decision, Ankit 2026-08-11: read LinkedIn the way the /linkedin skill
does, through Unipile.

What this check refuses to accept:

  1. A client that reaches the network to be constructed. build_client() runs on
     every feeder invocation; it must be able to say "no credentials" without a
     round trip and without raising.
  2. Message text anywhere. A Unipile chat payload carries the conversation. An
     observation may carry WHO, WHEN, WHICH CHANNEL and a reference — never a
     subject, snippet, preview or body. This channel is Ankit's real inbox.
  3. A credential in an artifact. The DSN, the token and the account id must not
     appear in any record, observation or coverage detail. Coverage details are
     written into receipts.
  4. A naive timestamp. The resolver ranks a LinkedIn touch against a Gmail
     thread and a Bee call purely by time; a naive datetime mis-ranks them.
  5. A failed read that looks like an empty inbox. That is the whole model.

Usage: u16_linkedin_unipile_check.py --worktree <tree> [--owned PATH ...]
                                     [--patch OUT] [--summary fix-summary.md]
                                     [--exported-summary OUT]
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")

DSN = "api99.unipile.example"
TOKEN = "unipile-token-THIS-MUST-NEVER-APPEAR"
ACCOUNT = "acct-THIS-MUST-NEVER-APPEAR"
MESSAGE_TEXT = "hey, following up on the recording slot we discussed"
PERSON = "Ada Lovelace"

CREDENTIAL_ENV = ("UNIPILE_DSN", "UNIPILE_ACCESS_TOKEN", "UNIPILE_LINKEDIN_ACCOUNT_ID",
                  "LINKEDIN_UNIPILE_CREDENTIALS")

# One page of /chats as Unipile returns it: attendees carry the display name, the
# chat carries its own last-activity timestamp, and the preview carries text that
# must not survive into an observation.
CHATS_PAYLOAD = {
    "items": [
        {"id": "chat-77", "timestamp": "2026-08-05T09:00:00.000Z", "unread_count": 1,
         "attendees": [{"display_name": PERSON, "id": "li-ada"}],
         "last_message": {"text": MESSAGE_TEXT, "is_sender": 1}},
        {"id": "chat-88", "timestamp": "2026-08-06T11:30:00.000Z",
         "attendees": [{"display_name": "Someone Else", "id": "li-else"}],
         "last_message": {"text": MESSAGE_TEXT, "is_sender": 0}},
    ]
}

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


def scrub_env() -> dict[str, str]:
    saved = {key: os.environ[key] for key in CREDENTIAL_ENV if key in os.environ}
    for key in CREDENTIAL_ENV:
        os.environ.pop(key, None)
    return saved


def restore_env(saved: dict[str, str]) -> None:
    for key in CREDENTIAL_ENV:
        os.environ.pop(key, None)
    os.environ.update(saved)


def leaked(blob: str) -> str | None:
    for secret in (TOKEN, ACCOUNT, MESSAGE_TEXT):
        if secret.lower() in blob.lower():
            return secret
    return None


def check(worktree: Path) -> None:
    module = load(worktree, "server/pipeline/contact_sources/linkedin_source.py",
                  "u16_linkedin_source")
    if module is None:
        return

    source_text = (worktree / "server/pipeline/contact_sources/linkedin_source.py").read_text(
        encoding="utf-8")
    if "open-engine" in source_text or "podcast_guest_linkedin.jsonl" in source_text:
        fail("still_reads_dead_file",
             "the adapter still refers to the open-engine JSONL handoff, which has never been "
             "written and whose producer fail-closes on an ungranted IAM read. That file is why "
             "the channel is permanently unreachable")

    saved = scrub_env()
    try:
        # 1. No credentials: None, quietly.
        try:
            client = module.build_client()
        except Exception as exc:  # noqa: BLE001
            fail("build_client_raised",
                 f"build_client() raised {exc.__class__.__name__} with no credentials present. It "
                 "runs on every feeder invocation and must simply return None")
            client = None
        if client is not None:
            fail("build_client_without_credentials",
                 "build_client() returned a client although UNIPILE_DSN, UNIPILE_ACCESS_TOKEN, "
                 "UNIPILE_LINKEDIN_ACCOUNT_ID and LINKEDIN_UNIPILE_CREDENTIALS were all absent")

        # 2. Credentials present: a client, built without touching the network.
        os.environ.update({"UNIPILE_DSN": DSN, "UNIPILE_ACCESS_TOKEN": TOKEN,
                           "UNIPILE_LINKEDIN_ACCOUNT_ID": ACCOUNT})
        try:
            built = module.build_client()
        except Exception as exc:  # noqa: BLE001
            fail("build_client_network",
                 f"build_client() raised {exc.__class__.__name__} with credentials present, which "
                 "means it tried to reach Unipile while merely being constructed")
            built = None
        if built is None:
            fail("build_client_ignored_env",
                 "the three UNIPILE_* variables were set and build_client() still returned None, "
                 "so the runner can never make this channel readable")

        # 3. The JSON-blob form, which is how one secret ref arrives from secret_exec.
        for key in ("UNIPILE_DSN", "UNIPILE_ACCESS_TOKEN", "UNIPILE_LINKEDIN_ACCOUNT_ID"):
            os.environ.pop(key, None)
        os.environ["LINKEDIN_UNIPILE_CREDENTIALS"] = (
            '{"UNIPILE_DSN": "%s", "UNIPILE_ACCESS_TOKEN": "%s", '
            '"UNIPILE_LINKEDIN_ACCOUNT_ID": "%s"}' % (DSN, TOKEN, ACCOUNT))
        if module.build_client() is None:
            fail("blob_form_unsupported",
                 "LINKEDIN_UNIPILE_CREDENTIALS carrying the secret as one JSON object produced no "
                 "client. That is the shape one secret reference injects, so the runner has no way "
                 "to supply these credentials without splitting the secret")
        os.environ.pop("LINKEDIN_UNIPILE_CREDENTIALS", None)
    finally:
        restore_env(saved)

    # 4. Drive the client's own normalisation through an injected transport.
    client_class = getattr(module, "_UnipileLinkedInClient", None)
    if client_class is None:
        fail("client_class_missing",
             "no _UnipileLinkedInClient(dsn, token, account_id, *, request=None) to drive. The "
             "request seam is what lets this be checked without calling Unipile")
        return

    calls: list[tuple] = []

    def fake_request(method, path, params=None):
        calls.append((method, path, dict(params or {})))
        return CHATS_PAYLOAD

    try:
        driven = client_class(DSN, TOKEN, ACCOUNT, request=fake_request)
        records = driven.fetch([{"alias": "cand-1", "name": PERSON, "email": None}])
    except Exception as exc:  # noqa: BLE001
        fail("fetch_raised", f"fetch() raised {exc.__class__.__name__}: {exc}")
        return

    if not calls:
        fail("no_request", "fetch() returned without asking Unipile for anything")
    elif not any("chat" in str(call[1]).lower() for call in calls):
        fail("wrong_endpoint", f"fetch() called {[c[1] for c in calls]}, expected the chats listing")

    blob = repr(records)
    hit = leaked(blob)
    if hit is not None:
        fail("leak_in_records",
             f"the normalised records carry {hit[:32]!r}. Credentials and message text never leave "
             "this adapter")

    matched = [record for record in records
               if PERSON.lower() in str(record.get("name", "")).lower()]
    if not matched:
        fail("person_not_matched",
             f"the chat whose attendee is {PERSON!r} produced no record for them: {records!r}")
    else:
        record = matched[0]
        if "chat-77" not in str(record.get("thread_ref", "")):
            fail("no_reference",
                 f"the record does not point back at the chat it came from: {record!r}")
        moment = record.get("sent_at")
        parsed = None
        if isinstance(moment, str):
            try:
                parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
        elif isinstance(moment, datetime):
            parsed = moment
        if parsed is None:
            fail("no_timestamp", f"the record carries no parseable sent_at: {record!r}")
        elif parsed.tzinfo is None:
            fail("naive_timestamp",
                 "sent_at is timezone-naive; the resolver ranks this against Gmail and Bee purely "
                 "by time and a naive value mis-ranks them")

    # 5. Through observe(), a transport failure must degrade coverage, not vanish.
    def angry_request(method, path, params=None):
        raise RuntimeError(f"401 from {DSN} for {ACCOUNT}")

    observations, coverage = module.observe(
        [{"alias": "cand-1", "name": PERSON, "email": None}],
        client=client_class(DSN, TOKEN, ACCOUNT, request=angry_request))
    if observations:
        fail("observations_after_failure", "a failed read still produced observations")
    if getattr(coverage, "reached", None) is not False:
        fail("failure_reported_reached",
             "a transport failure was reported as a successful read, so an unreadable LinkedIn "
             "inbox would look like a LinkedIn inbox with nothing in it")
    detail = str(getattr(coverage, "detail", "") or "")
    if not detail.strip():
        fail("no_failure_detail", "coverage says unreached with no reason a human can act on")
    hit = leaked(detail)
    if hit is not None:
        fail("leak_in_coverage",
             f"the coverage detail repeats {hit[:32]!r}; that string is written into receipts")

    # 6. And a healthy read of an inbox with nobody relevant in it still clears.
    empty_observations, empty_coverage = module.observe(
        [{"alias": "cand-1", "name": PERSON, "email": None}],
        client=client_class(DSN, TOKEN, ACCOUNT, request=lambda *a, **k: {"items": []}))
    if empty_observations:
        fail("phantom_observations", "an empty chat list produced observations")
    if getattr(empty_coverage, "reached", None) is not True:
        fail("empty_read_not_reached",
             "an empty but successful read reported reached=False, which would hold every "
             "candidate forever for a channel that is working fine")


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
    check(worktree)

    if args.exported_summary:
        if not args.summary.is_file():
            fail("summary_missing", f"{args.summary} was not written")
        else:
            text = args.summary.read_text(encoding="utf-8").lower()
            missing = [s for s in SUMMARY_SECTIONS if s not in text]
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
        print("u16_linkedin_unipile_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u16_linkedin_unipile_check: PASS — the channel reads LinkedIn through Unipile, builds "
          "without credentials and without a network call, accepts the credentials either split or "
          "as one secret blob, turns chats into timezone-aware observations that carry neither "
          "message text nor credentials, reports a failed read as unreached with a reason, and "
          "still clears on a healthy empty inbox")
    return 0


if __name__ == "__main__":
    sys.exit(main())
