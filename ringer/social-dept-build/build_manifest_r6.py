#!/usr/bin/env python3
"""Build manifest-r6-residuals.json — one-task closing fix for the 4 residual
items from the r5 verification review."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r6"
CHECK = "/home/ankit114/repos/ringer/templates/fix-swarm/checks/fix-swarm.py"

OWNED = [
    "departments/social/runtime/proposal_card_to_outbox.py",
    "departments/social/tests/test_learn_lane.py",
    "departments/social/tests/test_sense_lane.py",
    "departments/social/tests/test_republish_scripts.py",
]
VERIFY = "PYTHONDONTWRITEBYTECODE=1 python3 -m pytest departments/social/tests/ -q -p no:cacheprovider"

SPEC = f"""You are a fix worker closing 4 residual findings from a verification review of the social department (loop-factory). Your current working directory IS a git worktree at commit 9da43e2 — edit files directly, do NOT git commit/branch/push, no skills/MCP, no network, tests use fakes only, stdlib+PyYAML only. Own ONLY: {', '.join(OWNED)}. Do not weaken any existing test.

FINDINGS TO FIX:
1. [P1] proposal_card_to_outbox.py:21,114 — _safe_out_path blocks traversal and charter/runbook/graph names, but NOT the approval queue itself: `--out state/approval_queue.jsonl` passes containment and _write_json would TRUNCATE the append-only queue (and its .approval_queue.lock). Fix robustly: resolve the --out target and refuse (exit 2, blocked receipt to stderr-safe path) when it equals the resolved approval_queue.jsonl or its lock file, in addition to the name-parts list. Test in test_learn_lane.py: --out pointed at the queue (and at the lock) is refused and the queue content is untouched.
2. [P2] proposal_card_to_outbox.py:184 — main()'s except tuple omits RuntimeError, so a missing/corrupt charter (CharterError from factory/charter_loader.py, a RuntimeError subclass) escapes as a traceback exit 1 instead of the blocked-receipt exit 2 contract (sibling propose_insights.py already catches RuntimeError). Add CharterError/RuntimeError to the except tuple; test with a corrupt charter file asserting exit 2 + status blocked + nothing appended.
3. [P2] test_sense_lane.py:423 — test_compare_charter_each_consumed_threshold_flips_signal parametrizes only 6 of the 7+ consumed thresholds. Add weekly_touch_ceiling and faux_work_touch_floor as parametrized cases so finding-12 coverage is self-contained (do not remove the pre-existing test at :330).
4. [P3] test_republish_scripts.py — the title/url placeholder branch in assemble_context.py:72-74 is code-correct but untested. Add a test: a TODO_-valued item.title (and one for item.url) yields complete:false, returncode 2, and the matching entry in missing[].

HOW TO RUN (this is also the check): {VERIFY}

OUTPUT CONTRACT: fixes + tests green, plus ./fix-summary.md ('# Fix Summary', '## Summary', '## Files Changed', '## Verification', '## Assumptions', <700 words)."""

TASKS = [{
    "key": "fix-residuals",
    "engine": "codex",
    "task_type": "code-fix",
    "timeout_s": 3600,
    "spec": SPEC,
    "check": (
        f"python3 '{CHECK}' --verify-command '{VERIFY}' "
        f"--patch '{WORKDIR}/fix-residuals.patch' --summary fix-summary.md "
        f"--exported-summary '{WORKDIR}/fix-residuals.summary.md' "
        f"--owned-files '{','.join(OWNED)}'"
    ),
    "expect_files": [],
    "verified": "the approval queue cannot be truncated via --out, corrupt charter fails closed with a blocked receipt, and the two remaining coverage gaps have dedicated tests",
}]

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 1,
    "worktrees": True,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r6-residuals.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} (1 task)")
