#!/usr/bin/env python3
"""Oracle for the return half: packet_id propagation, and pushing decisions back.

Found live 2026-08-05: Codex on a second machine raised a packet, the owner
approved it, the decision was recorded here, and Codex still cannot see it. It
polls on packet_id; the decision row only carries card_identifier. And nothing
writes decisions back to that machine at all. The agent waits correctly forever
on a system that is failing it.
"""
from __future__ import annotations

import argparse, json, os, subprocess, sys, tempfile
from pathlib import Path
import yaml

FAILURES: list[str] = []
OWNED = {
    "packet-id-flow": ["factory/outbox_push.py", "factory/outbox_listen.py", "tests/test_packet_id_flow.py"],
    "push-back": ["factory/decision_push_back.py", "tests/test_decision_push_back.py"],
}


def fail(m): FAILURES.append(m)


def run(argv, cwd, timeout=300):
    env = dict(os.environ); env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)


def check_packet_id_flow(repo: Path) -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        watch = tmp / "outbox.jsonl"
        watch.write_text(json.dumps({
            "packet_id": "cdx-probe-a1", "kind": "escalation", "eli5": "probe ask",
            "ts": "2026-08-05T12:00:00+00:00", "department": "codex-macmini",
        }) + "\n", encoding="utf-8")
        card = tmp / "card.py"
        card.write_text("print('{\"identifier\": \"ANK-800\", \"url\": \"https://e.test/800\"}')\n", encoding="utf-8")
        noop = tmp / "noop.py"; noop.write_text("raise SystemExit(0)\n", encoding="utf-8")
        ledger = tmp / "ledger.jsonl"
        pcfg = tmp / "push.yaml"
        pcfg.write_text(yaml.safe_dump({
            "cursor_file": str(tmp / "cur.json"), "ledger_file": str(ledger),
            "watches": [{"path": str(watch), "department": "codex-macmini", "kind": "escalation"}],
            "senders": {"card_enabled": True, "ping": [sys.executable, str(noop)],
                        "card": [sys.executable, str(card), "{title}"]},
        }), encoding="utf-8")
        r = run([sys.executable, "factory/outbox_push.py", "--config", str(pcfg), "--once"], repo)
        if r.returncode != 0:
            fail(f"push exited {r.returncode}: {(r.stderr or '')[:300]}"); return
        rows = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]
        if not rows or rows[-1].get("packet_id") != "cdx-probe-a1":
            fail("the packet's packet_id must land on the ledger row. Without it the "
                 "decision can never be matched back to the agent that asked, because "
                 f"the agent polls on packet_id, not card_identifier. Got {rows[-1].get('packet_id') if rows else None!r}")
            return

        # Now the listener: a decision must carry that packet_id through.
        reader = tmp / "reader.py"
        reader.write_text("import json,sys; print(json.dumps([{'body':'APPROVE proposal','createdAt':'2026-08-05T13:00:00Z'}]))\n", encoding="utf-8")
        lcfg = tmp / "listen.yaml"
        lcfg.write_text(yaml.safe_dump({
            "ledger_file": str(ledger),
            "listener": {"reader": [sys.executable, str(reader), "{issue}"],
                         "closer": [sys.executable, str(noop), "{issue}", "{state}"],
                         "close_enabled": True, "decisions_file": str(tmp / "decisions.jsonl")},
        }), encoding="utf-8")
        r2 = run([sys.executable, "factory/outbox_listen.py", "--config", str(lcfg), "--once"], repo)
        if r2.returncode != 0:
            fail(f"listen exited {r2.returncode}: {(r2.stderr or '')[:300]}"); return
        drows = [json.loads(l) for l in (tmp / "decisions.jsonl").read_text().splitlines() if l.strip()]
        if not drows:
            fail("no decision was recorded"); return
        if drows[-1].get("packet_id") != "cdx-probe-a1":
            fail("the DECISION row must carry packet_id so the raising agent can match "
                 f"the answer to its question. Got {drows[-1].get('packet_id')!r}. Keys: {sorted(drows[-1])}")

    # A packet with no packet_id must still work; not every caller uses one.
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        watch = tmp / "o.jsonl"
        watch.write_text(json.dumps({"kind": "escalation", "eli5": "no id", "ts": "2026-08-05T12:00:00+00:00"}) + "\n", encoding="utf-8")
        card = tmp / "c.py"; card.write_text("print('{\"identifier\": \"ANK-801\"}')\n", encoding="utf-8")
        noop = tmp / "n.py"; noop.write_text("raise SystemExit(0)\n", encoding="utf-8")
        cfg = tmp / "p.yaml"
        cfg.write_text(yaml.safe_dump({
            "cursor_file": str(tmp / "c.json"), "ledger_file": str(tmp / "l.jsonl"),
            "watches": [{"path": str(watch), "department": "probe", "kind": "escalation"}],
            "senders": {"card_enabled": True, "ping": [sys.executable, str(noop)], "card": [sys.executable, str(card), "{title}"]},
        }), encoding="utf-8")
        r = run([sys.executable, "factory/outbox_push.py", "--config", str(cfg), "--once"], repo)
        if r.returncode != 0:
            fail(f"a packet WITHOUT packet_id must still push cleanly; got exit {r.returncode}: {(r.stderr or '')[:250]}")


def check_push_back(repo: Path) -> None:
    script = repo / "factory" / "decision_push_back.py"
    if not script.exists():
        fail("factory/decision_push_back.py does not exist"); return
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        decisions = tmp / "decisions.jsonl"
        decisions.write_text("".join(json.dumps(r) + "\n" for r in [
            {"packet_id": "a1", "department": "codex-macmini", "decision": "approve", "card_identifier": "ANK-1"},
            {"packet_id": "b1", "department": "outreach", "decision": "approve", "card_identifier": "ANK-2"},
            {"department": "codex-macmini", "decision": "skip", "card_identifier": "ANK-3"},
        ]), encoding="utf-8")
        dest = tmp / "remote" / "decisions.jsonl"
        r = run([sys.executable, "factory/decision_push_back.py", "--decisions", str(decisions),
                 "--department", "codex-macmini", "--dest", str(dest)], repo)
        if r.returncode != 0:
            fail(f"a local --dest must work so this is testable without a second machine; exit {r.returncode}: {(r.stderr or '')[:250]}")
            return
        if not dest.exists():
            fail("nothing was written to --dest"); return
        got = [json.loads(l) for l in dest.read_text().splitlines() if l.strip()]
        ids = [g.get("packet_id") for g in got]
        if "b1" in ids:
            fail("a decision for a DIFFERENT department must not be pushed to this machine; that is a cross-surface leak")
        if ids != ["a1"]:
            fail("only rows for the named department that actually carry a packet_id are useful to the "
                 f"remote agent, since it polls on packet_id. Expected ['a1'], got {ids!r}")

        # Idempotent.
        run([sys.executable, "factory/decision_push_back.py", "--decisions", str(decisions),
             "--department", "codex-macmini", "--dest", str(dest)], repo)
        if len([l for l in dest.read_text().splitlines() if l.strip()]) != 1:
            fail("pushing back twice duplicated rows; the remote agent would see the same answer repeatedly")

        # New decision arrives.
        with decisions.open("a", encoding="utf-8") as h:
            h.write(json.dumps({"packet_id": "a2", "department": "codex-macmini", "decision": "fix", "notes": "change X", "card_identifier": "ANK-4"}) + "\n")
        run([sys.executable, "factory/decision_push_back.py", "--decisions", str(decisions),
             "--department", "codex-macmini", "--dest", str(dest)], repo)
        got = [json.loads(l) for l in dest.read_text().splitlines() if l.strip()]
        if [g.get("packet_id") for g in got] != ["a1", "a2"]:
            fail(f"a new decision must arrive on the next push-back; got {[g.get('packet_id') for g in got]!r}")
        if not any(g.get("notes") for g in got):
            fail("a fix decision must carry its notes across, or the remote agent cannot act on the correction")

        # Fail closed, non-destructive.
        bad = run([sys.executable, "factory/decision_push_back.py", "--decisions", str(tmp / "nope.jsonl"),
                   "--department", "codex-macmini", "--dest", str(dest)], repo)
        if bad.returncode == 0:
            fail("a missing decisions file must exit nonzero, not silently succeed")
        if len([l for l in dest.read_text().splitlines() if l.strip()]) != 2:
            fail("a failed push-back must never truncate what the remote already has")


def check_suite(repo: Path) -> None:
    r = run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], repo, timeout=900)
    if r.returncode != 0:
        fail(f"the repo test suite failed (exit {r.returncode}):\n{(r.stdout or '')[-1000:]}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--task", required=True, choices=sorted(OWNED))
    p.add_argument("--repo", default=os.getcwd()); p.add_argument("--export", required=True); p.add_argument("--notes")
    a = p.parse_args(); repo = Path(a.repo).resolve()
    {"packet-id-flow": check_packet_id_flow, "push-back": check_push_back}[a.task](repo)
    check_suite(repo)
    owned = OWNED[a.task]
    guarded = ("factory/", "tests/", "kernel/", "runbooks/", "loopfactory.py")
    st = run(["git", "status", "--porcelain"], repo)
    stray = sorted(x for x in {l[3:].strip() for l in st.stdout.splitlines() if l[3:].strip()}
                   if x not in owned and x.startswith(guarded))
    if stray: fail(f"task '{a.task}' owns only {owned}, but git status also shows {stray}")
    if a.notes:
        src = repo / "notes.md"
        if not src.exists(): fail("./notes.md was not written")
        else:
            t = Path(a.notes); t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    run(["git", "add", "--"] + owned, repo)
    d = run(["git", "diff", "--cached"], repo)
    ex = Path(a.export).resolve(); ex.mkdir(parents=True, exist_ok=True)
    (ex / f"{a.task}.patch").write_text(d.stdout, encoding="utf-8")
    if not d.stdout.strip(): fail("the exported patch is empty")
    if FAILURES:
        print(f"\nCHECK FAILED for '{a.task}' — {len(FAILURES)} problem(s):\n")
        for i, m in enumerate(FAILURES, 1): print(f"{i}. {m}\n")
        return 1
    print(f"CHECK PASS for '{a.task}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
