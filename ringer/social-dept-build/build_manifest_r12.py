#!/usr/bin/env python3
"""Build manifest-r12-fix-verb.json — third human verb on cards: FIX with
notes. Owner (Ankit 2026-07-28): 'add fix and i can add notes to fix'."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r12"
CHECK = "/home/ankit114/repos/ringer/templates/fix-swarm/checks/fix-swarm.py"

OWNED = [
    "factory/outbox_listen.py",
    "factory/outbox_push.py",
    "tests/test_outbox_listen.py",
    "tests/test_outbox_push.py",
]
VERIFY = "PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_outbox_push.py tests/test_outbox_listen.py -q -p no:cacheprovider && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider"

SPEC = f"""You are a build worker extending the loop-factory card loop with a third HUMAN verb: FIX (a change request with notes). Your current working directory IS a git worktree of /mnt/d_drive/repos/loop-factory — edit files directly, no git commit/branch/push, no skills/MCP, no network, tests use fakes only. Own ONLY: {', '.join(OWNED)}. Read both factory files first; extend behavior, keep ALL existing tests green (extend test files, never delete assertions). stdlib+PyYAML, logging not print, department-agnostic factory code.

CURRENT BEHAVIOR: outbox_listen polls ledgered cards; newest comment whose first line matches ^APPROVE\\b or ^SKIP\\b (and carries no agent marker) becomes a decision; ledger gains a 'decided:<x>' status row; card is acked + closed.

NEW BEHAVIOR:
1. Grammar: a comment whose FIRST LINE (stripped) matches ^FIX\\b (e.g. 'FIX: change the hook to a question' or bare 'FIX' with notes below) and carries NO agent marker is a HUMAN change request. Scanning newest-first, the newest decision-bearing comment (APPROVE/SKIP/FIX) wins.
2. On FIX: append to decisions_file {{"ts","card_identifier","row_hash","department","kind","decision":"fix","first_line":<=120 chars,"notes":<the FULL comment body, <=2000 chars>,"source":"linear-comment"}}. Append a ledger status row 'fix_requested'. The card STAYS OPEN and stays polled: ack (if configured) with 'AGENT UPDATE: fix request recorded and routed. Reply APPROVE or SKIP after the revised payload lands.' — do NOT close the card, do NOT use AGENT DONE for a fix ack.
3. Cards in 'fix_requested' status keep being polled exactly like 'open': a LATER APPROVE/SKIP decides+closes them as today; a NEW (different-content) FIX comment records another fix row. Dedupe: a FIX whose sha256(comment body) was already recorded for that card (track content hashes in the ledger fix rows, e.g. field 'notes_hash') is silently skipped — no duplicate decision rows, no duplicate acks, across ticks.
4. outbox_push.py card body: update the YOUR MOVE reply line to 'Reply with first line: APPROVE (confirm/apply), SKIP (dismiss), or FIX: <what to change> (add notes on the lines below).' Keep title logic unchanged.
5. Safety unchanged: agent-marked comments ('AGENT UPDATE: FIX the thing' etc.) are NEVER decisions; APPROVE/SKIP semantics and close behavior unchanged; decisions_file stays append-only.
6. Tests: FIX detected with notes captured (multi-line body lands in notes, <=2000 truncation); bare 'FIX' first line works; card stays open + still polled after FIX; later APPROVE on a fix_requested card decides and closes; duplicate FIX comment across two ticks records exactly one row and one ack; a NEW different FIX records a second row; agent-marked FIX ignored; newest-wins ordering among APPROVE/SKIP/FIX; existing approve/skip tests untouched and green; push body line test updated.

HOW TO RUN (this is also the check): {VERIFY}

OUTPUT CONTRACT: files + tests green, plus ./fix-summary.md ('# Fix Summary', '## Summary', '## Files Changed', '## Verification', '## Assumptions', <700 words)."""

TASKS = [{
    "key": "fix-verb",
    "engine": "codex",
    "task_type": "code-feature",
    "timeout_s": 3600,
    "spec": SPEC,
    "check": (
        f"python3 '{CHECK}' --verify-command '{VERIFY}' "
        f"--patch '{WORKDIR}/fix-verb.patch' --summary fix-summary.md "
        f"--exported-summary '{WORKDIR}/fix-verb.summary.md' "
        f"--owned-files '{','.join(OWNED)}'"
    ),
    "expect_files": [],
    "verified": "FIX with notes is a recorded, deduped human change-request that keeps the card open for a later APPROVE/SKIP, proven by executed tests",
}]

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 1,
    "worktrees": True,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r12-fix-verb.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} (1 task)")
