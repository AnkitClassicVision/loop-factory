#!/usr/bin/env python3
"""Build manifest-r11-listener.json — the inbound half of the card loop:
outbox_push records which card belongs to which row (ledger); a new
outbox_listen poller reads card comments, accepts ONLY human-grammar replies
(first line APPROVE/SKIP, no agent markers), appends decisions append-only,
and closes the card. Owner directive 2026-07-28: every Ankit-facing card must
have a listener that pulls his reply back."""
import json
from pathlib import Path

REPO = "/mnt/d_drive/repos/loop-factory"
WORKDIR = "/home/ankit114/ringer-work/social-dept-build-r11"
CHECK = "/home/ankit114/repos/ringer/templates/fix-swarm/checks/fix-swarm.py"

OWNED = [
    "factory/outbox_push.py",
    "factory/outbox_listen.py",
    "tests/test_outbox_push.py",
    "tests/test_outbox_listen.py",
]
VERIFY = "PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_outbox_push.py tests/test_outbox_listen.py -q -p no:cacheprovider && PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/ -q -p no:cacheprovider"

SPEC = f"""You are a build worker extending the loop-factory's card loop with its inbound half. Your current working directory IS a git worktree of /mnt/d_drive/repos/loop-factory — edit files directly, no git commit/branch/push, no skills/MCP, no network, tests use fakes only. Own ONLY: {', '.join(OWNED)}. FACTORY LAW: factory/ code is department-agnostic — no department names, no tool paths hardcoded; everything reachable via config argv templates. stdlib+PyYAML, logging not print. Read factory/outbox_push.py first — extend it, do not rewrite its working behavior; all 8 existing tests must stay green (extend the test file, never delete assertions).

CONTEXT: outbox_push currently tails outbox jsonl files and delivers rows via injectable ping/card senders. The card sender (Linear) prints JSON to stdout containing 'identifier' (e.g. ANK-123) and 'url'. Human replies land as Linear comments; the HUMAN approval grammar is: comment FIRST LINE starts with APPROVE or SKIP. Agent comments ALWAYS carry a first-line marker (one of: 'AGENT CLAIMED:', 'AGENT UPDATE:', 'AGENT FOLLOW-UP:', 'AGENT DONE:', 'AGENT REVIEW', 'AGENT BLOCKED:', 'AGENT HUMAN HOLD:', 'AGENT NEEDS INPUT:', 'AGENT FAILED:', 'QA REVIEW:') and must NEVER be treated as approvals.

TASKS:
1. outbox_push.py — LEDGER: new optional config key `ledger_file: <path>`. When set, after a successful card send, parse the card sender's captured stdout as JSON (search the last JSON object in stdout defensively); append one jsonl row: {{"ts","row_hash","department","kind","summary","card_identifier","card_url","status":"open"}}. Unparseable stdout or missing identifier → append the row with card_identifier null and status "untracked" and log a warning (the ping already went; never fail the push over ledger bookkeeping). Ledger is APPEND-ONLY.
2. factory/outbox_listen.py — NEW CLI: `python3 factory/outbox_listen.py --config <yaml> [--once] [--dry-run]`. Config keys (same yaml as push; document in module docstring):
   listener:
     reader: [argv template, placeholder {{issue}}]      # prints JSON: a list (or dict containing a list) of comments, each with a 'body' (and possibly timestamps)
     closer: [argv template, placeholders {{issue}} {{state}}]   # sets card state
     close_enabled: true|false
     ack: [argv template, placeholders {{issue}} {{body}}]       # posts an agent comment; optional
     decisions_file: <path>                              # append-only decisions jsonl
   Behavior per tick: read the ledger; current status of a card = its LATEST ledger row for that row_hash (append-only status model — a later row {{"row_hash","card_identifier","status":"decided:approve"}} supersedes "open"). For every card still "open" with a non-null identifier:
   a. Run reader; parse defensively; on failure log + leave open (retry next tick, fail-closed).
   b. Scan comments NEWEST-first for the first comment whose first line (stripped) matches ^APPROVE\\b or ^SKIP\\b AND does not start with any agent marker. Conflicting human replies → newest wins.
   c. On decision: append to decisions_file {{"ts","card_identifier","row_hash","department","kind","decision":"approve"|"skip","source":"linear-comment","first_line":<the matched first line, <=120 chars>}}; append ledger status row "decided:<decision>"; if ack configured, post 'AGENT DONE: decision recorded ({{decision}}). This card's loop is closed.' via ack argv; if close_enabled, run closer with state 'Agent Done'. Ack/closer failures log warnings but the decision stays recorded (the append IS the truth).
   d. No qualifying comment → leave open.
   --dry-run prints would-be decisions to stderr, changes nothing. Exit codes: 0 ok, 2 invalid config (fail closed, clear message), 3 every reader call failed this tick.
3. Tests — test_outbox_push.py additions: ledger row written from fake card sender emitting JSON w/ identifier; junk stdout → untracked row, push still succeeds. test_outbox_listen.py (fake reader/closer/ack = tmp scripts recording argv): APPROVE detected → decision row + ledger decided + closer called with Agent Done; SKIP detected; agent-marker APPROVE-lookalike ('AGENT UPDATE: APPROVE plan...') ignored; newest-wins on conflict; decided cards not re-processed on second tick; reader failure leaves card open and decides nothing; dry-run changes no files; invalid config exits 2; decisions file is append-only across ticks.

HOW TO RUN (this is also the check): {VERIFY}

OUTPUT CONTRACT: files + tests green, plus ./fix-summary.md ('# Fix Summary', '## Summary', '## Files Changed', '## Verification', '## Assumptions', <700 words)."""

TASKS = [{
    "key": "outbox-listener",
    "engine": "codex",
    "task_type": "code-feature",
    "timeout_s": 3600,
    "engine_args": ["-c", "model_reasoning_effort=high"],
    "spec": SPEC,
    "check": (
        f"python3 '{CHECK}' --verify-command '{VERIFY}' "
        f"--patch '{WORKDIR}/outbox-listener.patch' --summary fix-summary.md "
        f"--exported-summary '{WORKDIR}/outbox-listener.summary.md' "
        f"--owned-files '{','.join(OWNED)}'"
    ),
    "expect_files": [],
    "verified": "cards are ledgered at send time and a fail-closed listener turns human APPROVE/SKIP replies into append-only decisions, ignoring agent-marked lookalikes, proven by executed tests",
}]

manifest = {
    "run_name": "social-dept-build",
    "workdir": WORKDIR,
    "max_parallel": 1,
    "worktrees": True,
    "repo": REPO,
    "tasks": TASKS,
}

out = Path(__file__).parent / "manifest-r11-listener.json"
out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} (1 task)")
