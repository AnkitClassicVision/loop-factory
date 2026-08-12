#!/usr/bin/env python3
"""U0 check: no success verdict without machine corroboration.

U1 moved the verdict from something the worker typed to something computed. It did
not move the INPUTS: `cap`, `blocked_by` and `quota.met` are still fields the worker
writes, and each one buys a success verdict outright. `cap: "yes"` returns CAPPED.
`blocked_by: "x"` returns BLOCKED. `quota.met: true` stops re-entry. Only EXHAUSTED
was made to pay for itself, with per-candidate evidence.

Worse, the verdict is final before anything can corroborate it. It is computed
inside the worker task's check (run_podcast_loop.sh:320), which is before the
reviewer runs and before the post-QA action runs. So a reviewer who catches a
fabricated disqualifier changes nothing: `QA: REVISE` pings Telegram and the run
exits 0 with EXHAUSTED standing.

U0 closes both. Every success verdict must be corroborated by something the runner
observed, passed in as a flag the worker cannot write:

    python3 scripts/obe_loop_verdict.py --receipt <path> [--apply]
        [--qa-verdict PASS|REVISE|BLOCK]   the reviewer's actual verdict
        [--blocked-by <loop>]              the ONLY source of BLOCKED
        [--sends-proof <path>]             an artifact proving sends happened
        [--quota-target <int>]             from the charter, not from the receipt

  DROVE      needs sends > 0 AND --sends-proof corroborating at least one send.
  CAPPED     needs cap.ceiling to name a real charter ceiling.
  BLOCKED    comes only from --blocked-by. The receipt's own field is ignored.
  EXHAUSTED  unchanged, plus repo-path evidence must resolve to a real file.
  FAILED     everything else, including every uncorroborated claim.

  Any success verdict demotes to FAILED when --qa-verdict is REVISE or BLOCK.

The control-flow half is verified by executing the real runner through
scripts/loop_shadow_run.py, because three units of this job proved that reading the
script does not tell you what it runs.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SUMMARY_SECTIONS = ("# Fix Summary", "## Summary", "## Files Changed", "## Verification")
CHARTER = Path("/mnt/d_drive/repos/loop-factory/departments/podcast/charter.yaml")
# Ceilings that exist in the charter. A cap naming anything else is not a ceiling.
REAL_CEILINGS = ("outbound_per_day", "new_contacts_per_day", "per_contact_cadence_floor_days",
                 "worker_minutes", "weekly_touch_ceiling")
FAKE_CEILING = "vibes_per_fortnight"

CITED = {"alias": "cand-1", "eligible": False,
         "disqualifier": "inside the 4-day per-contact cadence floor",
         "evidence": "scripts/run_podcast_loop.sh"}
CITED_BAD_PATH = {"alias": "cand-3", "eligible": False,
                  "disqualifier": "not a fit", "evidence": "scripts/there_is_no_such_file.py"}
ELIGIBLE = {"alias": "cand-2", "eligible": True, "disqualifier": None, "evidence": None}


def block(sends=0, candidates=None, cap=None, blocked_by=None, quota_met=False):
    return {"sends": sends, "cap": cap, "blocked_by": blocked_by,
            "quota": {"target": 2, "met": quota_met},
            "candidates": candidates if candidates is not None else []}


def receipt_text(blk: dict) -> str:
    filler = "\n".join(f"{n}. Step {n} ran; evidence counted and checked." for n in range(1, 12))
    return (
        "# OBE Loop Receipt, fixture\n\nMode: READ/PROPOSE ONLY\n\n"
        "```loop-drive-v1\n" + json.dumps(blk, indent=2) + "\n```\n\n" + filler + "\n"
    )


def drive(worktree: Path, receipt: Path, extra: list[str]) -> tuple[dict | None, str]:
    result = subprocess.run(
        [sys.executable, "scripts/obe_loop_verdict.py", "--receipt", str(receipt), *extra],
        capture_output=True, text=True, timeout=120, cwd=str(worktree),
    )
    for line in reversed(result.stdout.strip().splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line), ""
            except json.JSONDecodeError:
                continue
    return None, (f"no JSON on stdout (exit {result.returncode})\n"
                  f"stdout:\n{result.stdout[-900:]}\nstderr:\n{result.stderr[-900:]}")


def case(worktree: Path, tmp: Path, name: str, blk: dict, extra: list[str],
         expect: str, why: str) -> list[str]:
    receipt = tmp / f"{name}.md"
    receipt.write_text(receipt_text(blk), encoding="utf-8")
    got, detail = drive(worktree, receipt, extra)
    if got is None:
        return [f"FAIL [{name}/output_contract]: {detail}"]
    if str(got.get("verdict", "")).upper() != expect:
        return [f"FAIL [{name}]: expected {expect}, got {got.get('verdict')!r}. {why}."]
    return []


def check_module(worktree: Path, tmp: Path) -> list[str]:
    if not (worktree / "scripts/obe_loop_verdict.py").is_file():
        return ["FAIL [module_missing]: scripts/obe_loop_verdict.py does not exist"]
    proof = tmp / "sends-proof.json"
    proof.write_text(json.dumps({"sent": True, "drafts_created": 1, "sends": 1}), encoding="utf-8")
    empty_proof = tmp / "no-sends-proof.json"
    empty_proof.write_text(json.dumps({"sent": False, "drafts_created": 0, "sends": 0}), encoding="utf-8")

    failures: list[str] = []
    failures += case(
        worktree, tmp, "cap_must_name_a_real_ceiling",
        block(cap={"ceiling": FAKE_CEILING, "limit": 3}, candidates=[ELIGIBLE]), [],
        "FAILED",
        f"{FAKE_CEILING!r} is not a charter ceiling, so it cannot buy the cheapest success verdict",
    )
    failures += case(
        worktree, tmp, "cap_with_real_ceiling_is_capped",
        block(cap={"ceiling": "outbound_per_day", "limit": 12}, candidates=[ELIGIBLE]), [],
        "CAPPED",
        "a cap naming a real charter ceiling is still a clean success",
    )
    failures += case(
        worktree, tmp, "worker_blocked_by_is_ignored",
        block(blocked_by="health", candidates=[ELIGIBLE]), [],
        "FAILED",
        "BLOCKED must come from the runner's --blocked-by, never from a field the worker writes",
    )
    failures += case(
        worktree, tmp, "runner_blocked_by_is_honoured",
        block(candidates=[ELIGIBLE]), ["--blocked-by", "health"],
        "BLOCKED",
        "the runner observed the block, so it is real",
    )
    failures += case(
        worktree, tmp, "sends_without_proof_is_failed",
        block(sends=3, candidates=[ELIGIBLE]), [],
        "FAILED",
        "a send claim with no artifact behind it is exactly the lie the whole unit exists to stop",
    )
    failures += case(
        worktree, tmp, "sends_with_proof_is_drove",
        block(sends=1, candidates=[ELIGIBLE]), ["--sends-proof", str(proof)],
        "DROVE",
        "a corroborated send is the one thing that counts as driving",
    )
    failures += case(
        worktree, tmp, "sends_contradicted_by_proof_is_failed",
        block(sends=4, candidates=[ELIGIBLE]), ["--sends-proof", str(empty_proof)],
        "FAILED",
        "the artifact says nothing was sent; the receipt says four. The artifact wins",
    )
    failures += case(
        worktree, tmp, "qa_revise_demotes_exhausted",
        block(candidates=[CITED]), ["--qa-verdict", "REVISE"],
        "FAILED",
        "a reviewer who rejects the receipt must cost the run its success verdict, or the "
        "review is decoration",
    )
    failures += case(
        worktree, tmp, "qa_block_demotes_capped",
        block(cap={"ceiling": "outbound_per_day", "limit": 12}, candidates=[CITED]),
        ["--qa-verdict", "BLOCK"], "FAILED",
        "QA BLOCK is the strongest rejection there is",
    )
    failures += case(
        worktree, tmp, "qa_pass_leaves_exhausted",
        block(candidates=[CITED]), ["--qa-verdict", "PASS"],
        "EXHAUSTED",
        "a passing review must not change an honest verdict",
    )
    failures += case(
        worktree, tmp, "evidence_path_must_resolve",
        block(candidates=[CITED, CITED_BAD_PATH]), [],
        "FAILED",
        "a disqualifier citing a repo path that does not exist is a fabricated citation",
    )
    failures += case(
        worktree, tmp, "quota_met_from_receipt_is_ignored",
        block(sends=1, candidates=[ELIGIBLE], quota_met=True),
        ["--sends-proof", str(proof), "--quota-target", "5"], "DROVE",
        "the verdict itself is unaffected, but see the re-entry assertion below",
    )

    # quota.met in the receipt must not be able to stop re-entry.
    receipt = tmp / "quota-reentry.md"
    receipt.write_text(receipt_text(block(sends=1, candidates=[ELIGIBLE], quota_met=True)), encoding="utf-8")
    got, detail = drive(worktree, receipt, ["--sends-proof", str(proof), "--quota-target", "5"])
    if got is None:
        failures.append(f"FAIL [quota_reentry/output_contract]: {detail}")
    elif got.get("reentry_allowed") is not True:
        failures.append(
            "FAIL [worker_quota_met_stops_reentry]: the receipt claimed quota.met true and "
            "re-entry stopped, even though only 1 of a charter target of 5 is proven. A worker "
            "must not be able to end its own day by typing a boolean."
        )
    return failures


def check_charter_alignment(worktree: Path) -> list[str]:
    """The ceiling names must be the charter's, or this drifts the first time it changes."""
    if not CHARTER.is_file():
        return []
    charter = CHARTER.read_text(encoding="utf-8")
    module = (worktree / "scripts/obe_loop_verdict.py")
    if not module.is_file():
        return []
    text = module.read_text(encoding="utf-8")
    named = [c for c in REAL_CEILINGS if c in text]
    if not named:
        return [
            "FAIL [ceilings_not_named]: obe_loop_verdict.py names none of the charter's ceilings "
            f"{REAL_CEILINGS}, so cap.ceiling cannot be validated against anything."
        ]
    missing_from_charter = [c for c in named if c not in charter]
    if missing_from_charter:
        return [
            f"FAIL [ceiling_not_in_charter]: {missing_from_charter} appear in the module's allowed "
            "ceilings but not in departments/podcast/charter.yaml. The closed enum must be the "
            "charter's, not one the code invented."
        ]
    return []


def check_runner_control_flow(worktree: Path, tmp: Path) -> list[str]:
    """Execute the real runner. Reading it is what missed three defects."""
    harness = worktree / "scripts/loop_shadow_run.py"
    if not harness.is_file():
        return ["FAIL [harness_missing]: scripts/loop_shadow_run.py is required to verify this unit"]
    failures: list[str] = []

    def run(scenario: str) -> dict | None:
        result = subprocess.run(
            [sys.executable, "scripts/loop_shadow_run.py", "--repo", str(worktree),
             "--scenario", scenario, "--out", str(tmp / f"shadow-{scenario}")],
            capture_output=True, text=True, timeout=600, cwd=str(worktree),
        )
        for line in reversed(result.stdout.strip().splitlines()):
            if line.strip().startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        failures.append(
            f"FAIL [shadow_{scenario}]: the harness produced no report (exit {result.returncode})\n"
            f"stderr:\n{result.stderr[-700:]}"
        )
        return None

    revise = run("guest-exhausted-qa-revise")
    if revise is not None:
        if str(revise.get("verdict") or "").upper() != "FAILED":
            failures.append(
                "FAIL [qa_revise_not_applied_by_runner]: driving the real runner with a reviewer "
                f"verdict of REVISE left the receipt at {revise.get('verdict')!r}. The demotion "
                "exists in the module but the runner never passes --qa-verdict, so in production "
                "a rejected receipt still records a success."
            )

    blocked = run("referral-blocked-cross-loop")
    if blocked is not None:
        if str(blocked.get("verdict") or "").upper() != "BLOCKED":
            failures.append(
                "FAIL [cross_loop_verdict_regressed]: the cross-loop path must still compute "
                f"BLOCKED, got {blocked.get('verdict')!r}. Since the receipt's own blocked_by is "
                "now ignored, the runner has to pass --blocked-by instead."
            )
        if not blocked.get("repair_ledger"):
            failures.append(
                "FAIL [repair_task_regressed]: the cross-loop repair task stopped being opened"
            )
    return failures


def git(worktree: Path, *args: str):
    return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True, timeout=120)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    owned = ["scripts/obe_loop_verdict.py", "scripts/run_podcast_loop.sh"]
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="u0-corroboration-") as tmp_name:
        tmp = Path(tmp_name)
        failures += check_module(worktree, tmp)
        failures += check_charter_alignment(worktree)
        failures += check_runner_control_flow(worktree, tmp)

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
        for item in failures:
            print(item)
        print(f"\n{len(failures)} failure(s). Exit 1.")
        return 1

    print("PASS [u0_no_success_without_corroboration]")
    print("  a cap must name a real charter ceiling; an invented one buys nothing")
    print("  BLOCKED comes only from the runner's --blocked-by, never from the receipt")
    print("  DROVE needs an artifact behind the send claim, and the artifact outranks the claim")
    print("  a REVISE or BLOCK review demotes any success verdict to FAILED")
    print("  a disqualifier citing a repo path that does not exist is a fabricated citation")
    print("  the receipt's own quota.met cannot end the day")
    print("  and the real runner, executed, actually applies all of it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
