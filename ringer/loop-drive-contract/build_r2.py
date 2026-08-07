#!/usr/bin/env python3
"""Generate manifest-r2-u5-marker.json for the loop-drive-contract job.

Round 2 is the U5 fix: the `.delivered` escalation marker must key on the
content of the escalation body, not on its own existence.

Generated rather than hand-written so that if the run goes to two lanes, both
get byte-identical instructions and only the engine differs — otherwise a
difference in outcome tells you nothing about the models.

Edit LANES, then:  python3 build_r2.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
PODCAST = "/mnt/d_drive/repos/podcast"
SCRIPT = "scripts/run_podcast_loop.sh"
CHECK = f"{HERE}/checks/u5_marker_check.py"
WORKDIR = "/mnt/d_drive/ringer-work/loop-drive-contract-r2"

# (task key suffix, engine, model or None, engine_args)
LANES: list[tuple[str, str, str | None, list[str]]] = [
    ("codex", "codex", None, []),
]


def spec() -> str:
    return f"""You are a fix worker. Your current working directory IS a dedicated git
worktree of the repository {PODCAST}, detached at HEAD — edit the files here directly.

BOUNDARY, read this before anything else.

You own exactly ONE tracked file: {SCRIPT}. You may also create ./fix-summary.md in
the worktree root. Change nothing else. Do not touch .git. Do not run git commit, git
branch, git checkout, git stash or git push — leave your change uncommitted; a
validator exports it as a patch. Do not reformat, re-indent, or "tidy" lines you did
not have to change.

This repository drives seven live podcast loops that page a human over Telegram. So:
never execute {SCRIPT} itself, never ssh anywhere, never send a Telegram message,
never run anything under server/ or any script whose name suggests it sends, drafts,
publishes, or writes to a CRM. Do not create or edit systemd units. Do not load skills
and do not call any MCP tool or App; nothing about this task should be captured to any
memory or ledger backend. Everything you need is below or reachable with a file read
inside this worktree.

THE DEFECT

In {SCRIPT}, `deliver_escalation_once()` decides whether today's escalation reaches
the human. It opens:

    deliver_escalation_once() {{
      local marker="${{ESCALATE_FILE}}.delivered"
      [ -f "$ESCALATE_FILE" ] || return 0
      [ ! -f "$marker" ] || return 0

The marker is created empty, twice in that function, with `: > "$marker"` — once after
the Linear-card path succeeds and once after the plain-Telegram fallback succeeds. So
the marker records only THAT something was delivered, never WHAT.

`ESCALATE_FILE` is one file per loop per day: `<loop>-<YYYYMMDD>.ESCALATE`. Once any
escalation goes out, every later escalation that same day is dropped in silence, even
when its text is completely different. A same-day rerun cannot alert. That is the bug.

There is a second half. `append_abnormal_status_once()` adds the runner's own status
line ("runner status: reviewer unverified", "runner status: budget exceeded") to the
escalation body, but it refuses to when the marker exists:

    if [ -f "$ESCALATE_FILE" ] && [ ! -f "${{ESCALATE_FILE}}.delivered" ] &&

That guard only makes sense while delivery is keyed on existence: it stops the file
from growing text nobody will ever send. Once delivery is keyed on content, the guard
is what hides the runner's own failure reason from an alert that already went out.

WHAT THE FIXED CODE MUST DO

1. The marker records a digest of the escalation body — sha256 of the file's bytes is
   the expected choice. It must be at most 200 bytes and must not contain the body.
2. Delivery is suppressed only when the CURRENT body's digest matches the recorded
   one. Same body, same day: still exactly one delivery, as today.
3. A body that changed since the last delivery is delivered again, and the marker is
   updated to the new digest.
4. Both marker-writing sites — the card path and the plain-Telegram fallback — record
   the digest of the body that was actually delivered.
5. Unchanged invariant, do not regress it: when delivery FAILS, no marker is written
   and the function returns non-zero. A lost alert must never look delivered.
6. `append_abnormal_status_once()` appends whenever the line is not already present in
   the body, regardless of the marker.
7. Keep the function headers `telegram() {{`, `deliver_escalation_once() {{` and
   `append_abnormal_status_once() {{` exactly as they are, each closed by a `}}` in
   column 1. The check extracts those three functions by that exact shape and will
   fail if you restyle them.
8. The script runs under `set -u`. Declare any new variable with `local` at the top of
   the function before it is read.

HOW TO RUN THE CHECK — this is the same command the validator runs:

    PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --worktree "$PWD"

It needs no network, no Telegram, no ssh and no worker: it extracts those three shell
functions into a temporary sandbox and drives them against a fake receipt directory,
with the escalation "delivered" into a sink file. It runs two phases. First it proves
the bug still reproduces against the pristine `git show HEAD:{SCRIPT}` — if that
negative control ever stops failing, the harness is broken and nothing it says can be
trusted. Then it runs the same scenarios against your edited file. Every failure names
the scenario and prints the sink and marker state.

Run it BEFORE you edit, to watch the four expected failures. Run it again after.
Also run `bash -n {SCRIPT}` for a syntax check.

OUTPUT CONTRACT

- Your edit to {SCRIPT}, uncommitted, in this worktree.
- ./fix-summary.md containing the headings `# Fix Summary`, `## Summary`,
  `## Files Changed`, `## Verification` and `## Assumptions`, under 700 words. Paste
  the real final output of the check command under `## Verification` — not a summary
  of it, the actual text.

If you cannot make the check pass inside that one file, do NOT make speculative edits
or weaken the check. Say what blocked you under `## Assumptions` so the failure is
readable. A wrong fix that ships is worse than a red check that explains itself.
"""


tasks = []
for suffix, engine, model, engine_args in LANES:
    key = f"r2-u5-marker-{suffix}"
    task: dict[str, object] = {
        "key": key,
        "engine": engine,
        "task_type": "code-fix",
        "timeout_s": 1800,
        "spec": spec(),
        "check": (
            f"PYTHONDONTWRITEBYTECODE=1 python3 {CHECK} --worktree \"$PWD\" "
            f"--patch {WORKDIR}/{key}.patch "
            f"--summary fix-summary.md --exported-summary {WORKDIR}/{key}.summary.md"
        ),
        # Deliverables live outside the worktree: a passing task's worktree is
        # deleted, so fix-summary.md and the diff only survive as the copies the
        # check exports into WORKDIR.
        "expect_files": [f"{WORKDIR}/{key}.patch", f"{WORKDIR}/{key}.summary.md"],
        "verified": (
            "the escalation marker records a digest of the delivered body: the baseline "
            "still reproduces the dropped same-day alert, the fixed code delivers a "
            "changed body, suppresses an identical one, appends the runner status line "
            "after a delivery, and writes no marker when delivery fails"
        ),
    }
    if model:
        task["model"] = model
    if engine_args:
        task["engine_args"] = engine_args
    tasks.append(task)

manifest = {
    "run_name": "loop-drive-contract",
    "workdir": WORKDIR,
    "repo": PODCAST,
    "worktrees": True,
    "max_parallel": len(tasks),
    "tasks": tasks,
}

out = HERE / "manifest-r2-u5-marker.json"
out.write_text(json.dumps(manifest, indent=2) + "\n")
print(f"wrote {out}")
for task in manifest["tasks"]:
    print(f"  {task['key']:<28} {task['engine']:<12} spec={len(task['spec'])} chars")
