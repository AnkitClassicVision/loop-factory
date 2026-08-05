#!/usr/bin/env python3
"""Oracle for the urgency seam between outbox_push and reescalate.

Found live 2026-08-05: reescalate refuses every real card because the ledger
rows outbox_push writes carry no urgency field at all. Each component was
correct in isolation. The seam was never exercised.
"""
from __future__ import annotations

import argparse, json, os, subprocess, sys, tempfile
from pathlib import Path
import yaml

FAILURES: list[str] = []
OWNED = ["factory/outbox_push.py", "factory/reescalate.py", "tests/test_urgency_seam.py"]


def fail(m): FAILURES.append(m)


def run(argv, cwd, timeout=300):
    env = dict(os.environ); env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def push_ledger(repo: Path, tmp: Path, packet: dict) -> list[dict]:
    watch = tmp / "outbox.jsonl"; watch.write_text(json.dumps(packet) + "\n", encoding="utf-8")
    card = tmp / "card.py"
    card.write_text("import sys; print('{\"identifier\": \"ANK-1\", \"url\": \"https://e.test/1\"}')\n", encoding="utf-8")
    noop = tmp / "noop.py"; noop.write_text("raise SystemExit(0)\n", encoding="utf-8")
    ledger = tmp / "ledger.jsonl"
    cfg = tmp / "c.yaml"
    cfg.write_text(yaml.safe_dump({
        "cursor_file": str(tmp / "cur.json"), "ledger_file": str(ledger),
        "watches": [{"path": str(watch), "department": "probe", "kind": "escalation"}],
        "senders": {"card_enabled": True, "ping": [sys.executable, str(noop)],
                    "card": [sys.executable, str(card), "{title}"]},
    }), encoding="utf-8")
    r = run([sys.executable, "factory/outbox_push.py", "--config", str(cfg), "--once"], repo)
    if r.returncode != 0:
        fail(f"push exited {r.returncode}: {(r.stderr or '')[:300]}")
        return []
    return [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]


def check(repo: Path) -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        rows = push_ledger(repo, tmp, {"kind": "escalation", "eli5": "probe", "ts": "2026-08-05T12:00:00+00:00",
                                       "urgency": "urgent", "due": "2026-08-09T12:00:00+00:00"})
        if rows and rows[-1].get("urgency") != "urgent":
            fail(f"a packet marked urgent must put urgency on the ledger row so the cadence can scale to the deadline; got {rows[-1].get('urgency')!r}")
        if rows and rows[-1].get("due") != "2026-08-09T12:00:00+00:00":
            fail(f"the packet's due date must reach the ledger row; got {rows[-1].get('due')!r}")

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        rows = push_ledger(repo, tmp, {"kind": "escalation", "eli5": "probe", "ts": "2026-08-05T12:00:00+00:00"})
        if rows and rows[-1].get("urgency") != "normal":
            fail(f"a packet with no urgency must default to normal on the ledger row; got {rows[-1].get('urgency')!r}")

    # A legacy row with NO urgency must be treated as normal, not refused.
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw); ledger = tmp / "l.jsonl"
        ledger.write_text(json.dumps({"row_hash": "h", "card_identifier": "ANK-2", "status": "open",
                                      "department": "probe", "kind": "escalation",
                                      "first_raised": "2026-01-01T00:00:00+00:00"}) + "\n", encoding="utf-8")
        r = run([sys.executable, "factory/reescalate.py", "--ledger", str(ledger), "--plan-only"], repo)
        if r.returncode != 0:
            fail("a ledger row with NO urgency field must be treated as 'normal', not refused. Every card written before this field existed lacks it, and refusing them means re-escalation never runs for any real card. "
                 f"Got exit {r.returncode}: {(r.stderr or '')[:250]}")
        elif '"ANK-2"' not in r.stdout and "ANK-2" not in r.stdout:
            fail("a very old undated-urgency card must still come out as due")

    # An INVALID urgency must still fail closed. Lenient on absent, strict on wrong.
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw); ledger = tmp / "l.jsonl"
        ledger.write_text(json.dumps({"row_hash": "h", "card_identifier": "ANK-3", "status": "open",
                                      "department": "probe", "kind": "escalation", "urgency": "banana",
                                      "first_raised": "2026-01-01T00:00:00+00:00"}) + "\n", encoding="utf-8")
        r = run([sys.executable, "factory/reescalate.py", "--ledger", str(ledger), "--plan-only"], repo)
        if r.returncode == 0:
            fail("an INVALID urgency value must still be refused loudly. Absent means 'nobody said', which is safely normal; 'banana' means something is corrupt.")

    r = run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], repo, timeout=900)
    if r.returncode != 0:
        fail(f"the repo test suite failed (exit {r.returncode}):\n{(r.stdout or '')[-1000:]}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=os.getcwd()); p.add_argument("--export", required=True); p.add_argument("--notes")
    a = p.parse_args(); repo = Path(a.repo).resolve()
    check(repo)
    st = run(["git", "status", "--porcelain"], repo)
    guarded = ("factory/", "tests/", "kernel/", "runbooks/", "loopfactory.py")
    stray = sorted(x for x in {l[3:].strip() for l in st.stdout.splitlines() if l[3:].strip()}
                   if x not in OWNED and x.startswith(guarded))
    if stray: fail(f"you own only {OWNED}, but git status also shows {stray}")
    if a.notes:
        src = repo / "notes.md"
        if not src.exists(): fail("./notes.md was not written")
        else:
            t = Path(a.notes); t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    run(["git", "add", "--"] + OWNED, repo)
    d = run(["git", "diff", "--cached"], repo)
    ex = Path(a.export).resolve(); ex.mkdir(parents=True, exist_ok=True)
    (ex / "urgency-seam.patch").write_text(d.stdout, encoding="utf-8")
    if not d.stdout.strip(): fail("the exported patch is empty")
    if FAILURES:
        print(f"\nCHECK FAILED — {len(FAILURES)} problem(s):\n")
        for i, m in enumerate(FAILURES, 1): print(f"{i}. {m}\n")
        return 1
    print("CHECK PASS: urgency flows push -> ledger -> reescalate; absent is normal, invalid still refuses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
