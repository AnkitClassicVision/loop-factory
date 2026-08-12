#!/usr/bin/env python3
"""U6 check: a cross-loop gate gets an owner, a deadline, and a defect escalation.

  NEGATIVE CONTROL — drive the REAL gate at HEAD. With health QA at REVISE,
  `referral_touch_automation.validate_inputs` raises GateBlocked("Health QA is not
  PASS"), and nothing anywhere creates a repair task for it. That is the silent
  daily block the spec describes. If the gate stops raising, the harness is not
  reaching the thing under test and nothing it reports can be trusted.

  FIXED — drive `scripts/obe_cross_loop_repair.py` and require: a cross-loop
  reason resolves to the owning loop and a BLOCKED verdict naming it; a repair
  task lands in the ledger with a deadline inside the charter's detection
  latency; the same block on the next business day escalates as a department
  defect; a non-consecutive recurrence does not; a self-caused block creates no
  task at all; the same block twice in one day is idempotent; and no address or
  phone number ever reaches the ledger.

Locked interface (coordinator decision, restated in the worker spec):

    python3 scripts/obe_cross_loop_repair.py --blocked-loop <loop> --reason <text>
        --ledger <path> [--now <iso8601>] [--detection-latency-hours 26]

    stdout, one JSON line:
      {"cross_loop": bool, "owner": <loop|null>, "verdict": "BLOCKED: <owner>"|null,
       "task_id": <str|null>, "deadline": <iso8601|null>,
       "consecutive_days": <int>, "defect": bool}
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SUMMARY_SECTIONS = ("# Fix Summary", "## Summary", "## Files Changed", "## Verification")
CROSS_LOOP_REASON = "Health QA is not PASS"
SELF_REASON = "tracker coverage incomplete"
DAY1 = "2026-08-07T14:00:00+00:00"
DAY2 = "2026-08-08T14:00:00+00:00"
DAY4 = "2026-08-10T14:00:00+00:00"

NEGATIVE_CONTROL_DRIVER = r'''
import json, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from server.pipeline import referral_touch_automation as rta

now = datetime.now(timezone.utc)
et_date = now.astimezone(rta.ZoneInfo("America/New_York")).date().isoformat()
report = {
    "schema": rta.TRACKER_SCHEMA,
    "date": et_date,
    "generated_at": now.isoformat(),
    "query_coverage_complete": True,
    "touch_coverage_complete": True,
    "summary": {"touch_count_status": "complete"},
    "who_next": [],
}
action = {"schema": rta.ACTION_SCHEMA, "decision": "no_action"}
qa = Path(sys.argv[2]); qa.write_text("QA: REVISE\nreviewer found gaps\n", encoding="utf-8")
receipt = Path(sys.argv[3]); receipt.write_text("pipeline below target\n", encoding="utf-8")
try:
    rta.validate_inputs(report, action, health_receipt=receipt, health_qa=qa, now=now)
except rta.GateBlocked as exc:
    print(json.dumps({"blocked": True, "reason": str(exc)}))
    raise SystemExit(0)
except Exception as exc:  # a different failure means the harness never reached the gate
    print(json.dumps({"blocked": False, "reason": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(0)
print(json.dumps({"blocked": False, "reason": "validate_inputs returned without blocking"}))
'''


def run_module(worktree: Path, ledger: Path, *, loop: str, reason: str, now: str,
               hours: int = 26) -> tuple[dict | None, str]:
    result = subprocess.run(
        [
            sys.executable, "scripts/obe_cross_loop_repair.py",
            "--blocked-loop", loop, "--reason", reason,
            "--ledger", str(ledger), "--now", now,
            "--detection-latency-hours", str(hours),
        ],
        capture_output=True, text=True, timeout=120, cwd=str(worktree),
    )
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line), ""
            except json.JSONDecodeError:
                continue
    return None, (
        f"no JSON line on stdout (exit {result.returncode}).\n"
        f"stdout:\n{result.stdout[-1200:]}\nstderr:\n{result.stderr[-1200:]}"
    )


def negative_control(worktree: Path, tmp: Path) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", NEGATIVE_CONTROL_DRIVER, str(worktree),
         str(tmp / "health.QA.md"), str(tmp / "health.md")],
        capture_output=True, text=True, timeout=180, cwd=str(worktree),
    )
    payload = None
    for line in reversed(result.stdout.strip().splitlines()):
        if line.strip().startswith("{"):
            payload = json.loads(line)
            break
    if payload is None:
        return [
            "FAIL [BASELINE/gate_undrivable]: could not drive "
            "referral_touch_automation.validate_inputs at all, so the cross-loop block this "
            f"unit exists to own was never reproduced.\nstdout:\n{result.stdout[-800:]}\n"
            f"stderr:\n{result.stderr[-800:]}"
        ]
    if not payload.get("blocked") or "Health QA" not in str(payload.get("reason", "")):
        return [
            "FAIL [BASELINE/negative_control]: with health QA at REVISE the real gate did not "
            f"raise the expected cross-loop block. Got: {payload!r}. The harness is not "
            "exercising the defect and no result from it can be trusted."
        ]
    return []


def check_module(worktree: Path, tmp: Path) -> list[str]:
    failures: list[str] = []
    tool = worktree / "scripts/obe_cross_loop_repair.py"
    if not tool.is_file():
        return [
            "FAIL [module_missing]: scripts/obe_cross_loop_repair.py does not exist. A gate owned "
            "by another loop must produce an owner and a repair task, not a silent zero."
        ]

    # 1. A cross-loop reason resolves to its owner, with a deadline inside the latency.
    ledger = tmp / "ledger-main.json"
    day1, detail = run_module(worktree, ledger, loop="referral-flywheel", reason=CROSS_LOOP_REASON, now=DAY1)
    if day1 is None:
        return [f"FAIL [output_contract]: {detail}"]
    if not day1.get("cross_loop"):
        failures.append(
            f"FAIL [cross_loop_not_detected]: {CROSS_LOOP_REASON!r} is a block owned by the health "
            "loop, but the module reported cross_loop false."
        )
    if day1.get("owner") != "health":
        failures.append(
            f"FAIL [owner_not_resolved]: expected owner 'health', got {day1.get('owner')!r}. The "
            "spec requires the blocked loop to name the loop that owns the gate."
        )
    verdict = str(day1.get("verdict") or "")
    if not verdict.startswith("BLOCKED"):
        failures.append(
            f"FAIL [verdict_not_blocked]: verdict was {verdict!r}; a cross-loop block returns "
            "'BLOCKED: <owning loop>' and the run is never recorded as a success."
        )
    elif "health" not in verdict:
        failures.append(f"FAIL [verdict_does_not_name_owner]: verdict {verdict!r} does not name health")
    if not day1.get("task_id"):
        failures.append("FAIL [no_repair_task]: no task_id was returned, so no repair task was opened")
    deadline = day1.get("deadline")
    if not deadline:
        failures.append("FAIL [no_deadline]: the repair task carries no deadline")
    else:
        try:
            when = datetime.fromisoformat(str(deadline))
            if when.tzinfo is None:
                raise ValueError("timezone required")
            opened = datetime.fromisoformat(DAY1)
            if not (opened < when <= opened + timedelta(hours=26)):
                failures.append(
                    f"FAIL [deadline_outside_latency]: deadline {deadline} is not inside the "
                    "charter's detection_latency_hours: 26 window after the block"
                )
        except ValueError as exc:
            failures.append(f"FAIL [deadline_not_iso]: {deadline!r} is not a tz-aware ISO 8601 time ({exc})")
    if day1.get("defect"):
        failures.append("FAIL [first_block_is_not_a_defect]: the first occurrence escalated as a defect")

    # 2. Same day, same block: idempotent, not a second task and not a second day.
    repeat, detail = run_module(worktree, ledger, loop="referral-flywheel", reason=CROSS_LOOP_REASON, now=DAY1)
    if repeat is None:
        failures.append(f"FAIL [idempotent_output]: {detail}")
    else:
        if repeat.get("consecutive_days") != day1.get("consecutive_days"):
            failures.append(
                "FAIL [same_day_not_idempotent]: running twice in one day changed consecutive_days "
                f"from {day1.get('consecutive_days')} to {repeat.get('consecutive_days')}. A re-run "
                "inside a day must not manufacture a second day of blockage."
            )
        if repeat.get("defect"):
            failures.append("FAIL [same_day_defect]: a same-day re-run escalated as a defect")

    # 3. Next business day, same block: department defect.
    day2, detail = run_module(worktree, ledger, loop="referral-flywheel", reason=CROSS_LOOP_REASON, now=DAY2)
    if day2 is None:
        failures.append(f"FAIL [day2_output]: {detail}")
    else:
        if int(day2.get("consecutive_days") or 0) < 2:
            failures.append(
                f"FAIL [consecutive_not_counted]: day two reported consecutive_days="
                f"{day2.get('consecutive_days')}, expected at least 2."
            )
        if not day2.get("defect"):
            failures.append(
                "FAIL [no_defect_on_day_two]: the same cross-loop block on two consecutive days "
                "must escalate as a department defect; it did not."
            )

    # 4. A gap breaks the streak.
    gap_ledger = tmp / "ledger-gap.json"
    run_module(worktree, gap_ledger, loop="referral-flywheel", reason=CROSS_LOOP_REASON, now=DAY1)
    gap, detail = run_module(worktree, gap_ledger, loop="referral-flywheel", reason=CROSS_LOOP_REASON, now=DAY4)
    if gap is None:
        failures.append(f"FAIL [gap_output]: {detail}")
    elif gap.get("defect") or int(gap.get("consecutive_days") or 0) != 1:
        failures.append(
            "FAIL [gap_treated_as_consecutive]: a block on 08-07 and again on 08-10 is not two "
            f"consecutive days, but the module reported consecutive_days="
            f"{gap.get('consecutive_days')} defect={gap.get('defect')}."
        )

    # 5. A self-caused block belongs to this loop; it opens no repair task for anyone.
    self_ledger = tmp / "ledger-self.json"
    own, detail = run_module(worktree, self_ledger, loop="referral-flywheel", reason=SELF_REASON, now=DAY1)
    if own is None:
        failures.append(f"FAIL [self_block_output]: {detail}")
    else:
        if own.get("cross_loop"):
            failures.append(
                f"FAIL [self_block_misrouted]: {SELF_REASON!r} is this loop's own problem, but it "
                "was reported as cross-loop, which would hand its work to another loop."
            )
        if own.get("task_id"):
            failures.append("FAIL [self_block_opened_task]: a self-caused block opened a repair task")
        if self_ledger.is_file() and json.loads(self_ledger.read_text() or "{}").get("tasks"):
            failures.append("FAIL [self_block_wrote_ledger]: a self-caused block wrote a repair task to the ledger")

    # 6. Privacy: a reason carrying contact data must never reach the ledger.
    priv_ledger = tmp / "ledger-priv.json"
    run_module(
        worktree, priv_ledger, loop="referral-flywheel",
        reason=f"{CROSS_LOOP_REASON} for guest.person@example.com tel 555-123-4567", now=DAY1,
    )
    if priv_ledger.is_file():
        text = priv_ledger.read_text(encoding="utf-8")
        if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
            failures.append(
                "FAIL [ledger_leaks_email]: an email address reached the repair ledger. Receipts "
                "and ledgers are pasted into Telegram; contact data must be stripped."
            )
        if re.search(r"\d{3}[-.\s]\d{3}[-.\s]\d{4}", text):
            failures.append("FAIL [ledger_leaks_phone]: a phone number reached the repair ledger")

    return failures


def check_runner_wiring(worktree: Path) -> list[str]:
    """Invoking the opener is not enough — it has to be REACHABLE on the blocked path.

    `referral_touch_automation.main()` returns 1 whenever its status is "blocked", and
    the runner answers a non-zero POST_RC with `exit 1`. Any repair call placed after
    that exit is dead code on the exact scenario this unit exists for, which is how an
    earlier attempt passed a weaker version of this check.
    """
    runner = worktree / "scripts/run_podcast_loop.sh"
    if not runner.is_file():
        return ["FAIL [runner_missing]: scripts/run_podcast_loop.sh does not exist"]
    lines = runner.read_text(encoding="utf-8").splitlines()
    if not any("obe_cross_loop_repair.py" in line for line in lines):
        return [
            "FAIL [runner_does_not_open_repair_tasks]: run_podcast_loop.sh never invokes "
            "obe_cross_loop_repair.py, so a cross-loop block stays exactly as silent as it is today."
        ]

    post_rc = next((i for i, line in enumerate(lines) if "POST_RC=$?" in line), None)
    if post_rc is None:
        return [
            "FAIL [post_rc_branch_missing]: the referral post-QA action's POST_RC branch is gone "
            "from run_podcast_loop.sh. That branch is the blocked path; do not remove it."
        ]
    exits = [i for i, line in enumerate(lines) if i > post_rc and line.strip() == "exit 1"]
    if not exits:
        return [
            "FAIL [post_rc_exit_missing]: nothing exits non-zero after POST_RC, so a failed "
            "post-QA action would now read as a successful run."
        ]
    first_exit = exits[0]
    reachable = [
        i for i, line in enumerate(lines)
        if "obe_cross_loop_repair.py" in line and post_rc < i < first_exit
    ]
    if not reachable:
        call_lines = [i + 1 for i, line in enumerate(lines) if "obe_cross_loop_repair.py" in line]
        return [
            "FAIL [repair_unreachable_on_the_blocked_path]: the repair opener is called at line(s) "
            f"{call_lines}, but the blocked path leaves the script at line {first_exit + 1} "
            f"(`exit 1` after POST_RC on line {post_rc + 1}). referral_touch_automation returns 1 "
            "whenever its status is 'blocked', so that exit fires FIRST and the repair opener never "
            "runs on the one scenario this unit exists for. Open the repair task inside the "
            "POST_RC failure branch, before the script exits."
        ]
    return []


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True, timeout=120)


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
    for line in git(worktree, "status", "--porcelain").stdout.splitlines():
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
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    parser.add_argument("--skip-baseline", action="store_true", help="developer mode only")
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    owned = ["scripts/obe_cross_loop_repair.py", "scripts/run_podcast_loop.sh"]
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="u6-crossloop-") as tmp_name:
        tmp = Path(tmp_name)
        if not args.skip_baseline:
            failures += negative_control(worktree, tmp)
        failures += check_module(worktree, tmp)
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

    print("PASS [u6_cross_loop_gate_has_an_owner]")
    print("  baseline reproduced the block: health QA at REVISE still raises the real gate")
    print("  fixed: the block resolves to its owning loop and returns BLOCKED naming it")
    print("  fixed: a repair task opens with a deadline inside detection_latency_hours: 26")
    print("  fixed: two consecutive days escalate as a department defect; a gap does not")
    print("  fixed: a self-caused block opens no task for anyone else")
    print("  fixed: no address or phone number reaches the repair ledger")
    print("  fixed: the runner actually invokes the repair opener")
    return 0


if __name__ == "__main__":
    sys.exit(main())
