#!/usr/bin/env python3
"""Independent oracle for the buzz sender that attaches phone buttons to a card.

The gap this closes: outbox_push sends its ping BEFORE creating the card, so the
ping can never carry the card identifier the Telegram buttons need. A buzz
sender must therefore run AFTER a successful card creation, rendered with the
real identifier.

Runs with cwd set to the loop-factory worktree.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

FAILURES: list[str] = []
OWNED = ["factory/outbox_push.py", "tests/test_outbox_push_buzz.py"]


def fail(message: str) -> None:
    FAILURES.append(message)


def recorder(tmp: Path, name: str, *, exit_code: int = 0, stdout: str = "") -> Path:
    script = tmp / f"{name}.py"
    script.write_text(
        "import json, pathlib, sys\n"
        f"calls=pathlib.Path({str(tmp / (name + '_calls.jsonl'))!r})\n"
        "with calls.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"print({stdout!r})\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return script


def calls(tmp: Path, name: str) -> list[list[str]]:
    path = tmp / f"{name}_calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_push(repo: Path, config: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "factory/outbox_push.py", "--config", str(config), "--once"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


def build(tmp: Path, *, card_ok: bool = True, buzz_ok: bool = True, with_buzz: bool = True) -> Path:
    watch = tmp / "outbox.jsonl"
    watch.write_text(
        json.dumps({"kind": "escalation", "department": "probe", "issue": "probe issue", "eli5": "[probe] needs you: probe issue", "ts": "2026-08-05T12:00:00+00:00"}) + "\n",
        encoding="utf-8",
    )
    card_stdout = json.dumps({"identifier": "ANK-777", "url": "https://example.test/ANK-777"}) if card_ok else "no json here"
    senders = {
        "card_enabled": True,
        "ping": [sys.executable, str(recorder(tmp, "ping")), "{text}"],
        "card": [sys.executable, str(recorder(tmp, "card", exit_code=0 if card_ok else 1, stdout=card_stdout)), "{title}"],
    }
    if with_buzz:
        senders["buzz"] = [
            sys.executable,
            str(recorder(tmp, "buzz", exit_code=0 if buzz_ok else 1)),
            "{card}",
            "{department}",
            "{kind}",
        ]
    config = tmp / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "cursor_file": str(tmp / "cursor.json"),
                "ledger_file": str(tmp / "ledger.jsonl"),
                "watches": [{"path": str(watch), "department": "probe", "kind": "escalation"}],
                "senders": senders,
            }
        ),
        encoding="utf-8",
    )
    return config


def check_buzz(repo: Path) -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        result = run_push(repo, build(tmp))
        if result.returncode != 0:
            fail(f"a healthy tick with a buzz sender must exit 0; got {result.returncode}. stderr: {(result.stderr or '')[:400]}")
        buzz = calls(tmp, "buzz")
        if len(buzz) != 1:
            fail(
                "after a successful card creation the buzz sender must be invoked "
                f"exactly once so the owner's phone gets buttons bound to that card; got {len(buzz)} call(s)"
            )
        elif buzz[0][0] != "ANK-777":
            fail(
                "the buzz sender must be rendered with the REAL card identifier the "
                "card creator returned, because the Telegram buttons carry it in their "
                f"callback data. Got {buzz[0]!r}, expected ANK-777 first."
            )
        elif buzz[0][1:] != ["probe", "escalation"]:
            fail(f"buzz must also render department and kind; got {buzz[0]!r}")

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        run_push(repo, build(tmp, card_ok=False))
        if calls(tmp, "buzz"):
            fail(
                "the buzz sender must NOT run when card creation failed. Buttons with "
                "no card behind them would let the owner approve something that has no "
                "durable record."
            )

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        result = run_push(repo, build(tmp, buzz_ok=False))
        if result.returncode != 0:
            fail(
                "a failed buzz must NOT fail the tick: the card already exists and is "
                "the durable record, so losing the phone nudge must never lose the "
                f"alert. Got exit {result.returncode}."
            )
        combined = (result.stderr or "") + (result.stdout or "")
        if "buzz" not in combined.lower():
            fail("a failed buzz must be logged loudly enough to name it; nothing in the output mentions buzz")

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        result = run_push(repo, build(tmp, with_buzz=False))
        if result.returncode != 0:
            fail(
                "buzz must stay OPTIONAL so existing configs keep working; a config "
                f"without it must still exit 0. Got {result.returncode}: {(result.stderr or '')[:300]}"
            )
        if not calls(tmp, "card"):
            fail("without a buzz sender the card must still be created")


def check_suite(repo: Path) -> None:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
        cwd=repo, capture_output=True, text=True, timeout=900, env=env,
    )
    if result.returncode != 0:
        fail(f"the repo test suite failed (exit {result.returncode}):\n{(result.stdout or '')[-1200:]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--export", required=True)
    parser.add_argument("--notes")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()

    check_buzz(repo)
    check_suite(repo)

    guarded = ("factory/", "tests/", "kernel/", "runbooks/", "departments/", "loopfactory.py")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
    touched = {l[3:].strip() for l in status.stdout.splitlines() if l[3:].strip()}
    stray = sorted(p for p in touched if p not in OWNED and p.startswith(guarded))
    if stray:
        fail(f"you own only {OWNED}, but git status also shows {stray}")
    if args.notes and (repo / "notes.md").exists():
        target = Path(args.notes)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((repo / "notes.md").read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    elif args.notes:
        fail("./notes.md was not written at the worktree root")
    subprocess.run(["git", "add", "--"] + OWNED, cwd=repo, capture_output=True, text=True)
    diff = subprocess.run(["git", "diff", "--cached"], cwd=repo, capture_output=True, text=True)
    export = Path(args.export).resolve()
    export.mkdir(parents=True, exist_ok=True)
    (export / "buzz.patch").write_text(diff.stdout, encoding="utf-8")
    if not diff.stdout.strip():
        fail("the exported patch is empty; a passing worktree is deleted so the work would be lost")

    if FAILURES:
        print(f"\nCHECK FAILED — {len(FAILURES)} problem(s):\n")
        for index, message in enumerate(FAILURES, start=1):
            print(f"{index}. {message}\n")
        return 1
    print("CHECK PASS: the buzz sender fires after a real card, only after a real card")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
