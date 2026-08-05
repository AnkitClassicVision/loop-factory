#!/usr/bin/env python3
"""Independent oracle for r5: timer-ready re-escalation, and the remote pull leg."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

FAILURES: list[str] = []
OWNED = {
    "reescalate-timer": ["factory/reescalate.py", "tests/test_reescalate_timer.py"],
    "pull-leg": ["factory/remote_outbox_pull.py", "tests/test_remote_outbox_pull.py"],
}


def fail(m: str) -> None:
    FAILURES.append(m)


def run(argv, cwd, timeout=300):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def recorder(tmp: Path, name: str, *, exit_code: int = 0) -> Path:
    s = tmp / f"{name}.py"
    s.write_text(
        "import json, pathlib, sys\n"
        f"c=pathlib.Path({str(tmp / (name + '_calls.jsonl'))!r})\n"
        "with c.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return s


def calls(tmp: Path, name: str):
    p = tmp / f"{name}_calls.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def check_reescalate_timer(repo: Path) -> None:
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        ledger = tmp / "ledger.jsonl"
        ledger.write_text(
            json.dumps({
                "row_hash": "h1", "card_identifier": "ANK-600", "status": "open",
                "department": "probe", "kind": "escalation", "summary": "probe ask",
                "first_raised": (now - timedelta(hours=100)).isoformat(),
                "urgency": "normal", "packet_text": "probe body",
            }) + "\n",
            encoding="utf-8",
        )
        send = recorder(tmp, "send")
        cfg = tmp / "cfg.yaml"
        cfg.write_text(yaml.safe_dump({"senders": {"reescalation": [
            sys.executable, str(send), "{card_identifier}", "{department}",
            "{reescalation_count}", "{first_raised}", "{text}",
        ]}}), encoding="utf-8")

        # --now must be OPTIONAL so a systemd timer can call this with no shell.
        r = run([sys.executable, "factory/reescalate.py", "--ledger", str(ledger),
                 "--config", str(cfg)], repo)
        if r.returncode != 0:
            fail(
                "--now must be OPTIONAL, defaulting to the real current time, so a "
                "systemd timer can invoke this directly without a shell computing a "
                f"date. Got exit {r.returncode}: {(r.stderr or '')[:300]}"
            )
            return
        sent = calls(tmp, "send")
        if len(sent) != 1:
            fail(f"a 100h-old normal card is overdue and must be re-pinged exactly once; got {len(sent)} call(s)")
            return
        got = sent[0]
        if got[0] != "ANK-600":
            fail(f"{{card_identifier}} must render the real card so the re-armed buttons bind to it; got {got!r}")
        if got[1] != "probe":
            fail(f"{{department}} must be available to the sender; got {got!r}")
        # The count is rendered POST-increment, matching the pre-existing
        # contract in tests/test_reescalate.py. This is the humane reading: the
        # buzz says "re-escalation #1" for the first re-ping, and "#0" would be
        # nonsense on a phone. An earlier version of this oracle asserted the
        # pre-increment value and forced a regression; the existing test was right.
        if got[2] != "1":
            fail(f"{{reescalation_count}} must render 1 on the first re-ping (post-increment); got {got!r}")
        if not got[3] or "T" not in got[3]:
            fail(f"{{first_raised}} must render the ISO date the card was first raised; got {got!r}")

        # The ledger must record the ping so the backoff advances next time.
        rows = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
        last = rows[-1]
        if last.get("status") != "open":
            fail(f"a re-ping must NOT change the card's status; it is still unanswered. Got {last.get('status')!r}")
        if last.get("reescalation_count") != 1:
            fail(f"the ledger must record reescalation_count 1 after the first re-ping; got {last.get('reescalation_count')!r}")
        if not last.get("last_ping_at"):
            fail("the ledger must record last_ping_at or the cadence restarts from first_raised forever")

        # Second run at the same instant must NOT double-ping.
        r2 = run([sys.executable, "factory/reescalate.py", "--ledger", str(ledger), "--config", str(cfg)], repo)
        if r2.returncode != 0:
            fail(f"a second immediate run must exit 0 with nothing due; got {r2.returncode}")
        if len(calls(tmp, "send")) != 1:
            fail("running twice in a row must not re-ping the same card; the backoff clock just reset")


def check_pull_leg(repo: Path) -> None:
    script = repo / "factory" / "remote_outbox_pull.py"
    if not script.exists():
        fail("factory/remote_outbox_pull.py does not exist")
        return
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        remote = tmp / "remote"
        remote.mkdir()
        (remote / "decisions_outbox.jsonl").write_text(
            json.dumps({"kind": "escalation", "department": "codex-macmini", "eli5": "probe"}) + "\n",
            encoding="utf-8",
        )
        local = tmp / "local" / "decisions_outbox.jsonl"
        # A local-path transport must be supported so the puller is testable
        # without a second machine.
        r = run([sys.executable, "factory/remote_outbox_pull.py", "--source", str(remote / "decisions_outbox.jsonl"),
                 "--dest", str(local)], repo)
        if r.returncode != 0:
            fail(
                "the puller must accept a plain local --source path so it can be verified "
                f"without a second machine. Got exit {r.returncode}: {(r.stderr or '')[:300]}"
            )
            return
        if not local.exists():
            fail(f"{local} was not created; the pulled outbox must land where the mailroom watches")
            return
        if len(local.read_text().splitlines()) != 1:
            fail("exactly the one remote row must land")

        # Idempotent: pulling again must not duplicate rows.
        run([sys.executable, "factory/remote_outbox_pull.py", "--source", str(remote / "decisions_outbox.jsonl"),
             "--dest", str(local)], repo)
        if len(local.read_text().splitlines()) != 1:
            fail(
                "pulling twice duplicated rows. The mailroom dedups by row hash, but a "
                "puller that grows the file every tick will still re-push history."
            )

        # Appending remotely then pulling must bring only the new row.
        with (remote / "decisions_outbox.jsonl").open("a", encoding="utf-8") as h:
            h.write(json.dumps({"kind": "escalation", "department": "codex-macmini", "eli5": "second"}) + "\n")
        run([sys.executable, "factory/remote_outbox_pull.py", "--source", str(remote / "decisions_outbox.jsonl"),
             "--dest", str(local)], repo)
        if len(local.read_text().splitlines()) != 2:
            fail(f"a new remote row must arrive; local now has {len(local.read_text().splitlines())} row(s)")

        # An EMPTY source is the normal steady state: nobody has raised anything.
        # It must be a quiet success, not a failure. A timer that goes red every
        # tick whenever there is nothing to approve trains the operator to ignore
        # it, which is the always-green disease inverted.
        empty_src = tmp / "empty.jsonl"
        empty_src.write_text("", encoding="utf-8")
        empty_dest = tmp / "empty-dest.jsonl"
        quiet = run([sys.executable, "factory/remote_outbox_pull.py", "--source", str(empty_src),
                     "--dest", str(empty_dest)], repo)
        if quiet.returncode != 0:
            fail(
                "an EMPTY source must exit 0 with 0 rows added. An empty outbox means "
                "nobody has raised anything, which is the normal state most of the "
                f"time. Got exit {quiet.returncode}: {(quiet.stderr or '')[:250]}"
            )

        # Fail closed when the source is unreachable, and never truncate what we have.
        bad = run([sys.executable, "factory/remote_outbox_pull.py", "--source", str(tmp / "nope.jsonl"),
                   "--dest", str(local)], repo)
        if bad.returncode == 0:
            fail("an unreachable source must exit nonzero, not silently report success")
        if len(local.read_text().splitlines()) != 2:
            fail(
                "a failed pull must NEVER truncate or clobber the rows already pulled. "
                "Losing a raised packet loses a decision the owner never got to make."
            )


def check_suite(repo: Path) -> None:
    r = run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], repo, timeout=900)
    if r.returncode != 0:
        fail(f"the repo test suite failed (exit {r.returncode}):\n{(r.stdout or '')[-1200:]}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True, choices=sorted(OWNED))
    p.add_argument("--repo", default=os.getcwd())
    p.add_argument("--export", required=True)
    p.add_argument("--notes")
    a = p.parse_args()
    repo = Path(a.repo).resolve()

    {"reescalate-timer": check_reescalate_timer, "pull-leg": check_pull_leg}[a.task](repo)
    check_suite(repo)

    owned = OWNED[a.task]
    guarded = ("factory/", "tests/", "kernel/", "runbooks/", "departments/", "loopfactory.py")
    st = run(["git", "status", "--porcelain"], repo)
    touched = {l[3:].strip() for l in st.stdout.splitlines() if l[3:].strip()}
    stray = sorted(x for x in touched if x not in owned and x.startswith(guarded))
    if stray:
        fail(f"task '{a.task}' owns only {owned}, but git status also shows {stray}")
    if a.notes:
        src = repo / "notes.md"
        if not src.exists():
            fail("./notes.md was not written at the worktree root")
        else:
            t = Path(a.notes)
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    run(["git", "add", "--"] + owned, repo)
    d = run(["git", "diff", "--cached"], repo)
    ex = Path(a.export).resolve()
    ex.mkdir(parents=True, exist_ok=True)
    (ex / f"{a.task}.patch").write_text(d.stdout, encoding="utf-8")
    if not d.stdout.strip():
        fail("the exported patch is empty; a passing worktree is deleted so the work would be lost")

    if FAILURES:
        print(f"\nCHECK FAILED for '{a.task}' — {len(FAILURES)} problem(s):\n")
        for i, m in enumerate(FAILURES, 1):
            print(f"{i}. {m}\n")
        return 1
    print(f"CHECK PASS for '{a.task}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
