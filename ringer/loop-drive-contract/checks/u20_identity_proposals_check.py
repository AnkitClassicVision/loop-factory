#!/usr/bin/env python3
"""U20 executed check: an identity is proposed with evidence, never assumed.

Measured live 2026-08-11, after the feeder learned to split its own zero:

    considered 14 | selected 0 | awaiting LinkedIn identity 7
      6  warm candidate routes to email or text and has no address
      1  contact verdict CONTACTED; gmail saw the touch

Two different problems had been sharing one bucket. Seven cold candidates need
a LinkedIn profile before they can be routed. Six are WARM — referrals, the most
valuable records in the pool — accepted by intake with no way to reach them.

Both are the same shape: we have a name and we need an identity. And both have
the same failure mode, which is the only thing this check really guards. A wrong
match is not a missing feature, it is a message to a stranger who never heard of
Ankit, sent under his name, with a referral's name attached to it.

So the rule is: PROPOSE with evidence, never apply, and when the evidence does
not single somebody out, say so and propose nothing. An ambiguous match must be
worth less than no match, because no match costs a queue entry and a wrong match
costs the referral.

The artifact carries NAMES. It has to — a human cannot confirm "is this the
right person" against an alias. That is a deliberate boundary: receipts stay
alias-only and this file is a private review queue, written 0600, and it must
never carry note text or message bodies.

Usage: u20_identity_proposals_check.py --worktree <tree> [--owned PATH ...]
                                       [--patch OUT] [--summary fix-summary.md]
                                       [--exported-summary OUT]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")

WARM_NAME = "Grace Hopper"
COLD_NAME = "Ada Lovelace"
AMBIGUOUS_NAME = "John Smith"
REACHABLE_NAME = "Katherine Johnson"
REACHABLE_EMAIL = "katherine@example.invalid"
RIGHT_EMAIL = "grace@harborvision.invalid"
WRONG_EMAIL = "gracehopper@unrelated.invalid"
NOTE_TEXT = "referred by a past guest; Harbor Vision, three locations in Ohio"
PRIVATE_NOTE = "he mentioned his partner is buying him out, keep that quiet"

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


def inbox_fixture() -> dict:
    return {"candidates": [
        {"name": WARM_NAME, "email": None, "fit_score": 9,
         "source": "guest-acquisition-receipt", "note": NOTE_TEXT},
        {"name": COLD_NAME, "email": None, "fit_score": 8,
         "source": "guest-acquisition-receipt",
         "note": "solo practice, Cleveland; " + PRIVATE_NOTE},
        # WARM, so it routes to HubSpot — which is where the ambiguous pair
        # lives. An earlier revision of this file made this record cold while
        # planting the collision in HubSpot, and the only way to satisfy that
        # was to search HubSpot for every LinkedIn-bound candidate too. The
        # fixture must put the collision on the source the record is routed to.
        {"name": AMBIGUOUS_NAME, "email": None, "fit_score": 7,
         "source": "guest-acquisition-receipt", "note": "referred by a past guest"},
        {"name": REACHABLE_NAME, "email": REACHABLE_EMAIL, "fit_score": 6,
         "source": "guest-acquisition-receipt", "note": "referred by a past guest"},
    ]}


class FakeHubSpot:
    """One clean hit, one genuinely ambiguous pair, and nothing for the rest."""

    def __init__(self):
        self.queried: list[str] = []

    def search_by_name(self, name):
        self.queried.append(name)
        if name.strip().lower() == WARM_NAME.lower():
            return [{"name": WARM_NAME, "email": RIGHT_EMAIL,
                     "company": "Harbor Vision", "city": "Ohio"}]
        if name.strip().lower() == AMBIGUOUS_NAME.lower():
            return [{"name": AMBIGUOUS_NAME, "email": "js1@example.invalid",
                     "company": "Smith Eyecare", "city": "Dallas"},
                    {"name": AMBIGUOUS_NAME, "email": "js2@example.invalid",
                     "company": "Smith Optical", "city": "Denver"}]
        return []


class FakeUnipile:
    """Returns the RAW Unipile people-search shape, not a tidied one.

    Measured live 2026-08-11: an earlier revision of this fixture handed back
    records already carrying a single `name` field, so the code was never asked
    to cope with what Unipile actually sends — first_name and last_name as
    separate fields. A search that succeeded would then have matched nobody, and
    the check would have stayed green while the lane produced nothing.
    """

    def __init__(self):
        self.queried: list[str] = []

    def search_profiles(self, name, limit=10):
        self.queried.append(name)
        if name.strip().lower() == COLD_NAME.lower():
            first, last = COLD_NAME.split(" ", 1)
            return {"items": [{"provider_id": "li-ada", "first_name": first,
                               "last_name": last,
                               "headline": "Owner, Cleveland Eye Care",
                               "location": "Cleveland"}]}
        return {"items": []}


def check(worktree: Path) -> None:
    cli = load(worktree, "scripts/identity_proposals.py", "u20_identity_proposals")
    if cli is None:
        return
    if not hasattr(cli, "build_proposals"):
        fail("no_seam",
             "scripts/identity_proposals.py exposes no build_proposals(inbox, *, hubspot_client, "
             "unipile_client); there is no way to drive it without calling live APIs")
        return

    hubspot, unipile = FakeHubSpot(), FakeUnipile()
    try:
        report = cli.build_proposals(inbox_fixture(), hubspot_client=hubspot,
                                     unipile_client=unipile)
    except Exception as exc:  # noqa: BLE001
        fail("build_raised", f"{exc.__class__.__name__}: {exc}")
        return
    if not isinstance(report, dict):
        fail("bad_shape", f"build_proposals returned {type(report).__name__}, expected a dict")
        return

    proposals = report.get("proposals") or []
    unresolved = report.get("unresolved") or []
    by_name = {str(p.get("name", "")).lower(): p for p in proposals if isinstance(p, dict)}

    # 1. Somebody already reachable is never looked up. Asking two external
    #    systems about a person whose address we already hold discloses them for
    #    an answer nobody needs.
    if any(REACHABLE_NAME.lower() in q.lower() for q in hubspot.queried + unipile.queried):
        fail("looked_up_reachable",
             f"{REACHABLE_NAME} already has an address and was still searched for externally")
    if REACHABLE_NAME.lower() in by_name:
        fail("proposed_for_reachable", "a proposal was made for somebody already reachable")

    # 1b. A cold candidate is routed to LinkedIn, so HubSpot is never asked
    #     about them. Every name sent to a source is a disclosure, and one made
    #     to answer a question that source was not asked is one nobody agreed to.
    if any(COLD_NAME.lower() in q.lower() for q in hubspot.queried):
        fail("cold_disclosed_to_hubspot",
             f"a cold candidate's name was sent to HubSpot, which is not the source they route "
             f"to. HubSpot queries: {len(hubspot.queried)}")

    # 2. The warm referral gets an email proposal, with evidence and a real
    #    confidence — the whole point of the lane.
    warm = by_name.get(WARM_NAME.lower())
    if warm is None:
        fail("warm_not_proposed",
             f"HubSpot returned exactly one strong match for the warm referral and no proposal was "
             f"made. Proposals: {proposals!r}")
    else:
        if warm.get("channel") != "email":
            fail("warm_wrong_channel", f"warm referral proposed on {warm.get('channel')!r}")
        if RIGHT_EMAIL not in json.dumps(warm):
            fail("warm_wrong_value", f"the proposal does not carry the matched address: {warm!r}")
        if not str(warm.get("evidence", "")).strip():
            fail("warm_no_evidence",
                 "the proposal carries no evidence, so a human is being asked to rubber-stamp it")
        confidence = warm.get("confidence")
        if not isinstance(confidence, int) or not 0 <= confidence <= 100:
            fail("warm_no_confidence", f"confidence is {confidence!r}, expected an int 0-100")

    # 3. The cold candidate gets a LinkedIn proposal.
    cold = by_name.get(COLD_NAME.lower())
    if cold is None:
        fail("cold_not_proposed", f"one clean Unipile match produced no proposal: {proposals!r}")
    elif cold.get("channel") != "linkedin":
        fail("cold_wrong_channel", f"cold candidate proposed on {cold.get('channel')!r}")
    elif "li-ada" not in json.dumps(cold):
        fail("cold_no_identifier", f"the proposal carries no profile identifier: {cold!r}")

    # 4. THE ONE THAT MATTERS. Two plausible people, so propose nobody.
    if AMBIGUOUS_NAME.lower() in by_name:
        fail("ambiguous_proposed",
             f"two different people matched the same name and a proposal was made anyway: "
             f"{by_name[AMBIGUOUS_NAME.lower()]!r}. An ambiguous match is worth LESS than no "
             "match — no match costs a queue entry, a wrong match sends a stranger a message "
             "under Ankit's name with a referral attached to it")
    reasons = " ".join(str(u.get("reason", "")) for u in unresolved if isinstance(u, dict))
    if AMBIGUOUS_NAME.lower() not in json.dumps(unresolved).lower():
        fail("ambiguous_not_recorded",
             f"the ambiguous candidate is neither proposed nor recorded as unresolved, so they "
             f"vanish: {unresolved!r}")
    elif "ambig" not in reasons.lower() and "more than one" not in reasons.lower():
        fail("ambiguous_reason_thin",
             f"the unresolved entry does not say the match was ambiguous: {reasons!r}")

    # 4b. One lookup failing must not discard the lookups that worked.
    #     Measured live 2026-08-11: HubSpot answers four searches and then
    #     returns 429 "you have reached your secondly limit" on the fifth,
    #     because the generator fires them back to back. With a single try
    #     around the whole source loop, that one 429 threw away four good
    #     proposals and reported the channel unreadable. The run before it had
    #     produced five proposals from identical code, so the lane was flaky by
    #     construction rather than by circumstance.
    class RateLimited:
        """Answers the first lookup, then refuses the way a rate limiter does."""

        def __init__(self):
            self.calls = 0

        def search_by_name(self, name):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("HTTP Error 429: Too Many Requests")
            return [{"name": WARM_NAME, "email": RIGHT_EMAIL,
                     "company": "Harbor Vision", "city": "Ohio"}]

    limited = cli.build_proposals(inbox_fixture(), hubspot_client=RateLimited(),
                                  unipile_client=FakeUnipile())
    kept = [entry for entry in (limited.get("proposals") or [])
            if str(entry.get("name", "")).lower() == WARM_NAME.lower()]
    if not kept:
        fail("one_failure_discarded_the_rest",
             "a lookup that succeeded was thrown away because a LATER lookup on the same source "
             "failed. Somebody else's rate limit is not a reason to lose this person's answer. "
             f"Proposals kept: {limited.get('proposals')!r}")
    failed_reasons = " ".join(str(entry.get("reason", ""))
                              for entry in (limited.get("unresolved") or []))
    if "429" not in failed_reasons and "too many" not in failed_reasons.lower():
        fail("failure_status_hidden",
             f"the unresolved reason does not carry the HTTP status that explains it. A status "
             f"number is not sensitive, and it is the difference between 'retry in a moment' and "
             f"'this will never work'. Reasons seen: {failed_reasons!r}")

    # 5. Nothing is applied. Proposals are proposals.
    for proposal in proposals:
        if proposal.get("applied") or proposal.get("confirmed"):
            fail("auto_applied",
                 f"a proposal is marked applied/confirmed by the generator: {proposal!r}. Only a "
                 "human confirms an identity")

    # 6. Names are allowed here and note text is not.
    blob = json.dumps(report)
    if PRIVATE_NOTE.split(";")[0].strip().lower() in blob.lower() or PRIVATE_NOTE.lower() in blob.lower():
        fail("note_text_leaked",
             "intake note text reached the review artifact. Notes carry things people said in "
             "confidence; a proposal needs a name, a value and matching evidence, nothing else")

    # 7. A source that fails degrades that source only, and says so.
    class Angry:
        def search_by_name(self, name):
            raise RuntimeError("hubspot 401")

        def search_profiles(self, name, limit=10):
            raise RuntimeError("unipile 401")

    try:
        degraded = cli.build_proposals(inbox_fixture(), hubspot_client=Angry(),
                                       unipile_client=FakeUnipile())
    except Exception as exc:  # noqa: BLE001
        fail("failure_propagated",
             f"a failing source took the whole run down with {exc.__class__.__name__}: {exc}")
    else:
        coverage = {str(entry.get("source")): entry
                    for entry in (degraded.get("coverage") or []) if isinstance(entry, dict)}
        if "hubspot" not in coverage:
            fail("no_coverage_entry",
                 f"a source that raised produced no coverage entry: {degraded.get('coverage')!r}")
        elif coverage["hubspot"].get("reached") is not False:
            fail("failure_reported_reached",
                 "a source that raised was recorded as reached, so 'nobody matched' and 'we could "
                 "not look' are the same observation again")
        # The other source must still have done its job.
        if not any(str(p.get("name", "")).lower() == COLD_NAME.lower()
                   for p in (degraded.get("proposals") or [])):
            fail("healthy_source_lost",
                 "one source failing suppressed the other source's proposals")


def check_cli(worktree: Path) -> None:
    """Run the shipped entrypoint with no credentials. It must degrade, not crash."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        (root / "inbox.json").write_text(json.dumps(inbox_fixture()), encoding="utf-8")
        out = root / "proposals.json"
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("HUBSPOT_", "UNIPILE_", "LINKEDIN_"))}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.pop("PYTHONPATH", None)
        done = subprocess.run(
            [sys.executable, str(worktree / "scripts/identity_proposals.py"),
             "--inbox", str(root / "inbox.json"), "--out", str(out)],
            capture_output=True, text=True, timeout=300, cwd=str(worktree), env=env)

        if done.returncode != 0:
            fail("cli_failed",
                 f"exited {done.returncode} with no credentials. Missing credentials must produce "
                 f"an explained empty queue, not a crash: "
                 f"{(done.stdout + done.stderr).strip()[:400]}")
            return
        if not out.is_file():
            fail("cli_no_artifact", "the CLI wrote no proposals file")
            return
        mode = stat.S_IMODE(out.stat().st_mode)
        if mode & 0o077:
            fail("artifact_world_readable",
                 f"the review queue is mode {oct(mode)}; it carries real people's names and "
                 "addresses and must be 0600 like the other private artifacts here")
        payload = json.loads(out.read_text(encoding="utf-8"))
        if payload.get("proposals"):
            fail("proposed_without_credentials",
                 f"with no credentials at all the CLI still proposed "
                 f"{len(payload['proposals'])} identities")
        if not payload.get("coverage"):
            fail("no_coverage_recorded",
                 "the artifact records no coverage, so an empty queue caused by dead credentials "
                 "reads exactly like an empty queue caused by nobody matching")


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
    check_cli(worktree)

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
        print("u20_identity_proposals_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u20_identity_proposals_check: PASS — a warm referral gets an email proposal and a cold "
          "candidate a LinkedIn one, each with evidence and a confidence; two people sharing a name "
          "produce no proposal and a recorded reason; somebody already reachable is never looked "
          "up; note text never reaches the queue; one dead source degrades only itself; and the "
          "real CLI with no credentials writes an explained, private, empty queue")
    return 0


if __name__ == "__main__":
    sys.exit(main())
