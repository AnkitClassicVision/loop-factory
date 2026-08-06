#!/usr/bin/env python3
"""Validator for round 1 of the loop-drive-contract job: the read-only U2d audit.

Three lanes, three report shapes. Every lane is checked for substance, not
format: the required subjects must all be present, every file:line citation must
resolve against the real file, and every quote must actually appear in the file
it cites. That is the anti-fabrication core, because a polished audit that
invents a line number is worse than no audit.

Exit 0 only when every assertion passes. Every failure prints WHY, because the
failure text is what the retry prompt gets.

Usage:
  audit_check.py --lane A --report gate-audit.json
  audit_check.py --lane B --report module-audit.json
  audit_check.py --lane C --report draft-creation-audit.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PODCAST_REPO = Path("/mnt/d_drive/repos/podcast")
RUNBOOK = Path(
    "/home/ankit114/repos/Ankit-open-skills/skill-library/runbooks/operations"
    "/podcast-guest-acquisition-runbook/RUNBOOK.md"
)

# The 12 gate ids in the runbook's Gates table, extracted from the file on
# 2026-08-06. Completeness is the point of lane A: a report that silently drops
# a gate is the failure mode this list exists to catch.
GATES = [
    "source_truth_resolved_before_intake",
    "fit_scored_before_route",
    "channel_rule_cold_postcard_linkedin_warm_email_text",
    "referrer_episode_link_required_for_referral_outreach",
    "flagship_required_for_human_communication_output",
    "gmail_calendar_hubspot_context_before_communication_draft",
    "draft_only_no_send_without_human_approval",
    "never_set_nominated_status_on_manual_outreach",
    "neutralize_preexisting_nominated_before_draft",
    "send_harness_active_or_no_send_tools",
    "search_hubspot_name_and_email_before_contact_write",
    "cross_model_qa_pass_before_done",
]

MODULES = [
    "obe_draft_voice_qa.py",
    "date_safety.py",
    "crm_write_policy.py",
    "capability_preflight.py",
    "content_qa.py",
]

CLASSIFICATIONS = {"code", "prose", "absent"}

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def resolve_path(raw: str) -> Path | None:
    """Accept an absolute path, a podcast-repo-relative path, or the runbook."""
    candidate = Path(raw)
    if candidate.is_absolute() and candidate.is_file():
        return candidate
    rel = PODCAST_REPO / raw
    if rel.is_file():
        return rel
    if "RUNBOOK.md" in raw and RUNBOOK.is_file():
        return RUNBOOK
    return None


def check_citations(where: str, citations: object, *, minimum: int) -> None:
    """A citation is {path, line, quote}. All three must survive contact with disk."""
    if not isinstance(citations, list) or len(citations) < minimum:
        fail(
            f"{where}: needs at least {minimum} evidence citation(s), got "
            f"{len(citations) if isinstance(citations, list) else type(citations).__name__}. "
            f"Each citation is an object with path, line, and quote."
        )
        return
    for i, cite in enumerate(citations):
        tag = f"{where}: evidence[{i}]"
        if not isinstance(cite, dict):
            fail(f"{tag}: not an object; expected keys path, line, quote")
            continue
        raw_path = str(cite.get("path", "")).strip()
        if not raw_path:
            fail(f"{tag}: missing 'path'")
            continue
        resolved = resolve_path(raw_path)
        if resolved is None:
            fail(
                f"{tag}: path {raw_path!r} does not exist. Cite a real file, either "
                f"absolute or relative to {PODCAST_REPO}."
            )
            continue
        try:
            lines = resolved.read_text(errors="replace").splitlines()
        except OSError as exc:
            fail(f"{tag}: cannot read {resolved}: {exc}")
            continue
        line_no = cite.get("line")
        if not isinstance(line_no, int) or line_no < 1:
            fail(f"{tag}: 'line' must be a positive integer, got {line_no!r}")
        elif line_no > len(lines):
            fail(
                f"{tag}: cites {resolved.name}:{line_no} but that file has only "
                f"{len(lines)} lines. This is the fabricated-citation failure."
            )
        quote = str(cite.get("quote", ""))
        if len(_norm(quote)) < 8:
            fail(
                f"{tag}: 'quote' must be at least 8 characters of real text copied "
                f"from the file, got {quote!r}"
            )
        elif _norm(quote) not in _norm(resolved.read_text(errors="replace")):
            fail(
                f"{tag}: quote {_norm(quote)[:90]!r} does not appear anywhere in "
                f"{resolved}. Copy the text verbatim; do not paraphrase it."
            )


def check_searches(report: dict, *, minimum: int) -> None:
    searches = report.get("searches_run")
    if not isinstance(searches, list) or len(searches) < minimum:
        fail(
            f"searches_run: needs at least {minimum} entries recording the actual "
            f"grep/rg/find commands you ran, got "
            f"{len(searches) if isinstance(searches, list) else type(searches).__name__}. "
            f"An audit with no recorded searches is unfalsifiable."
        )
        return
    for i, cmd in enumerate(searches):
        if not isinstance(cmd, str) or len(cmd.strip()) < 6:
            fail(f"searches_run[{i}]: not a real command string: {cmd!r}")


def lane_a(report: dict) -> None:
    gates = report.get("gates")
    if not isinstance(gates, list):
        fail("gates: missing or not a list")
        return
    seen: dict[str, int] = {}
    for i, entry in enumerate(gates):
        if not isinstance(entry, dict):
            fail(f"gates[{i}]: not an object")
            continue
        name = str(entry.get("gate", "")).strip()
        if not name:
            fail(f"gates[{i}]: missing 'gate' id")
            continue
        if name in seen:
            fail(f"gates[{i}]: duplicate gate id {name!r} (already at index {seen[name]})")
            continue
        seen[name] = i
        if name not in GATES:
            fail(
                f"gates[{i}]: {name!r} is not one of the runbook's 12 gate ids. "
                f"Do not invent or rename gates."
            )
            continue
        classification = str(entry.get("classification", "")).strip().lower()
        if classification not in CLASSIFICATIONS:
            fail(
                f"gate {name}: classification must be one of "
                f"{sorted(CLASSIFICATIONS)}, got {classification!r}"
            )
            continue
        if classification == "code":
            check_citations(f"gate {name}", entry.get("evidence"), minimum=1)
            if not isinstance(entry.get("returns_structured_failure"), bool):
                fail(
                    f"gate {name}: classified 'code', so "
                    f"'returns_structured_failure' must be true or false. This is "
                    f"the field U2d exists to produce."
                )
        elif classification == "prose":
            cites = entry.get("evidence")
            check_citations(f"gate {name}", cites, minimum=1)
            if isinstance(cites, list) and cites and not any(
                "RUNBOOK" in str(c.get("path", "")) for c in cites if isinstance(c, dict)
            ):
                fail(
                    f"gate {name}: classified 'prose', so at least one citation must "
                    f"point into RUNBOOK.md where the prose lives."
                )
        else:  # absent
            if len(_norm(str(entry.get("notes", "")))) < 20:
                fail(
                    f"gate {name}: classified 'absent', so 'notes' must explain in at "
                    f"least 20 characters where you looked and what you concluded."
                )
    missing = [g for g in GATES if g not in seen]
    if missing:
        fail(
            f"gates: {len(missing)} of 12 runbook gates are missing from the report: "
            f"{', '.join(missing)}"
        )
    check_searches(report, minimum=3)


def lane_b(report: dict) -> None:
    modules = report.get("modules")
    if not isinstance(modules, list):
        fail("modules: missing or not a list")
        return
    seen: dict[str, dict] = {}
    for i, entry in enumerate(modules):
        if not isinstance(entry, dict):
            fail(f"modules[{i}]: not an object")
            continue
        name = str(entry.get("module", "")).strip()
        if name not in MODULES:
            fail(
                f"modules[{i}]: {name!r} is not one of the 5 modules under audit "
                f"({', '.join(MODULES)})"
            )
            continue
        seen[name] = entry
        raw_path = str(entry.get("path", "")).strip()
        if resolve_path(raw_path) is None:
            fail(f"module {name}: path {raw_path!r} does not exist on disk")
        enforces = entry.get("enforces")
        if not isinstance(enforces, list) or not enforces:
            fail(
                f"module {name}: 'enforces' must be a non-empty list of what this "
                f"module actually checks"
            )
        if len(_norm(str(entry.get("return_shape", "")))) < 10:
            fail(
                f"module {name}: 'return_shape' must describe what the module hands "
                f"back to its caller, in at least 10 characters. This is what "
                f"decides whether it can join the repair loop."
            )
        if not isinstance(entry.get("has_revise_loop"), bool):
            fail(f"module {name}: 'has_revise_loop' must be true or false")
        check_citations(f"module {name}", entry.get("evidence"), minimum=1)

    missing = [m for m in MODULES if m not in seen]
    if missing:
        fail(f"modules: missing from the report: {', '.join(missing)}")

    # Ground-truth anchor, verified by hand on 2026-08-06: obe_draft_voice_qa.py
    # contains `for iteration in (1, 2):` and rewrites the draft between
    # iterations. A report that says otherwise did not read the file.
    anchor = seen.get("obe_draft_voice_qa.py")
    if anchor is not None:
        if anchor.get("has_revise_loop") is not True:
            fail(
                "module obe_draft_voice_qa.py: reported has_revise_loop="
                f"{anchor.get('has_revise_loop')!r}, but the file contains "
                "'for iteration in (1, 2):' and calls update_draft_body() with a "
                "rewrite inside that loop. Read the file again."
            )
        if anchor.get("max_iterations") != 2:
            fail(
                "module obe_draft_voice_qa.py: reported max_iterations="
                f"{anchor.get('max_iterations')!r}; the implemented loop is "
                "'for iteration in (1, 2):', so the answer is 2."
            )
    check_searches(report, minimum=3)


def lane_c(report: dict) -> None:
    creators = report.get("creators")
    if not isinstance(creators, list):
        fail("creators: missing or not a list")
    elif not creators:
        # Anchor: draft-creating code demonstrably exists in this repo, so an
        # empty list is a search failure, not a finding.
        fail(
            "creators: empty. Gmail draft creation code exists in this repo (search "
            "for 'drafts().create' under server/pipeline and scripts). An empty "
            "list means the search was too narrow, not that no creator exists."
        )
    else:
        for i, entry in enumerate(creators):
            if not isinstance(entry, dict):
                fail(f"creators[{i}]: not an object")
                continue
            if len(_norm(str(entry.get("symbol", "")))) < 3:
                fail(f"creators[{i}]: 'symbol' must name the function or call site")
            check_citations(f"creators[{i}]", entry.get("evidence"), minimum=1)

    if not isinstance(report.get("reachable_from_run_podcast_loop"), bool):
        fail(
            "reachable_from_run_podcast_loop: must be true or false. This single "
            "boolean is what U2a depends on: can a loop run reach draft creation "
            "today, or not."
        )
    check_citations(
        "reachability_evidence", report.get("reachability_evidence"), minimum=1
    )
    check_searches(report, minimum=4)


LANES = {"A": ("gates", lane_a), "B": ("modules", lane_b), "C": ("creators", lane_c)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", required=True, choices=sorted(LANES))
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    path = Path(args.report)
    if not path.is_file():
        print(f"FAIL: report file {path} was never written", file=sys.stderr)
        return 1
    raw = path.read_text(errors="replace")
    if not raw.strip():
        print(f"FAIL: report file {path} is empty", file=sys.stderr)
        return 1
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(
            f"FAIL: {path} is not valid JSON: {exc}. Write strict JSON with no "
            f"markdown fences.",
            file=sys.stderr,
        )
        return 1
    if not isinstance(report, dict):
        print(f"FAIL: {path} must be a JSON object, got {type(report).__name__}", file=sys.stderr)
        return 1

    _, runner = LANES[args.lane]
    runner(report)

    if failures:
        print(f"FAIL: lane {args.lane} audit has {len(failures)} problem(s):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"PASS: lane {args.lane} audit is complete and every citation resolves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
