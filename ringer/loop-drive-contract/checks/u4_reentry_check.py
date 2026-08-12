#!/usr/bin/env python3
"""U4 executed check: re-entry replaces quitting, metered by a worker-minutes ledger.

Every assertion here EXECUTES the real runner through scripts/loop_shadow_run.py
(the harness that caught cfc502d). Nothing greps the runner. The four scenarios
were run against HEAD (podcast 29a4a19) on 2026-08-10 and all four showed the
defect: one invocation, no REENTRY stamp, no ledger row, over-budget ledger
ignored. This check encodes the FIXED behavior, plus a defect-repro phase so a
stale check cannot pass vacuously.

Frozen U4 contract this check enforces (the spec restates it verbatim):

  Ledger    ${LOOP_RECEIPT_DIR}/worker-minutes-<%G-W%V>.jsonl — the RUNNER
            appends one JSON row per Ringer invocation:
            {ts, loop, date_tag, attempt, seconds}, even when ringer exits
            nonzero.
  Preflight before the first invocation AND before every re-entry: if the
            current ISO week's summed seconds / 60 >= 1680, do not invoke the
            worker; the verdict becomes CAPPED via the runner-observed
            --cap worker_minutes flag (obe_loop_verdict.py), receipt stamped,
            exit 0.
  Stamp     obe_loop_verdict.py --apply --reentry N maintains line 2 of the
            receipt as `REENTRY: N`; line 1 stays the VERDICT line. First
            passes stamp REENTRY: 0.
  Re-entry  after the QA-verdict apply, the runner parses the verdict JSON;
            while reentry_allowed it re-invokes ringer under the SAME run
            identity (same receipt/QA paths, regenerated manifest carrying the
            previous verdict + reason as re-entry context), and folds the
            superseded attempt into the same receipt under a
            "Prior attempt" section with its loop-drive-v1 fence defused, so
            exactly one live block remains.
  Bounds    re-entry stops on DROVE(quota met), EXHAUSTED, CAPPED, BLOCKED, a
            preflight cap, or a nonzero ringer exit (which stays the loud
            existing failure path).

Usage:
    u4_reentry_check.py --repo <candidate-tree> [--base <head-tree>] --out <dir>

Exit 0 only when every scenario matches the fixed contract. Every failure
prints WHY with the scenario name and the observed report.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

FAILURES: list[str] = []


def fail(scenario: str, why: str, report: dict | None = None) -> None:
    detail = ""
    if report is not None:
        detail = (
            f" [exit={report.get('exit_code')} verdict={report.get('verdict')}"
            f" inv={report.get('ringer_invocations')} reentry={report.get('reentry')}"
            f" blocks={report.get('live_block_count')}]"
        )
    FAILURES.append(f"CHECK FAIL ({scenario}): {why}{detail}")


def run_scenario(repo: Path, scenario: str, out: Path) -> dict | None:
    out_dir = out / scenario
    if out_dir.exists():
        subprocess.run(["rm", "-rf", str(out_dir)], check=True)
    done = subprocess.run(
        [sys.executable, str(repo / "scripts/loop_shadow_run.py"),
         "--repo", str(repo), "--scenario", scenario, "--out", str(out_dir)],
        capture_output=True, text=True, timeout=900,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        fail(scenario, f"harness produced no JSON report; stderr tail: {done.stderr[-300:]}")
        return None


def receipt_text(report: dict) -> str:
    path = report.get("receipt")
    if not path or not Path(path).is_file():
        return ""
    return Path(path).read_text(encoding="utf-8")


def ledger_rows(report: dict) -> list[dict]:
    return [r for r in report.get("worker_minutes_ledger", {}).get("rows", [])
            if isinstance(r, dict) and "unparseable" not in r]


def check_common(scenario: str, report: dict) -> None:
    if not report.get("tree_unchanged"):
        fail(scenario, "the driven tree changed — the runner wrote outside its sandbox", report)
    for call in report.get("external_calls", []):
        if call.get("kind") not in {"tracker", "automation", "automation-absent", "escalation-card"}:
            fail(scenario, f"unexpected external call intercepted: {call}", report)


def check_candidate(repo: Path, out: Path) -> None:
    # S1: FAILED pass 1 re-enters, pass 2 lands EXHAUSTED in the same receipt.
    report = run_scenario(repo, "guest-reenter-then-exhausted", out)
    if report:
        check_common("guest-reenter-then-exhausted", report)
        if report.get("exit_code") != 0:
            fail("guest-reenter-then-exhausted", "run must end exit 0 (EXHAUSTED is success)", report)
        if report.get("verdict") != "EXHAUSTED":
            fail("guest-reenter-then-exhausted", "final verdict must be EXHAUSTED from attempt 2's block", report)
        if report.get("ringer_invocations") != 2:
            fail("guest-reenter-then-exhausted",
                 "ringer must be invoked exactly twice — one re-entry, really executed, not simulated", report)
        if report.get("reentry") != 1:
            fail("guest-reenter-then-exhausted", "receipt line 2 must read 'REENTRY: 1'", report)
        if report.get("live_block_count") != 1:
            fail("guest-reenter-then-exhausted",
                 "exactly one live loop-drive-v1 block may remain; the superseded attempt is defused", report)
        text = receipt_text(report)
        if "prior attempt" not in text.lower():
            fail("guest-reenter-then-exhausted",
                 "the superseded attempt must be folded into the SAME receipt under a 'Prior attempt' section")
        rows = ledger_rows(report)
        if len(rows) != 2 or any(not isinstance(r.get("seconds"), (int, float)) or r["seconds"] < 0 for r in rows):
            fail("guest-reenter-then-exhausted",
                 f"worker-minutes ledger must carry one row per invocation (want 2, got {len(rows)})", report)

    # S2: a named ceiling in the block is CAPPED — clean success, no re-entry.
    report = run_scenario(repo, "guest-capped-daily-sends", out)
    if report:
        check_common("guest-capped-daily-sends", report)
        if report.get("exit_code") != 0:
            fail("guest-capped-daily-sends", "CAPPED is a clean success: exit 0", report)
        if report.get("verdict") != "CAPPED":
            fail("guest-capped-daily-sends", "verdict must be CAPPED", report)
        if report.get("ringer_invocations") != 1:
            fail("guest-capped-daily-sends", "CAPPED must not re-enter", report)
        if report.get("reentry") != 0:
            fail("guest-capped-daily-sends", "first passes stamp 'REENTRY: 0' too", report)
        if len(ledger_rows(report)) != 1:
            fail("guest-capped-daily-sends", "the single invocation must be metered", report)

    # S3: ledger already over 1680 min this week — the worker must never run.
    report = run_scenario(repo, "guest-worker-minutes-preseeded", out)
    if report:
        check_common("guest-worker-minutes-preseeded", report)
        if report.get("exit_code") != 0:
            fail("guest-worker-minutes-preseeded", "a preflight cap is a clean stop: exit 0", report)
        if report.get("verdict") != "CAPPED":
            fail("guest-worker-minutes-preseeded", "verdict must be CAPPED (runner-observed worker_minutes)", report)
        if report.get("ringer_invocations") != 0:
            fail("guest-worker-minutes-preseeded",
                 "the worker must NOT be invoked when the weekly ledger is over budget", report)
        if report.get("reentry") != 0:
            fail("guest-worker-minutes-preseeded", "runner-authored receipt still stamps 'REENTRY: 0'", report)
        text = receipt_text(report)
        if "worker_minutes" not in text:
            fail("guest-worker-minutes-preseeded", "the receipt must NAME the worker_minutes ceiling")
        if len(text.encode()) < 600:
            fail("guest-worker-minutes-preseeded",
                 "runner-authored CAPPED receipt must satisfy the 600-byte receipt contract")

    # S4: re-entry pass crashes (stub refusal) — loud failure, never silent success.
    report = run_scenario(repo, "guest-failed-attempts-exhausted", out)
    if report:
        check_common("guest-failed-attempts-exhausted", report)
        if report.get("exit_code") == 0:
            fail("guest-failed-attempts-exhausted",
                 "a re-entry pass whose worker run fails must fail the unit, not exit 0", report)
        if report.get("ringer_invocations") != 2:
            fail("guest-failed-attempts-exhausted",
                 "the runner must actually have attempted the re-entry (2 invocations)", report)
        if not any("FAILED" in line for line in report.get("sink", [])):
            fail("guest-failed-attempts-exhausted",
                 "the failure must be delivered to the alert sink, not swallowed", report)

    # S5 (r8 finding 4): an unreadable ledger fails CLOSED — loud, pre-worker.
    report = run_scenario(repo, "guest-worker-minutes-corrupt", out)
    if report:
        check_common("guest-worker-minutes-corrupt", report)
        if report.get("exit_code") == 0:
            fail("guest-worker-minutes-corrupt",
                 "a corrupt weekly ledger must FAIL the run loudly, never read as 'not capped'", report)
        if report.get("ringer_invocations") != 0:
            fail("guest-worker-minutes-corrupt",
                 "no worker may be invoked while the safety ledger is unreadable", report)
        if not any("FAILED" in line for line in report.get("sink", [])):
            fail("guest-worker-minutes-corrupt",
                 "the ledger failure must be delivered to the alert sink", report)

    # S6 (r8 minor): a preflight cap must preserve an existing same-day receipt.
    report = run_scenario(repo, "guest-worker-minutes-preseeded-receipt", out)
    if report:
        check_common("guest-worker-minutes-preseeded-receipt", report)
        if report.get("exit_code") != 0:
            fail("guest-worker-minutes-preseeded-receipt", "preflight cap is a clean stop: exit 0", report)
        if report.get("verdict") != "CAPPED":
            fail("guest-worker-minutes-preseeded-receipt", "verdict must be CAPPED", report)
        if report.get("ringer_invocations") != 0:
            fail("guest-worker-minutes-preseeded-receipt", "the worker must not run", report)
        text = receipt_text(report)
        if "PRESEEDED-RECEIPT-MARKER" not in text:
            fail("guest-worker-minutes-preseeded-receipt",
                 "the pre-existing receipt was clobbered by the preflight cap stub — history destroyed")
        if report.get("reentry") != 1:
            fail("guest-worker-minutes-preseeded-receipt",
                 "the existing REENTRY: 1 value must be preserved by the re-stamp, not reset", report)
        if report.get("live_block_count") != 1:
            fail("guest-worker-minutes-preseeded-receipt",
                 "exactly one live block must remain in the preserved receipt", report)

    # S7 (r8 finding 1): fast-fail thrash is a malfunction, not a work loop.
    report = run_scenario(repo, "guest-thrash-fast-fails", out)
    if report:
        check_common("guest-thrash-fast-fails", report)
        if report.get("exit_code") == 0:
            fail("guest-thrash-fast-fails",
                 "3 consecutive sub-60s FAILED passes must end the run as a defect, not keep spinning", report)
        if report.get("ringer_invocations") != 3:
            fail("guest-thrash-fast-fails",
                 "the thrash detector must stop after the 3rd consecutive fast FAILED pass "
                 "(want exactly 3 invocations)", report)
        if not any("thrash" in line.lower() for line in report.get("sink", [])):
            fail("guest-thrash-fast-fails",
                 "the alert must NAME the thrash defect so the human knows this is malfunction, not workload", report)


def check_defect_repro(base: Path, out: Path) -> None:
    """The check must be able to SEE the defect, or it proves nothing."""
    report = run_scenario(base, "guest-reenter-then-exhausted", out / "defect-repro")
    if report is None:
        return
    quit_after_one = report.get("ringer_invocations") == 1 and report.get("reentry") is None
    if not quit_after_one:
        fail("defect-repro",
             "base tree did not show the quitting defect — the check or the base is stale; "
             "refusing to grade a fix against an unreproduced defect", report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path, help="candidate tree under test")
    parser.add_argument("--base", type=Path, help="tree that still carries the defect (HEAD)")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    if args.base:
        check_defect_repro(args.base.resolve(), args.out)
    check_candidate(args.repo.resolve(), args.out)

    if FAILURES:
        print("\n".join(FAILURES))
        print(f"u4_reentry_check: {len(FAILURES)} failure(s)")
        return 1
    print("u4_reentry_check: PASS — re-entry, metering, preflight cap, and loud-failure all proven by execution")
    return 0


if __name__ == "__main__":
    sys.exit(main())
