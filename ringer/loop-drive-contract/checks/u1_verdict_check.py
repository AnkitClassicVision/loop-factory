#!/usr/bin/env python3
"""U1 check: the loop verdict is COMPUTED from the receipt, never authored by the worker.

Two phases, same as the U5 check.

  NEGATIVE CONTROL — prove today's defect still reproduces against the pristine
  `git show <ref>:scripts/loop_receipt_check.sh`: a receipt that sent nothing and
  says `VERDICT: OK` passes the receipt check. That is "+0 is a passing grade",
  the first mechanism named in the spec. If it stops reproducing, the harness is
  not testing what it claims and every later assertion is void.

  FIXED — drive `scripts/obe_loop_verdict.py` over fixture receipts and require
  the five verdicts to fall out of the loop-drive block, the worker's own verdict
  line to be overwritten, an unproven zero to land on FAILED, and the receipt
  check to accept the new vocabulary while still rejecting a made-up verdict.

The contract under test (locked by the coordinator, restated in the worker spec):

    ```loop-drive-v1
    {"sends": 0,
     "cap": null,                      or {"ceiling": "sends_per_day", "limit": 12}
     "blocked_by": null,               or "health"
     "quota": {"target": 2, "met": false},
     "candidates": [{"alias": "cand-1", "eligible": true},
                    {"alias": "cand-2", "eligible": false,
                     "disqualifier": "inside the 4-day cadence floor",
                     "evidence": "episodes/_loop_receipts/x.json:contacted_at"}]}
    ```

    python3 scripts/obe_loop_verdict.py --receipt <path> [--apply]
      prints one line of JSON: {"verdict", "reason", "sends", "reentry_allowed"}
      with --apply, rewrites the receipt's first line to `VERDICT: <verdict>`
      exit 0 when a verdict was computed, exit 2 when the receipt is unreadable

Precedence is CAPPED > BLOCKED > DROVE > EXHAUSTED > FAILED: every verdict that
must stop re-entry outranks one that may continue, so U4 can key on the verdict
alone without re-deriving the ceilings.

SUPERSEDED IN PART BY U0 (see u0_corroboration_check.py). U0 kept the shape above
but moved corroboration out of the receipt and onto the command line, because the
worker writes the receipt: `blocked_by` now comes only from `--blocked-by`, a send
claim needs `--sends-proof` behind it, `cap.ceiling` must name a real charter
ceiling, repo-path evidence must resolve, and `quota.met` in the receipt is
ignored in favour of `--quota-target` against proven sends. The fixtures below
supply those flags; a case that expects a success verdict without them is
asserting the pre-U0 contract and would be testing nothing.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERDICTS = ("DROVE", "EXHAUSTED", "CAPPED", "FAILED", "BLOCKED")
SUMMARY_SECTIONS = ("# Fix Summary", "## Summary", "## Files Changed", "## Verification")

RECEIPT_PROSE = """
# OBE {loop} Loop Receipt, 2026-08-07

Runbook: `podcast-{loop}-runbook`
Mode: READ/PROPOSE ONLY
External actions taken: none

## Result

- Source truth: current. Evidence: verified against the live roster receipt.
- Confirmed guest recording pipeline: 3/6. Counted from the calendar, checked.
- Funnel delta this run: outreach {sends} | first responses +0 | conversations +0

## Machine block

```loop-drive-v1
{block}
```
"""


def receipt_text(block: dict, verdict_line: str = "VERDICT: OK", loop: str = "guest-acquisition") -> str:
    body = RECEIPT_PROSE.format(
        loop=loop,
        sends=f"+{block.get('sends', 0)}",
        block=json.dumps(block, indent=2),
    )
    # Pad past loop_receipt_check.sh's 600-byte floor without inventing evidence.
    padding = "\n" + "\n".join(
        f"{n}. Candidate row {n} recorded, evidence cited in the machine block above."
        for n in range(1, 12)
    )
    return f"{verdict_line}\n{body}{padding}\n"


def block(
    sends: int = 0,
    candidates: list[dict] | None = None,
    cap: dict | None = None,
    blocked_by: str | None = None,
    quota_met: bool = False,
) -> dict:
    return {
        "sends": sends,
        "cap": cap,
        "blocked_by": blocked_by,
        "quota": {"target": 2, "met": quota_met},
        "candidates": candidates if candidates is not None else [],
    }


def cited(alias: str) -> dict:
    # U0 requires repo-path evidence to resolve to a real file, so the fixture cites
    # one that always exists. A missing path would make these cases fall to FAILED
    # for the wrong reason and quietly stop testing what they claim to test.
    return {
        "alias": alias,
        "eligible": False,
        "disqualifier": "inside the 4-day per-contact cadence floor",
        "evidence": "scripts/run_podcast_loop.sh:contacted_at",
    }


def uncited(alias: str) -> dict:
    return {"alias": alias, "eligible": False, "disqualifier": "not a fit", "evidence": None}


def eligible(alias: str) -> dict:
    return {"alias": alias, "eligible": True, "disqualifier": None, "evidence": None}


class Case:
    def __init__(self, name: str, receipt: str, expect: str, why: str,
                 extra: list[str] | None = None) -> None:
        self.name = name
        self.receipt = receipt
        self.expect = expect
        self.why = why
        # U0 moved corroboration out of the receipt and onto the command line, so a
        # case that expects a success verdict has to supply the runner's evidence.
        self.extra = extra or []


def cases(sends_proof: Path) -> list[Case]:
    return [
        Case(
            "failed_one_eligible_candidate",
            receipt_text(block(sends=0, candidates=[eligible("cand-1"), cited("cand-2")])),
            "FAILED",
            "zero sends with an eligible candidate left on the table is the spec's core failure",
        ),
        Case(
            "exhausted_every_candidate_cited",
            receipt_text(block(sends=0, candidates=[cited("cand-1"), cited("cand-2")])),
            "EXHAUSTED",
            "zero sends is only honest when every candidate carries an evidence-cited disqualifier",
        ),
        Case(
            "failed_disqualifier_without_evidence",
            receipt_text(block(sends=0, candidates=[cited("cand-1"), uncited("cand-2")])),
            "FAILED",
            "a disqualifier with no evidence is an unproven zero, not an exhausted pipeline",
        ),
        Case(
            "failed_empty_candidate_table",
            receipt_text(block(sends=0, candidates=[])),
            "FAILED",
            "'no candidates available' without an enumerated table is an unproven zero",
        ),
        Case(
            "drove_on_sends",
            receipt_text(block(sends=2, candidates=[eligible("cand-1")])),
            "DROVE",
            "sends greater than zero is the only thing that counts as driving",
            extra=["--sends-proof", str(sends_proof)],
        ),
        Case(
            "sends_without_proof_is_not_drove",
            receipt_text(block(sends=2, candidates=[eligible("cand-1")])),
            "FAILED",
            "U0: a send claim with no artifact behind it cannot buy DROVE",
        ),
        Case(
            "capped_outranks_drove",
            receipt_text(
                block(sends=3, candidates=[eligible("cand-1")],
                      cap={"ceiling": "outbound_per_day", "limit": 12})
            ),
            "CAPPED",
            "a named ceiling must outrank DROVE or re-entry would run past the ceiling",
            extra=["--sends-proof", str(sends_proof)],
        ),
        Case(
            "blocked_names_the_owning_loop",
            receipt_text(block(sends=0, candidates=[eligible("cand-1")])),
            "BLOCKED",
            "an upstream gate owned by another loop is not this loop's failure",
            extra=["--blocked-by", "health"],
        ),
        Case(
            "worker_authored_ok_is_overwritten",
            receipt_text(block(sends=0, candidates=[eligible("cand-1")]), verdict_line="VERDICT: OK"),
            "FAILED",
            "the worker may not author its own verdict; this is the negative test in the spec",
        ),
        Case(
            "missing_block_is_an_unproven_zero",
            "VERDICT: OK\n\n# Receipt with no machine block\n\n"
            + "Evidence: counted and verified against the roster.\n" * 20,
            "FAILED",
            "a receipt with no loop-drive block proves nothing, so it cannot earn a success verdict",
        ),
    ]


def run_verdict(worktree: Path, receipt: Path, apply: bool,
                extra: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "scripts/obe_loop_verdict.py", "--receipt", str(receipt)]
    if apply:
        cmd.append("--apply")
    cmd.extend(extra or [])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(worktree))


def parse_verdict(result: subprocess.CompletedProcess[str]) -> tuple[str | None, str]:
    """Pull the verdict out of the tool's stdout JSON line. Returns (verdict, detail)."""
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "verdict" in payload:
            return str(payload["verdict"]).upper(), ""
    return None, (
        f"no JSON line with a 'verdict' key on stdout (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout[-1500:]}\nstderr:\n{result.stderr[-1500:]}"
    )


def check_verdict_module(worktree: Path, tmp: Path) -> list[str]:
    failures: list[str] = []
    tool = worktree / "scripts/obe_loop_verdict.py"
    if not tool.is_file():
        return [
            "FAIL [module_missing]: scripts/obe_loop_verdict.py does not exist. The verdict "
            "must be computed by a deterministic step, not chosen by the worker."
        ]

    sends_proof = tmp / "sends-proof.json"
    sends_proof.write_text(json.dumps({"sends": 3, "sent": True}), encoding="utf-8")
    for index, case in enumerate(cases(sends_proof)):
        receipt = tmp / f"case-{index}-{case.name}.md"
        receipt.write_text(case.receipt, encoding="utf-8")
        result = run_verdict(worktree, receipt, apply=False, extra=case.extra)
        got, detail = parse_verdict(result)
        if got is None:
            failures.append(f"FAIL [{case.name}/output_contract]: {detail}")
            continue
        if got != case.expect:
            failures.append(
                f"FAIL [{case.name}]: expected {case.expect}, got {got}. {case.why}."
            )

    # --apply must overwrite the worker's line in the file itself, and be idempotent.
    receipt = tmp / "apply-target.md"
    receipt.write_text(
        receipt_text(block(sends=0, candidates=[eligible("cand-1")]), verdict_line="VERDICT: OK"),
        encoding="utf-8",
    )
    run_verdict(worktree, receipt, apply=True)
    first_line = receipt.read_text(encoding="utf-8").splitlines()[0].strip()
    if first_line != "VERDICT: FAILED":
        failures.append(
            "FAIL [apply_overwrites_worker_verdict]: after --apply the receipt's first line is "
            f"{first_line!r}, expected 'VERDICT: FAILED'. A worker-authored verdict must be "
            "overwritten, not honored."
        )
    else:
        run_verdict(worktree, receipt, apply=True)
        again = receipt.read_text(encoding="utf-8").splitlines()[0].strip()
        if again != "VERDICT: FAILED":
            failures.append(
                f"FAIL [apply_is_idempotent]: a second --apply produced {again!r}; re-running the "
                "computer on its own output must be stable, because U4 re-entry will do exactly that."
            )

    # --apply must not eat a first line that was never a verdict. The worker prompt no
    # longer asks for one, so the receipt's title line is the likely occupant.
    receipt = tmp / "apply-no-verdict-line.md"
    original_first = "# OBE Guest Acquisition Loop Receipt, 2026-08-07"
    body = receipt_text(block(sends=0, candidates=[cited("cand-1")]), verdict_line=original_first)
    receipt.write_text(body, encoding="utf-8")
    run_verdict(worktree, receipt, apply=True)
    after = receipt.read_text(encoding="utf-8")
    if after.splitlines()[0].strip() != "VERDICT: EXHAUSTED":
        failures.append(
            "FAIL [apply_without_existing_verdict_line]: --apply did not put the computed verdict "
            f"on the first line; got {after.splitlines()[0]!r}."
        )
    elif original_first not in after:
        failures.append(
            "FAIL [apply_destroys_first_line]: --apply overwrote a first line that was not a "
            f"verdict, deleting {original_first!r} from the receipt. Replace an existing VERDICT "
            "line, otherwise INSERT the computed one above the content."
        )

    # re-entry guidance must ride along, so U4 can key on the verdict alone.
    proof = ["--sends-proof", str(sends_proof)]
    for verdict, want_reentry, blk, flags in (
        ("FAILED", True, block(sends=0, candidates=[eligible("c")]), []),
        ("EXHAUSTED", False, block(sends=0, candidates=[cited("c")]), []),
        ("CAPPED", False, block(sends=1, cap={"ceiling": "outbound_per_day", "limit": 12}), proof),
        ("BLOCKED", False, block(sends=0, candidates=[eligible("c")]), ["--blocked-by", "health"]),
        # U0: re-entry keys on PROVEN sends against the charter target, never on the
        # receipt's own quota.met, so the target arrives as a flag.
        ("DROVE", True, block(sends=1, candidates=[eligible("c")]), proof + ["--quota-target", "9"]),
        ("DROVE", False, block(sends=9, candidates=[cited("c")], quota_met=True),
         proof + ["--quota-target", "2"]),
    ):
        receipt = tmp / f"reentry-{verdict}-{want_reentry}.md"
        receipt.write_text(receipt_text(blk), encoding="utf-8")
        result = run_verdict(worktree, receipt, apply=False, extra=flags)
        try:
            payload = json.loads(
                [ln for ln in result.stdout.strip().splitlines() if ln.strip().startswith("{")][-1]
            )
        except (IndexError, json.JSONDecodeError):
            failures.append(f"FAIL [reentry_{verdict}]: no JSON on stdout to read reentry_allowed from")
            continue
        if "reentry_allowed" not in payload:
            failures.append(
                "FAIL [reentry_field_missing]: the JSON output carries no 'reentry_allowed'. U4 "
                "re-enters on FAILED and on DROVE with the quota unmet; it needs that answer here."
            )
            break
        if bool(payload["reentry_allowed"]) is not want_reentry:
            failures.append(
                f"FAIL [reentry_{verdict}_{want_reentry}]: reentry_allowed was "
                f"{payload['reentry_allowed']}, expected {want_reentry} for {verdict} "
                f"(flags={flags})."
            )
    return failures


def check_receipt_check(script: Path, tmp: Path, *, fixed: bool) -> list[str]:
    """Drive loop_receipt_check.sh over each verdict word."""
    failures: list[str] = []
    phase = "FIXED" if fixed else "BASELINE"
    if not script.is_file():
        return [f"FAIL [{phase}/receipt_check_missing]: {script} does not exist"]

    def run(receipt: Path) -> int:
        return subprocess.run(
            ["bash", str(script), str(receipt)],
            capture_output=True,
            text=True,
            timeout=60,
        ).returncode

    if fixed:
        for verdict in VERDICTS:
            receipt = tmp / f"rc-{verdict}.md"
            receipt.write_text(
                receipt_text(block(sends=1, candidates=[cited("c")]), verdict_line=f"VERDICT: {verdict}"),
                encoding="utf-8",
            )
            if run(receipt) != 0:
                failures.append(
                    f"FAIL [receipt_check_rejects_{verdict}]: loop_receipt_check.sh refused a "
                    f"receipt headed 'VERDICT: {verdict}'. All five computed verdicts are legal."
                )
        bogus = tmp / "rc-bogus.md"
        bogus.write_text(
            receipt_text(block(sends=1, candidates=[cited("c")]), verdict_line="VERDICT: SPLENDID"),
            encoding="utf-8",
        )
        if run(bogus) == 0:
            failures.append(
                "FAIL [receipt_check_accepts_invented_verdict]: 'VERDICT: SPLENDID' passed. The "
                "check must accept exactly the five computed verdicts and nothing else."
            )
    else:
        zero_send_ok = tmp / "rc-baseline-ok.md"
        zero_send_ok.write_text(
            receipt_text(block(sends=0, candidates=[eligible("c")]), verdict_line="VERDICT: OK"),
            encoding="utf-8",
        )
        if run(zero_send_ok) != 0:
            failures.append(
                "FAIL [BASELINE/negative_control]: today's loop_receipt_check.sh already rejects a "
                "zero-send 'VERDICT: OK' receipt, so the defect this unit exists to fix does not "
                "reproduce and the harness is not testing what it claims."
            )
    return failures


def check_runner_wiring(worktree: Path) -> list[str]:
    """The computer has to actually run, and the worker has to be told to emit the block."""
    failures: list[str] = []
    runner = worktree / "scripts/run_podcast_loop.sh"
    if not runner.is_file():
        return ["FAIL [runner_missing]: scripts/run_podcast_loop.sh does not exist"]
    text = runner.read_text(encoding="utf-8")
    if "obe_loop_verdict.py" not in text:
        failures.append(
            "FAIL [runner_does_not_compute_verdict]: run_podcast_loop.sh never invokes "
            "obe_loop_verdict.py, so the verdict is still whatever the worker typed."
        )
    elif "--apply" not in text.split("obe_loop_verdict.py", 1)[1][:400]:
        failures.append(
            "FAIL [runner_computes_but_does_not_apply]: obe_loop_verdict.py is invoked without "
            "--apply nearby, so the computed verdict never replaces the worker's line."
        )
    if "loop-drive-v1" not in text:
        failures.append(
            "FAIL [prompt_does_not_request_block]: the worker prompt in run_podcast_loop.sh never "
            "mentions the loop-drive-v1 block, so no receipt will contain the fields the verdict "
            "is computed from."
        )
    if "disqualifier" not in text:
        failures.append(
            "FAIL [qa_prompt_does_not_spot_check_disqualifiers]: nothing in run_podcast_loop.sh's "
            "reviewer prompt mentions disqualifiers. EXHAUSTED is now a success verdict a worker "
            "can buy by calling good candidates ineligible, and the spec's guard against that is "
            "the reviewer spot-checking at least one disqualifier against the file it cites."
        )
    return failures


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, timeout=120
    )


def check_summary(summary: Path, exported: Path) -> list[str]:
    if not summary.is_file():
        return [f"FAIL [summary_missing]: {summary} was not written"]
    text = summary.read_text(encoding="utf-8")
    missing = [s for s in SUMMARY_SECTIONS if s.lower() not in text.lower()]
    failures = [f"FAIL [summary_sections]: fix-summary.md is missing {', '.join(missing)}"] if missing else []
    if len(text.split()) > 900:
        failures.append(f"FAIL [summary_length]: fix-summary.md is {len(text.split())} words, ceiling is 900")
    if not failures:
        exported.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(summary, exported)
    return failures


def export_patch(worktree: Path, owned: list[str], patch: Path) -> list[str]:
    failures: list[str] = []
    add = git(worktree, "add", "--", *owned)
    if add.returncode != 0:
        return [f"FAIL [git_add_failed]: {add.stderr.strip()}"]
    status = git(worktree, "status", "--porcelain")
    for line in status.stdout.splitlines():
        code, path = line[:2], line[3:].strip('"')
        if code == "??":
            continue
        if path not in owned:
            failures.append(f"FAIL [outside_owned_files]: {path} changed; this task owns {owned}")
    diff = git(worktree, "diff", "--cached", "--binary", "--", *owned)
    if not diff.stdout.strip():
        return failures + ["FAIL [empty_patch]: nothing staged; no owned file was edited"]
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text(diff.stdout, encoding="utf-8")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--baseline-ref", default="HEAD")
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    parser.add_argument("--skip-baseline", action="store_true", help="developer mode only")
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    owned = ["scripts/obe_loop_verdict.py", "scripts/loop_receipt_check.sh", "scripts/run_podcast_loop.sh"]
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="u1-verdict-") as tmp_name:
        tmp = Path(tmp_name)

        if not args.skip_baseline:
            show = git(worktree, "show", f"{args.baseline_ref}:scripts/loop_receipt_check.sh")
            if show.returncode != 0:
                print(f"FAIL [baseline_unavailable]: {show.stderr.strip()}")
                return 1
            baseline_script = tmp / "baseline_loop_receipt_check.sh"
            baseline_script.write_text(show.stdout, encoding="utf-8")
            failures += check_receipt_check(baseline_script, tmp, fixed=False)

        failures += check_verdict_module(worktree, tmp)
        failures += check_receipt_check(worktree / "scripts/loop_receipt_check.sh", tmp, fixed=True)
        failures += check_runner_wiring(worktree)

    if args.exported_summary:
        failures += check_summary(args.summary, args.exported_summary)
    if args.patch:
        failures += export_patch(worktree, owned, args.patch)

    if failures:
        for item in failures:
            print(item)
        print(f"\n{len(failures)} failure(s). Exit 1.")
        return 1

    print("PASS [u1_verdict_is_computed]")
    print("  baseline reproduced the bug: a zero-send 'VERDICT: OK' receipt passes today's check")
    print("  fixed: all five verdicts fall out of the loop-drive block, precedence included")
    print("  fixed: an unproven zero (uncited disqualifier, empty table, missing block) is FAILED")
    print("  fixed: --apply overwrites the worker's verdict line and is idempotent")
    print("  fixed: reentry_allowed is present and correct for every verdict U4 will key on")
    print("  fixed: the runner computes and applies the verdict, and asks the worker for the block")
    return 0


if __name__ == "__main__":
    sys.exit(main())
