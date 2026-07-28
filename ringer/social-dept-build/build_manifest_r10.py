#!/usr/bin/env python3
"""Build manifest-r10-outbox-push.json — factory-level outbox push watcher:
tails department outbox/approval-queue jsonl files and delivers new rows via
injectable sender commands (Telegram ping + Linear card). Department-agnostic
factory code; real sender argv wired by the coordinator at install time."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r10"
CHECK = "/home/ankit114/repos/ringer/templates/fix-swarm/checks/fix-swarm.py"

OWNED = [
    "factory/outbox_push.py",
    "tests/test_outbox_push.py",
]
VERIFY = "PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_outbox_push.py departments/social/tests/ -q -p no:cacheprovider"

SPEC = f"""You are a build worker adding ONE department-agnostic factory tool to the loop-factory. Your current working directory IS a git worktree of /mnt/d_drive/repos/loop-factory — edit files directly, no git commit/branch/push, no skills/MCP, no network, tests use fakes only. Own ONLY: {', '.join(OWNED)}. FACTORY LAW: factory/ code is department-agnostic — no department names, no channel credentials, no URLs in code. Read factory/human_in_the_loop.py and departments/social/state/decisions_outbox.jsonl row shape (kind/department/issue/context/ts/eli5) as source material; also approval-queue rows appended by departments/social/runtime/proposal_card_to_outbox.py (read it for the exact card fields).

BUILD factory/outbox_push.py — a cursor-based push watcher, stdlib+PyYAML only, logging not print:

CLI: python3 factory/outbox_push.py --config <yaml> [--once] [--dry-run]
Config yaml shape (document in the module docstring):
  cursor_file: <path>            # json: {{per-watched-file: {{offset_lines, last_hashes}}}}
  watches:
    - path: <jsonl file>         # e.g. a department's decisions_outbox.jsonl
      department: <name>         # label only, passed to senders
      kind: escalation|approval
  senders:
    ping:                        # argv template list; placeholders {{text}} {{department}} {{kind}}
      - <argv...>
    card:                        # argv template list; placeholders {{title}} {{body}} {{department}} {{kind}}
      - <argv...>
    card_enabled: true|false     # ping-only mode when false

Behavior (each tick / --once):
1. For each watched file: read rows AFTER the stored line offset; tolerate the file not existing yet (skip, no error).
2. For each new row: build sanitized text — department, kind, and the row's eli5 (fall back to issue/question), truncated to 800 chars; NEVER include row context objects, bodies, or anything beyond eli5/issue/question/ts. Idempotency: sha256 of (path + row json) — a hash already in last_hashes (keep last 200) is skipped.
3. Invoke ping sender argv (subprocess, list argv, NO shell) with placeholders substituted; if card_enabled, also invoke card sender with title '[<department>] <kind>: <first 80 chars>' and body = the sanitized text + 'Reply on this card: first line APPROVE or SKIP.'
4. FAIL-CLOSED CURSOR: advance the offset for a row ONLY when the ping sender exits 0 (card failure logs a warning but does not block the cursor — ping is the critical path). A failing ping leaves the cursor so the row retries next tick; after a retry both senders must still be idempotency-safe (hash check).
5. --dry-run prints (to stderr) what would be sent, sends nothing, advances nothing. Exit codes: 0 ok (including nothing new), 2 config invalid (fail closed, clear message), 3 all sends failed this tick.

TESTS (tests/test_outbox_push.py, factory test conventions — see tests/ for style): fake senders = tmp scripts recording argv to files. Cover: new rows pushed once; second tick pushes nothing (offset); duplicate row content skipped by hash; ping failure leaves cursor and retries next tick without double-sending earlier rows; card failure does NOT block cursor; context/body fields never appear in sent text; missing watch file tolerated; invalid config exits 2; --dry-run sends nothing and advances nothing; 800-char truncation.

HOW TO RUN (this is also the check): {VERIFY}

OUTPUT CONTRACT: files + tests green, plus ./fix-summary.md ('# Fix Summary', '## Summary', '## Files Changed', '## Verification', '## Assumptions', <700 words)."""

TASKS = [{
    "key": "outbox-push",
    "engine": "codex",
    "task_type": "code-feature",
    "timeout_s": 3600,
    "spec": SPEC,
    "check": (
        f"python3 '{CHECK}' --verify-command '{VERIFY}' "
        f"--patch '{WORKDIR}/outbox-push.patch' --summary fix-summary.md "
        f"--exported-summary '{WORKDIR}/outbox-push.summary.md' "
        f"--owned-files '{','.join(OWNED)}'"
    ),
    "expect_files": [],
    "verified": "a cursor-based, fail-closed, idempotent outbox push watcher exists with injectable senders and tests proving no re-sends, no body leakage, and retry-on-ping-failure",
}]

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 1,
    "worktrees": True,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r10-outbox-push.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} (1 task)")
