#!/usr/bin/env python3
"""Independent oracle for the mailroom-rebuild round 1 workers.

The worker writes its own tests. Those tests are NOT the check: a worker can
write a vacuous test that passes against broken code. This script drives the
worker's module through fixtures IT never saw and asserts observable behaviour,
then enforces file ownership and exports the patch out of the worktree before
Ringer deletes it.

Every failure prints WHY, because the failure text is what lands in the retry
prompt.

Usage:
  mailroom_check.py --task reescalate|registry|stall --repo <worktree> --export <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOUR = 3600
FAILURES: list[str] = []

OWNED = {
    "reescalate": ["factory/reescalate.py", "tests/test_reescalate.py"],
    "registry": [
        "factory/mailroom_registry.py",
        "factory/scaffold.py",
        "tests/test_mailroom_registry.py",
        "runbooks/factory-pipeline.md",
    ],
    # r1b: test_outbox_push.py added. Round 1 failed because the spec told the
    # worker to repair a legacy fixture it was not allowed to own, so it
    # correctly refused and the suite gate failed it. Orchestrator defect.
    "stall": [
        "factory/outbox_push.py",
        "tests/test_outbox_push_stall.py",
        "tests/test_outbox_push.py",
    ],
}


def fail(message: str) -> None:
    FAILURES.append(message)


def run(argv: list[str], cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
    )


def iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat()


# --------------------------------------------------------------- reescalate


def _ledger_row(**fields) -> dict:
    row = {
        "row_hash": fields.get("row_hash", "h1"),
        "card_identifier": fields.get("card_identifier", "ANK-1"),
        "department": "probe",
        "kind": "escalation",
        "summary": "probe row",
        "status": "open",
    }
    row.update(fields)
    return row


def _plan(repo: Path, rows: list[dict], now: datetime) -> set[str] | None:
    """Return the set of card identifiers the module says are due right now."""
    if not (repo / "factory" / "reescalate.py").exists():
        return None  # already reported once by check_reescalate; do not spam the retry
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "ledger.jsonl"
        ledger.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        result = run(
            [
                sys.executable,
                "factory/reescalate.py",
                "--ledger",
                str(ledger),
                "--now",
                iso(now),
                "--plan-only",
            ],
            repo,
        )
    if result.returncode != 0:
        fail(
            "reescalate.py --plan-only exited "
            f"{result.returncode}. stderr: {(result.stderr or '')[:400]}"
        )
        return None
    try:
        payload = json.loads(result.stdout[result.stdout.index("{") :])
    except (ValueError, json.JSONDecodeError):
        fail(
            "reescalate.py --plan-only did not print a JSON object. "
            f"stdout was: {result.stdout[:400]!r}"
        )
        return None
    due = payload.get("due")
    if not isinstance(due, list):
        fail(f"--plan-only JSON has no 'due' list. Got keys: {sorted(payload)}")
        return None
    identifiers = set()
    for entry in due:
        if not isinstance(entry, dict) or "card_identifier" not in entry:
            fail(f"each 'due' entry must be an object with card_identifier; got {entry!r}")
            return None
        identifiers.add(entry["card_identifier"])
    return identifiers


def check_reescalate(repo: Path) -> None:
    now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
    if not (repo / "factory" / "reescalate.py").exists():
        fail(
            "factory/reescalate.py does not exist. Build it with the CLI contract in "
            "the spec: --ledger <path> --now <iso8601> --plan-only must print a JSON "
            "object {\"due\": [{\"card_identifier\": ..., \"reescalation_count\": ..., "
            "\"reason\": ...}]} listing exactly the cards owed a ping at that instant."
        )
        return

    def case(label: str, rows: list[dict], expect_due: bool, card: str = "ANK-1") -> None:
        got = _plan(repo, rows, now)
        if got is None:
            return
        is_due = card in got
        if is_due != expect_due:
            fail(
                f"{label}: expected card {card} to be "
                f"{'DUE' if expect_due else 'NOT due'} at {iso(now)}, "
                f"but --plan-only returned due={sorted(got)}"
            )

    case(
        "normal cadence must not fire early (47h < 48h)",
        [_ledger_row(first_raised=iso(now - timedelta(hours=47)), urgency="normal")],
        expect_due=False,
    )
    case(
        "normal cadence must fire at 48h (49h elapsed)",
        [_ledger_row(first_raised=iso(now - timedelta(hours=49)), urgency="normal")],
        expect_due=True,
    )
    # TTL invariant, expressed as behaviour: the Telegram nonce dies at 72h, so a
    # normal card MUST have been re-pinged before 72h or the buttons go dead.
    case(
        "TTL invariant: a normal card 71h old must already be due",
        [_ledger_row(first_raised=iso(now - timedelta(hours=71)), urgency="normal")],
        expect_due=True,
    )
    case(
        "backoff: after 3 pings the interval doubles to 96h, so 49h is too early",
        [
            _ledger_row(
                first_raised=iso(now - timedelta(hours=200)),
                last_ping_at=iso(now - timedelta(hours=49)),
                reescalation_count=3,
                urgency="normal",
            )
        ],
        expect_due=False,
    )
    case(
        "backoff: after 3 pings, 97h since the last ping is due",
        [
            _ledger_row(
                first_raised=iso(now - timedelta(hours=200)),
                last_ping_at=iso(now - timedelta(hours=97)),
                reescalation_count=3,
                urgency="normal",
            )
        ],
        expect_due=True,
    )
    case(
        "urgent scales to deadline: 10h remaining at last ping means 5h interval, 1h is early",
        [
            _ledger_row(
                first_raised=iso(now - timedelta(hours=1)),
                urgency="urgent",
                due=iso(now + timedelta(hours=9)),
            )
        ],
        expect_due=False,
    )
    case(
        "urgent scales to deadline: 10h remaining at last ping, 6h elapsed is due",
        [
            _ledger_row(
                first_raised=iso(now - timedelta(hours=6)),
                urgency="urgent",
                due=iso(now + timedelta(hours=4)),
            )
        ],
        expect_due=True,
    )
    case(
        "urgent past due re-pings on the 2h floor",
        [
            _ledger_row(
                first_raised=iso(now - timedelta(hours=30)),
                last_ping_at=iso(now - timedelta(hours=3)),
                urgency="urgent",
                due=iso(now - timedelta(hours=5)),
            )
        ],
        expect_due=True,
    )
    case(
        "urgent floor is 2h, not less: 1h after the last ping is early",
        [
            _ledger_row(
                first_raised=iso(now - timedelta(hours=30)),
                last_ping_at=iso(now - timedelta(hours=1)),
                urgency="urgent",
                due=iso(now - timedelta(hours=5)),
            )
        ],
        expect_due=False,
    )
    case(
        "a retired card must never be re-escalated",
        [
            _ledger_row(
                first_raised=iso(now - timedelta(hours=500)),
                urgency="normal",
                status="retired",
            )
        ],
        expect_due=False,
    )
    case(
        "a decided card must never be re-escalated",
        [
            _ledger_row(
                first_raised=iso(now - timedelta(hours=500)),
                urgency="normal",
                status="decided:approve",
            )
        ],
        expect_due=False,
    )
    # One ping per card per tick even when several ledger rows share the card.
    shared = [
        _ledger_row(
            row_hash=f"h{index}",
            card_identifier="ANK-9",
            first_raised=iso(now - timedelta(hours=100)),
            urgency="normal",
        )
        for index in range(3)
    ]
    got = _plan(repo, shared, now)
    if got is not None and len(got) != 1:
        fail(
            "three ledger rows share card ANK-9; exactly one ping is owed, "
            f"but --plan-only returned {sorted(got)}"
        )
    # Backoff must be capped, never unbounded.
    case(
        "backoff caps at 336h: 337h after the last ping of a very old card is due",
        [
            _ledger_row(
                first_raised=iso(now - timedelta(hours=5000)),
                last_ping_at=iso(now - timedelta(hours=337)),
                reescalation_count=20,
                urgency="normal",
            )
        ],
        expect_due=True,
    )


# ----------------------------------------------------------------- registry


def check_registry(repo: Path) -> None:
    base = {
        "cursor_file": "/tmp/probe-cursor.json",
        "ledger_file": "/tmp/probe-ledger.jsonl",
        "watches": [],
        "senders": {"card_enabled": False, "ping": ["true"]},
    }
    import yaml  # noqa: PLC0415 - only needed for this branch

    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "outbox.yaml"
        config.write_text(yaml.safe_dump(base), encoding="utf-8")
        argv = [
            sys.executable,
            "factory/mailroom_registry.py",
            "--config",
            str(config),
            "--department",
            "probe",
            "--outbox",
            "/tmp/probe/decisions_outbox.jsonl",
        ]
        first = run(argv, repo)
        if first.returncode != 0:
            fail(
                f"mailroom_registry.py exited {first.returncode} on a valid config. "
                f"stderr: {(first.stderr or '')[:400]}"
            )
            return
        second = run(argv, repo)
        if second.returncode != 0:
            fail(
                "registering the same department twice must be idempotent, but the "
                f"second call exited {second.returncode}. stderr: {(second.stderr or '')[:400]}"
            )
        loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
        watches = loaded.get("watches")
        if not isinstance(watches, list) or len(watches) != 1:
            fail(
                "after registering the same department twice, watches must hold "
                f"exactly 1 entry; got {watches!r}"
            )
        elif watches[0].get("department") != "probe" or watches[0].get(
            "path"
        ) != "/tmp/probe/decisions_outbox.jsonl":
            fail(f"the registered watch entry is wrong: {watches[0]!r}")
        elif watches[0].get("kind") not in {"escalation", "approval"}:
            fail(
                "the registered watch kind must be escalation or approval "
                f"(outbox_push.KINDS); got {watches[0].get('kind')!r}"
            )
        if loaded.get("cursor_file") != base["cursor_file"] or loaded.get(
            "senders"
        ) != base["senders"]:
            fail(
                "registering a department must not disturb cursor_file or senders; "
                f"config is now {loaded!r}"
            )

        broken = Path(tmp) / "broken.yaml"
        broken.write_text("- this is a list, not a mapping\n", encoding="utf-8")
        bad = run(
            [
                sys.executable,
                "factory/mailroom_registry.py",
                "--config",
                str(broken),
                "--department",
                "probe",
                "--outbox",
                "/tmp/probe/decisions_outbox.jsonl",
            ],
            repo,
        )
        if bad.returncode == 0:
            fail(
                "a malformed config must fail closed with a nonzero exit and a "
                "printed reason, but the registry exited 0"
            )
        elif not ((bad.stderr or "") + (bad.stdout or "")).strip():
            fail("the registry failed closed but printed no reason; retry prompts need WHY")

    scaffold = (repo / "factory" / "scaffold.py").read_text(encoding="utf-8")
    if "mailroom_registry" not in scaffold:
        fail(
            "factory/scaffold.py must call the mailroom registry so every new "
            "department is wired to the human channel at F0; no reference found"
        )
    runbook = (repo / "runbooks" / "factory-pipeline.md").read_text(
        encoding="utf-8"
    ).lower()
    if "mailroom" not in runbook:
        fail(
            "runbooks/factory-pipeline.md must document the mailroom wiring step; "
            "the word 'mailroom' does not appear"
        )


# -------------------------------------------------------------------- stall


def check_stall(repo: Path) -> None:
    import yaml  # noqa: PLC0415

    def config_for(tmp: Path, paths: list[str]) -> Path:
        config = tmp / "outbox.yaml"
        config.write_text(
            yaml.safe_dump(
                {
                    "cursor_file": str(tmp / "cursor.json"),
                    "watches": [
                        {"path": path, "department": "probe", "kind": "escalation"}
                        for path in paths
                    ],
                    "senders": {"card_enabled": False, "ping": ["true"]},
                }
            ),
            encoding="utf-8",
        )
        return config

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        missing = config_for(tmp, [str(tmp / "gone-a.jsonl"), str(tmp / "gone-b.jsonl")])
        result = run(
            [sys.executable, "factory/outbox_push.py", "--config", str(missing), "--once"],
            repo,
        )
        if result.returncode == 0:
            fail(
                "a tick where EVERY configured watch path is missing must NOT exit 0. "
                "This is the exact failure that hid a dead approval channel for six "
                "days: both timers reported success while watching a file that did "
                "not exist. Exit nonzero and name the missing paths."
            )
        combined = (result.stderr or "") + (result.stdout or "")
        if "gone-a.jsonl" not in combined:
            fail(
                "the stall failure must name the missing watch path(s) so the "
                f"operator can fix it; output was: {combined[:400]!r}"
            )

        present = tmp / "there.jsonl"
        present.write_text("", encoding="utf-8")
        healthy = config_for(tmp, [str(present), str(tmp / "gone-c.jsonl")])
        ok = run(
            [sys.executable, "factory/outbox_push.py", "--config", str(healthy), "--once"],
            repo,
        )
        if ok.returncode != 0:
            fail(
                "a tick where at least one watch path exists is NOT a stall and must "
                f"still exit 0; got {ok.returncode}. stderr: {(ok.stderr or '')[:400]}"
            )


# ----------------------------------------------------------------- shared


def check_suite(repo: Path) -> None:
    """The repo's own gate. A worker may not break anything else to pass."""
    result = run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        repo,
        timeout=900,
    )
    if result.returncode != 0:
        tail = (result.stdout or "")[-1500:]
        fail(f"the repo test suite failed (exit {result.returncode}):\n{tail}")


def check_ownership_and_export(repo: Path, task: str, export: Path) -> None:
    owned = OWNED[task]
    # Only the source areas a worker could break are policed. Scratch notes
    # elsewhere are harmless; edits under these prefixes are not.
    guarded = ("factory/", "tests/", "kernel/", "runbooks/", "departments/", "loopfactory.py")
    status = run(["git", "status", "--porcelain"], repo)
    touched = set()
    for line in status.stdout.splitlines():
        path = line[3:].strip()
        if path:
            touched.add(path)
    stray = sorted(
        path
        for path in touched
        if path not in owned and path.startswith(guarded)
    )
    if stray:
        fail(
            f"task '{task}' owns only {owned}, but git status also shows {stray}. "
            "Revert everything outside your ownership list."
        )
    produced = sorted(path for path in touched if path in owned)
    if not produced:
        fail(f"task '{task}' changed none of its owned files {owned}; nothing was built")
        return
    run(["git", "add", "--"] + owned, repo)
    diff = run(["git", "diff", "--cached"], repo)
    export.mkdir(parents=True, exist_ok=True)
    target = export / f"{task}.patch"
    target.write_text(diff.stdout, encoding="utf-8")
    if not diff.stdout.strip():
        fail("the exported patch is empty; the worktree is deleted on PASS so this would lose the work")
    print(f"exported {len(diff.stdout.splitlines())} patch lines to {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, choices=sorted(OWNED))
    # In worktrees mode the check runs with cwd set to the task's worktree, so
    # cwd is the right default; --repo stays available for manual proving.
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--export", required=True)
    parser.add_argument(
        "--notes",
        help="absolute path to copy the worker's ./notes.md to before the worktree dies",
    )
    args = parser.parse_args()
    repo = Path(args.repo).resolve()

    if args.notes:
        source = repo / "notes.md"
        if not source.exists():
            fail(
                "./notes.md was not written at the worktree root. It is required: it "
                "is the only record of what you read, what you decided, and which "
                "verification command you actually ran."
            )
        elif len(source.read_text(encoding="utf-8", errors="replace").split()) < 40:
            fail(
                "./notes.md is under 40 words. Record what you read, what you changed, "
                "the exact verification command and its result, and any assumption."
            )
        else:
            target = Path(args.notes)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                source.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
            )
            print(f"exported notes to {target}")

    {"reescalate": check_reescalate, "registry": check_registry, "stall": check_stall}[
        args.task
    ](repo)
    check_suite(repo)
    check_ownership_and_export(repo, args.task, Path(args.export).resolve())

    if FAILURES:
        print(f"\nCHECK FAILED for task '{args.task}' — {len(FAILURES)} problem(s):\n")
        for index, message in enumerate(FAILURES, start=1):
            print(f"{index}. {message}\n")
        return 1
    print(f"CHECK PASS for task '{args.task}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
