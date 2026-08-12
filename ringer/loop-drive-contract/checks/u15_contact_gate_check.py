#!/usr/bin/env python3
"""U15 executed check: the contact resolver actually gates the candidate feed.

The resolver landed on 2026-08-10 and nothing called it. `contact_state.resolve`
was tested, four adapters were tested, and the feeder that decides who receives a
cold first-contact email still asked a string-matching guard over an intake note.
A verified component that no caller invokes protects nobody, and this is the
second time in this loop that a green seam measured a chain whose real user was
absent (U8 and U9 were the first).

So this check drives `build_candidates` ITSELF and asks the only question that
matters: given a picture of contact history, does the feeder hold or clear?

Nine scenarios, each of which has a way to fail:

  B1 build() records an exploding source as UNREACHED, never as empty.
  B2 build() merges observations from several sources under one alias.
  B3 build() only ships people who survived the cheap filters to the adapters.
  F1 incomplete coverage HOLDS      — the anti-laundering rule.
  F2 full coverage, empty history CLEARS — the anti-paranoia rule. Without this
     one, a feeder that holds everything passes every other scenario here and
     looks maximally safe while quietly ending all outreach.
  F3 a first-order touch HOLDS, and the drop names the source.
  F4 an intake note claiming an exchange HOLDS even under full clean coverage —
     the 2026-08-10 near-miss, now resolved through the model instead of a
     string match.
  F5 a ledger last_touch HOLDS the same way. A recording is not the event, and
     an uncorroborated recording may not be waved into a clean bill of health.
  F6 the drop report names the verdict AND the unreached sources, and carries no
     name, address, or note text.
  F7 the real CLI, run with no credentials at all, selects nobody and says why.
     This is the end-to-end proof and it also proves there is no fixture flag
     that could bypass the gate in production.

Usage:
  u15_contact_gate_check.py --worktree <tree> [--owned PATH ...]
                            [--patch OUT] [--summary fix-summary.md]
                            [--exported-summary OUT]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")

# Real people's details never appear in a receipt. These stand in for them, and
# F6 greps the report for every one of them.
PERSON_NAME = "Ada Lovelace"
PERSON_EMAIL = "ada@example.invalid"
NOTE_TEXT = "Gina Wesley referral; multi-location practice; introduction awaits reply"

FAILURES: list[str] = []


def fail(where: str, why: str) -> None:
    FAILURES.append(f"FAIL [{where}]: {why}")


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def load(worktree: Path, relative: str, module_name: str):
    """Import a module out of the worktree under test, by path."""
    path = worktree / relative
    if not path.is_file():
        fail("missing_file", f"{relative} does not exist in {worktree}")
        return None
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        fail("unimportable", f"{relative} could not be loaded as a module")
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - the reason is the product here
        fail("import_error", f"{relative} raised {exc.__class__.__name__}: {exc}")
        return None
    return module


# ------------------------------------------------------------------ fixtures
def inbox_record(*, note: str = "referred by a past guest", email: str = PERSON_EMAIL) -> dict:
    return {"candidates": [{"name": PERSON_NAME, "email": email, "fit_score": 9,
                            "source": "guest-acquisition-receipt", "note": note}]}


def ledger(*, last_touch=None, stage: str = "new_inbound") -> dict:
    person: dict = {"name": PERSON_NAME, "stage": stage}
    person["last_touch"] = last_touch if last_touch is not None else {"at": None}
    return {"people": [person]}


class StubIndex:
    """A controlled picture of contact history, resolved by the REAL resolver.

    The feeder is never handed a canned verdict. It is handed evidence, and the
    production `contact_state.resolve` turns that evidence into the verdict — so
    a feeder that passes this check is wired to the shipped model, not to a mock
    that agrees with it.
    """

    def __init__(self, contact_state, coverage, observations=None):
        self._contact_state = contact_state
        self.coverage = list(coverage)
        self.observations = dict(observations or {})
        self.resolved: list[str] = []
        self.extras: dict[str, tuple] = {}

    def resolve(self, alias, extra_observations=()):
        self.resolved.append(alias)
        extra = tuple(extra_observations)
        self.extras[alias] = extra
        evidence = list(self.observations.get(alias, [])) + list(extra)
        return self._contact_state.resolve(evidence, self.coverage)


def coverage_for(contact_state, reached: dict[str, bool]):
    return [contact_state.SourceCoverage(source=name, reached=state,
                                         checked_at=datetime.now(timezone.utc),
                                         detail="" if state else "credentials unavailable")
            for name, state in reached.items()]


ALL_REACHED = {"gmail": True, "linkedin": True, "bee": True, "hubspot": True}


def feed(feeder, index, *, inbox=None, led=None, limit: int = 5):
    """Call build_candidates the way the runner does, plus the injected index."""
    return feeder.build_candidates(
        inbox if inbox is not None else inbox_record(),
        led if led is not None else ledger(),
        date(2026, 8, 11), limit, contact_index=index)


# ------------------------------------------------------------------ build()
def check_build(worktree: Path, contact_state) -> None:
    index_module = load(worktree, "server/pipeline/contact_index.py", "u15_contact_index")
    if index_module is None:
        return
    if not hasattr(index_module, "build"):
        fail("build_missing", "contact_index.py defines no build(); the feeder has nothing to call")
        return

    people = [{"alias": "cand-a", "name": PERSON_NAME, "email": PERSON_EMAIL}]
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    touch = now - timedelta(days=30)

    def good(_people, *, client=None, now=None):
        return ({"cand-a": [contact_state.Observation(source="gmail", at=touch,
                                                      evidence_ref="gmail:t1")]},
                contact_state.SourceCoverage(source="gmail", reached=True, checked_at=now))

    def also_good(_people, *, client=None, now=None):
        return ({"cand-a": [contact_state.Observation(source="bee", at=touch + timedelta(days=1),
                                                      evidence_ref="bee:c9")]},
                contact_state.SourceCoverage(source="bee", reached=True, checked_at=now))

    def explodes(_people, *, client=None, now=None):
        raise RuntimeError("the linkedin export was not where it was last time")

    sources = [SimpleNamespace(SOURCE_NAME="gmail", observe=good),
               SimpleNamespace(SOURCE_NAME="bee", observe=also_good),
               SimpleNamespace(SOURCE_NAME="linkedin", observe=explodes)]

    try:
        index = index_module.build(people, sources=sources, now=now)
    except Exception as exc:  # noqa: BLE001
        fail("build_raised",
             f"a single failing source took the whole index down with "
             f"{exc.__class__.__name__}: {exc}. One unreadable channel must degrade coverage, "
             "not abort the run")
        return

    coverage = {entry.source: entry for entry in getattr(index, "coverage", [])}
    # B1. The rule the whole model rests on: not-read is not nothing-there.
    if "linkedin" not in coverage:
        fail("B1_exploding_source_vanished",
             "a source whose observe() raised produced no coverage entry at all, so the resolver "
             f"cannot know linkedin went unread. Coverage reported: {sorted(coverage)}")
    elif coverage["linkedin"].reached:
        fail("B1_exploding_source_reported_reached",
             "a source whose observe() raised was recorded as reached=True. A failed read that "
             "looks like an empty channel is exactly how a cold email reaches somebody we already "
             "emailed")
    elif not (coverage["linkedin"].detail or "").strip():
        fail("B1_no_reason", "linkedin is reached=False with an empty detail; a human reading the "
                             "receipt cannot tell why the channel was unread")

    # B2. Two sources, one person, both observations kept.
    merged = getattr(index, "observations", {}).get("cand-a", [])
    seen = {getattr(item, "source", None) for item in merged}
    if {"gmail", "bee"} - seen:
        fail("B2_observations_lost",
             f"build() kept {sorted(s for s in seen if s)} for cand-a but both gmail and bee "
             "reported a touch; a dropped observation silently rewinds the last-contact date")

    # And the aggregate must still resolve to CONTACTED through the real model.
    if hasattr(index, "resolve"):
        verdict = index.resolve("cand-a")
        if verdict.verdict != contact_state.CONTACTED:
            fail("B2_resolve_wrong",
                 f"two first-order touches resolved to {verdict.verdict!r}, expected CONTACTED")
    else:
        fail("resolve_missing", "the index exposes no resolve(alias, extra_observations=()); "
                                "the feeder has no way to ask about one person")


# ------------------------------------------------------------------ feeder
def check_feeder(worktree: Path, contact_state) -> None:
    feeder = load(worktree, "scripts/guest_candidate_feed.py", "u15_guest_candidate_feed")
    if feeder is None:
        return

    # The alias is the person's identity everywhere downstream: it becomes
    # candidate_key in the draft receipts and in the ledger that Ankit's
    # first-draft gate counts. It is derived from the NAME, so the same person
    # is the same key on Tuesday as on Monday, whatever order the inbox arrives
    # in. This check therefore asks the feeder what the alias is rather than
    # inventing one — an earlier revision of this file keyed its fixtures on a
    # made-up 'cand-a', and the only way to satisfy that was to replace the
    # stable alias with a positional one, silently breaking identity across runs.
    alias = feeder.alias_for(feeder.normalized_name(PERSON_NAME))

    # F2 first. If this fails, every "safe" result below is worthless, because a
    # feeder that holds unconditionally satisfies all the holding scenarios.
    clean = StubIndex(contact_state, coverage_for(contact_state, ALL_REACHED))
    try:
        selected, report = feed(feeder, clean)
    except TypeError as exc:
        fail("not_wired",
             f"build_candidates(..., contact_index=<index>) raised {exc}. The resolver is still "
             "not wired into the feeder; that wiring is the whole task")
        return
    except Exception as exc:  # noqa: BLE001
        fail("feeder_raised", f"{exc.__class__.__name__}: {exc}")
        return

    if len(selected) != 1:
        fail("F2_clean_history_held",
             f"all four first-order sources were read, none had any record of this person, and "
             f"the feeder still selected {len(selected)} of 1. A gate that never clears is not a "
             f"safe gate, it is an outage. Drops: {report.get('dropped')}")
    if not clean.resolved:
        fail("F2_resolver_unused",
             "the feeder returned a result without asking the contact index about anybody, so the "
             "verdict played no part in the decision")
    elif clean.resolved != [alias]:
        fail("F8_alias_not_stable",
             f"the feeder identified this person to the contact index as {clean.resolved!r}, but "
             f"their stable alias is {alias!r}. The alias is derived from the name for a reason: "
             "it becomes candidate_key in the draft ledger, and a key that moves between runs "
             "means the first-draft gate cannot recognise a person it already drafted")
    if selected and selected[0].get("alias") != alias:
        fail("F8_emitted_alias_wrong",
             f"the selected candidate went out as {selected[0].get('alias')!r} instead of the "
             f"stable {alias!r}")

    # F8b. The same person, arriving second in the inbox, is the same person.
    shuffled = StubIndex(contact_state, coverage_for(contact_state, ALL_REACHED))
    two = {"candidates": [{"name": "Grace Hopper", "email": "grace@example.invalid",
                           "fit_score": 3, "note": "referred"},
                          {"name": PERSON_NAME, "email": PERSON_EMAIL, "fit_score": 9,
                           "note": "referred"}]}
    two_people = {"people": [{"name": "Grace Hopper", "stage": "new_inbound",
                              "last_touch": {"at": None}},
                             {"name": PERSON_NAME, "stage": "new_inbound",
                              "last_touch": {"at": None}}]}
    picked, _ = feed(feeder, shuffled, inbox=two, led=two_people)
    moved = [entry for entry in picked if entry.get("alias") == alias]
    if not moved:
        fail("F8_alias_moved_with_position",
             f"the same person selected from position 2 came out as "
             f"{[entry.get('alias') for entry in picked]!r}, not {alias!r}. An alias that depends "
             "on inbox order is not an identity")

    # F1. Three of four read is not a clean history.
    partial = StubIndex(contact_state, coverage_for(
        contact_state, {**ALL_REACHED, "gmail": False}))
    selected, report = feed(feeder, partial)
    if selected:
        fail("F1_unknown_cleared",
             "gmail could not be read, the verdict was UNKNOWN, and the feeder selected the "
             "candidate anyway. That launders absence of evidence into evidence of absence and "
             "hands it a receipt")
    reasons = " ".join(entry.get("reason", "") for entry in report.get("dropped", []))
    if "unknown" not in reasons.lower():
        fail("F1_verdict_unnamed",
             f"the candidate was held but no drop reason names the UNKNOWN verdict. Reason text: "
             f"{reasons!r}")
    if "gmail" not in reasons.lower():
        fail("F1_unreached_unnamed",
             f"the drop reason does not say WHICH source went unread, so nobody can go fix it. "
             f"Reason text: {reasons!r}")

    # F3. A real touch, seen by a first-order source.
    contacted = StubIndex(
        contact_state, coverage_for(contact_state, ALL_REACHED),
        {alias: [contact_state.Observation(
            source="bee", at=datetime(2026, 8, 4, tzinfo=timezone.utc), evidence_ref="bee:c9")]})
    selected, report = feed(feeder, contacted)
    if selected:
        fail("F3_contacted_cleared",
             "a first-order source recorded a touch and the feeder still routed this person "
             "through the cold-open template")
    reasons = " ".join(entry.get("reason", "") for entry in report.get("dropped", []))
    if "contacted" not in reasons.lower() or "bee" not in reasons.lower():
        fail("F3_reason_thin",
             f"the drop names neither the CONTACTED verdict nor the source that saw it: {reasons!r}")

    # F4. The 2026-08-10 near-miss, now routed through the model.
    noted = StubIndex(contact_state, coverage_for(contact_state, ALL_REACHED))
    selected, report = feed(feeder, noted, inbox=inbox_record(note=NOTE_TEXT))
    if selected:
        fail("F4_note_ignored",
             "coverage was complete and empty, the intake note said an introduction awaits reply, "
             "and the feeder cleared the cold open. This is the exact defect the resolver exists "
             "to prevent, reintroduced one layer up")
    extras = noted.extras.get(alias, ())
    if not any(getattr(item, "source", "") in contact_state.SECOND_ORDER_SOURCES for item in extras):
        fail("F4_note_not_evidence",
             "the intake note never reached the resolver as a second-order observation, so the "
             "hold (if any) came from a leftover string guard rather than from the authority "
             f"model. Extras passed: {[getattr(i, 'source', None) for i in extras]}")
    if not any((getattr(item, "grounding", "") or "").strip() for item in extras):
        fail("F4_no_grounding",
             "the second-order observation carries no grounding, and the owner's ruling is that "
             "every ledger row needs grounding")

    # F5. A recording of a touch is still not nothing.
    recorded = StubIndex(contact_state, coverage_for(contact_state, ALL_REACHED))
    selected, report = feed(feeder, recorded,
                            led=ledger(last_touch={"at": "2026-05-01T10:00:00+00:00",
                                                   "direction": "outbound", "source": "email"}))
    if selected:
        fail("F5_stale_ledger_cleared",
             "the funnel ledger recorded an outbound touch in May, no first-order source "
             "corroborated it, and the feeder cleared a cold introduction anyway. An "
             "uncorroborated recording holds; it never clears")

    # F3b. People the cheap filters already rejected must never be shipped to an
    # external contact API. Sending a rejected person's address to HubSpot and
    # Gmail to answer a question nobody asked is an avoidable disclosure.
    watcher = StubIndex(contact_state, coverage_for(contact_state, ALL_REACHED))
    no_email = {"candidates": [{"name": "Grace Hopper", "email": "", "fit_score": 9,
                                "note": "referred"},
                               {"name": PERSON_NAME, "email": PERSON_EMAIL, "fit_score": 8,
                                "note": "referred"}]}
    feed(feeder, watcher, inbox=no_email,
         led={"people": [{"name": "Grace Hopper", "stage": "new_inbound",
                          "last_touch": {"at": None}},
                         {"name": PERSON_NAME, "stage": "new_inbound",
                          "last_touch": {"at": None}}]})
    if len(watcher.resolved) > 1:
        fail("B3_over_resolution",
             f"the feeder resolved {len(watcher.resolved)} people although only one survived the "
             "email/hold/stage/cadence filters. Contact lookups disclose an address to four "
             "external systems; do not run them for records already dropped")

    # F6. The receipt is public-ish. It carries aliases and reasons, never people.
    blob = json.dumps(report)
    for secret in (PERSON_NAME, PERSON_EMAIL, NOTE_TEXT):
        if secret.lower() in blob.lower():
            fail("F6_pii_in_report",
                 f"the drop report contains {secret[:32]!r}. Reports name records by alias only")
    if "contact_coverage" not in report:
        fail("F6_no_coverage_receipt",
             "the report has no contact_coverage block, so a run where three of four channels "
             "were unreadable is indistinguishable from a healthy one at review time")


# ------------------------------------------------------------------ the CLI
def check_cli(worktree: Path) -> None:
    """Run the shipped entrypoint with no credentials. It must refuse to clear."""
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        (root / "inbox.json").write_text(json.dumps(inbox_record()), encoding="utf-8")
        (root / "ledger.json").write_text(json.dumps(ledger()), encoding="utf-8")
        out, reasons = root / "candidates.json", root / "candidates.reasons.json"
        env = {k: v for k, v in os.environ.items()
               if k not in ("GMAIL_CREDENTIALS_PATH", "GMAIL_FULL_TOKEN_PATH",
                            "HUBSPOT_API_KEY", "BEE_API_KEY", "LINKEDIN_EXPORT_PATH")}
        # Deliberately NO PYTHONPATH. The scheduled runner does not set one, and
        # Python puts the SCRIPT's directory on sys.path, not the working
        # directory — so a feeder that imports server.pipeline dies at the import
        # line under systemd while passing a check that helpfully set the path.
        # It did exactly that on 2026-08-11, and only a live run caught it. A
        # check may control the inputs; it may not make the environment kinder
        # than production.
        env.update({"PYTHONDONTWRITEBYTECODE": "1"})
        env.pop("PYTHONPATH", None)
        done = subprocess.run(
            [sys.executable, str(worktree / "scripts/guest_candidate_feed.py"),
             "--inbox", str(root / "inbox.json"), "--ledger", str(root / "ledger.json"),
             "--out", str(out), "--reasons", str(reasons), "--now", "2026-08-11"],
            capture_output=True, text=True, timeout=300, cwd=str(worktree), env=env)

        if done.returncode != 0:
            fail("F7_cli_failed",
                 f"the feeder exited {done.returncode} with no credentials present. Missing "
                 f"credentials must produce an explained zero, not a crash: "
                 f"{(done.stdout + done.stderr).strip()[:400]}")
            return
        if not out.is_file() or not reasons.is_file():
            fail("F7_no_artifacts", "the CLI wrote no candidates and/or no reasons file")
            return
        selected = json.loads(out.read_text(encoding="utf-8"))
        report = json.loads(reasons.read_text(encoding="utf-8"))
        if selected:
            fail("F7_cleared_without_credentials",
                 f"with every contact credential removed from the environment, the real CLI still "
                 f"selected {len(selected)} candidate(s) for outreach. Either the gate is not on "
                 f"the production path, or something bypasses it")
        text = json.dumps(report).lower()
        if "unknown" not in text:
            fail("F7_zero_unexplained",
                 f"no contact channel was readable and the reasons file never says the history "
                 f"was UNKNOWN, so this run is indistinguishable from a real drought: {text[:400]}")


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
    contact_state = load(worktree, "server/pipeline/contact_state.py", "server.pipeline.contact_state")
    if contact_state is not None:
        check_build(worktree, contact_state)
        check_feeder(worktree, contact_state)
        check_cli(worktree)

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
        print("u15_contact_gate_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u15_contact_gate_check: PASS — the feeder resolves every surviving candidate through "
          "the shipped authority model: incomplete coverage holds, a first-order touch holds, an "
          "intake note and a ledger row hold as uncorroborated second-order claims, a genuinely "
          "clean and fully-covered history still clears, the receipt names the verdict and the "
          "unreached channels without naming the person, and the real CLI with no credentials "
          "selects nobody and says so")
    return 0


if __name__ == "__main__":
    sys.exit(main())
