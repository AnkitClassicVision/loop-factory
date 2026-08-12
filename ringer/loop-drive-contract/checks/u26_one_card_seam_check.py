#!/usr/bin/env python3
"""U26 executed seam check: ONE card, approved, EXECUTES — end to end, offline.

The fusion audits' unanimous verdict (2026-08-11): the system writes and
notifies but does not RESOLVE. This check executes the whole resolution seam
with the REAL scripts and the REAL W1a kernel, no mocks of the joints:

  identity_card.py         (create: proposal -> ledger row + YOUR MOVE card body
                            + AGENT UPDATE payload comment, ONE card only)
    -> human APPROVE       (fixture Linear-shaped comments file; agent-marked
                            decoy comments must never count as approval)
  identity_card_listen.py  (listen: grammar -> decision row -> decided ledger row
                            -> invokes factory/approve_dispatch.py --apply)
    -> approve_dispatch.py (kernel: replay-safe receipt)
    -> apply_identity_approve.py (handler: writes address + provenance into
                            CANDIDATE-INBOX.json, verified by re-read)

Then a REPLAY of the listener must not double-execute (skipped_receipted, no
second receipt, no duplicate write). Fictional data only. Everything runs in a
throwaway --root; the check never touches the real episodes/ state.

Usage: u26_one_card_seam_check.py --worktree <podcast-tree> [--owned ...]
                                  [--patch OUT] [--summary fix-summary.md]
                                  [--exported-summary OUT]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KERNEL = Path("/mnt/d_drive/repos/loop-factory/factory/approve_dispatch.py")
SUMMARY_SECTIONS = ("what changed", "how i verified", "risk")

PROPOSAL = {
    "alias": "cand-fic0001",
    "name": "Rosa Marchetti",
    "channel": "email",
    "value": "rosa@fictionalpractice.invalid",
    "evidence": "exact name match",
    "confidence": 55,
    "source": "hubspot",
}
DECOY_PROPOSAL = {
    "alias": "cand-fic0002",
    "name": "Theo Brandvold",
    "channel": "email",
    "value": "theo@alreadyknown.invalid",
    "evidence": "exact name match",
    "confidence": 90,
    "source": "hubspot",
}

FAILURES: list[str] = []


def fail(where: str, why: str) -> None:
    FAILURES.append(f"FAIL [{where}]: {why}")


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *args],
                          capture_output=True, text=True, timeout=120)


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                          timeout=180)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def build_root(root: Path, worktree: Path) -> None:
    receipts = root / "episodes/_loop_receipts"
    receipts.mkdir(parents=True)
    (root / "episodes/_loop_state").mkdir(parents=True)
    (receipts / "identity-proposals-20260811.json").write_text(json.dumps({
        "schema": "identity-proposals/v1",
        "generated_at": "2026-08-11T14:09:46+00:00",
        "considered": 2,
        "proposals": [PROPOSAL, DECOY_PROPOSAL],
        "unresolved": [],
    }, indent=1), encoding="utf-8")
    (root / "episodes/CANDIDATE-INBOX.json").write_text(json.dumps({
        "candidates": [
            {"name": "Rosa Marchetti", "email": None, "fit_score": 91,
             "confidence": 88, "source": "guest-acquisition-receipt",
             "note": "warm referral; fictional."},
            {"name": "Theo Brandvold", "email": "theo@alreadyknown.invalid",
             "fit_score": 95, "confidence": 90,
             "source": "guest-acquisition-receipt", "note": "fictional; already resolved."},
        ]
    }, indent=1), encoding="utf-8")
    (root / "episodes/_loop_state/approve-registry.json").write_text(json.dumps({
        "identity-approve": [sys.executable,
                             str(worktree / "scripts/apply_identity_approve.py"),
                             "--root", str(root)],
    }), encoding="utf-8")


def check(worktree: Path) -> None:
    for script in ("scripts/identity_card.py", "scripts/identity_card_listen.py",
                   "scripts/apply_identity_approve.py"):
        if not (worktree / script).is_file():
            fail("missing_script", f"{script} does not exist")
    if not KERNEL.is_file():
        fail("missing_kernel", f"{KERNEL} does not exist")
    if FAILURES:
        return

    tmp = Path(tempfile.mkdtemp(prefix="u26-root-"))
    try:
        _check_in_root(worktree, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _check_in_root(worktree: Path, root: Path) -> None:
    build_root(root, worktree)
    ledger = root / "episodes/_loop_state/approve-ledger.jsonl"
    decisions = root / "episodes/_loop_state/approve-decisions.jsonl"
    receipts = root / "episodes/_loop_state/approve-receipts.jsonl"

    # Step 1: create the card, offline.
    create = run([sys.executable, str(worktree / "scripts/identity_card.py"),
                  "--root", str(root), "--offline"], worktree)
    if create.returncode != 0:
        fail("create_failed",
             f"identity_card.py exit {create.returncode}: {create.stderr[-300:]}")
        return
    rows = load_jsonl(ledger)
    open_rows = [r for r in rows if r.get("status") == "open"]
    if len(open_rows) != 1:
        fail("create_ledger", f"expected exactly 1 open ledger row, got {len(open_rows)}")
        return
    row = open_rows[0]
    for field in ("card_identifier", "kind", "row_hash", "packet_text", "ts"):
        if not row.get(field):
            fail("create_ledger_fields", f"open ledger row missing {field}")
            return
    if row["kind"] != "identity-approve":
        fail("create_kind", f"kind is {row['kind']!r}, expected identity-approve")
    packet = json.loads(row["packet_text"])
    if packet.get("name") != "Rosa Marchetti":
        fail("create_selection",
             f"selected {packet.get('name')!r}; must pick the null-email candidate "
             "(Rosa Marchetti), never one already resolved")
    body = (root / "out/identity-card-body.md")
    if not body.is_file():
        fail("create_body_missing", "out/identity-card-body.md was not written")
        return
    body_text = body.read_text(encoding="utf-8")
    if not re.search(r"^## YOUR MOVE", body_text, re.MULTILINE):
        fail("create_your_move", "card body does not lead with a '## YOUR MOVE' block")
    for needle in ("APPROVE", "SKIP", "rosa@fictionalpractice.invalid", "Rosa Marchetti"):
        if needle not in body_text:
            fail("create_body_content", f"card body missing {needle!r}")
    comment = root / "out/identity-card-comment.md"
    if not comment.is_file():
        fail("create_comment_missing", "out/identity-card-comment.md was not written")
    else:
        first = comment.read_text(encoding="utf-8").splitlines()[0]
        if not first.startswith("AGENT UPDATE:"):
            fail("create_comment_marker",
                 f"payload comment first line {first!r} must start 'AGENT UPDATE:'")

    # Step 1b: idempotence — a second create must not open a second card.
    create2 = run([sys.executable, str(worktree / "scripts/identity_card.py"),
                   "--root", str(root), "--offline"], worktree)
    open_after = [r for r in load_jsonl(ledger) if r.get("status") == "open"]
    if len(open_after) != 1:
        fail("create_not_idempotent",
             f"second create call left {len(open_after)} open rows (exit "
             f"{create2.returncode}); ONE card only while one is open")

    # Step 2: fixture comments — an agent decoy that must NOT count, then the human.
    # The approve comment uses the REAL owner-tap shape proven live on
    # ANK-348 (tg_approval.py): a qualified first line plus nonce metadata —
    # not the polite bare "APPROVE" a kind fixture would invent.
    comments = root / "comments.json"
    comments.write_text(json.dumps([
        {"body": "AGENT UPDATE: payload attached for review.",
         "user": {"name": "cli-claude-code-linux", "displayName": "agent"}},
        {"body": "APPROVE external_send\nowner Telegram tap "
                 "2026-08-11T23:05:12+00:00 nonce fictional-0001",
         "user": {"name": "ankit114", "displayName": "ankit114"}},
    ]), encoding="utf-8")

    listen = run([sys.executable, str(worktree / "scripts/identity_card_listen.py"),
                  "--root", str(root), "--offline", "--comments-file", str(comments)],
                 worktree)
    if listen.returncode != 0:
        fail("listen_failed",
             f"identity_card_listen.py exit {listen.returncode}: {listen.stderr[-300:]}")
        return
    decision_rows = load_jsonl(decisions)
    approvals = [r for r in decision_rows if r.get("decision") == "approve"]
    if len(approvals) != 1:
        fail("listen_decision", f"expected 1 approve decision row, got {len(approvals)}")
        return
    if approvals[0].get("row_hash") != row["row_hash"]:
        fail("listen_hash", "decision row_hash does not match the open ledger row")
    decided = [r for r in load_jsonl(ledger) if str(r.get("status", "")).startswith("decided:approve")]
    if len(decided) != 1:
        fail("listen_ledger", f"expected 1 decided:approve ledger row, got {len(decided)}")
    receipt_rows = load_jsonl(receipts)
    if len(receipt_rows) != 1:
        fail("kernel_receipt", f"expected exactly 1 kernel receipt, got {len(receipt_rows)}"
             " — the approve did not EXECUTE, which is the defect this slice exists to kill")
        return
    if receipt_rows[0].get("handler_kind") != "identity-approve" or receipt_rows[0].get("handler_exit") != 0:
        fail("kernel_receipt_fields", f"receipt malformed: {receipt_rows[0]}")

    inbox = json.loads((root / "episodes/CANDIDATE-INBOX.json").read_text(encoding="utf-8"))
    rosa = next((c for c in inbox["candidates"] if c["name"] == "Rosa Marchetti"), None)
    if rosa is None or rosa.get("email") != "rosa@fictionalpractice.invalid":
        fail("handler_write", f"Rosa's email not written: {rosa}")
    else:
        prov_blob = json.dumps(rosa)
        for needle in ("hubspot", row["card_identifier"]):
            if needle not in prov_blob:
                fail("handler_provenance", f"provenance missing {needle!r} in {rosa}")
    theo = next((c for c in inbox["candidates"] if c["name"] == "Theo Brandvold"), None)
    if theo is None or theo.get("email") != "theo@alreadyknown.invalid":
        fail("handler_scope", f"untouched candidate was modified: {theo}")
    close = root / "out/identity-card-close.md"
    if not close.is_file():
        fail("close_missing", "out/identity-card-close.md was not written — the card "
             "would stay open forever after executing")
    else:
        first = close.read_text(encoding="utf-8").splitlines()[0]
        if not first.startswith("AGENT DONE:"):
            fail("close_marker", f"close comment first line {first!r} must start 'AGENT DONE:'")

    # Step 3: replay — a second listen must not double-execute or double-write.
    replay = run([sys.executable, str(worktree / "scripts/identity_card_listen.py"),
                  "--root", str(root), "--offline", "--comments-file", str(comments)],
                 worktree)
    if replay.returncode != 0:
        fail("replay_failed",
             f"replay exit {replay.returncode}: {replay.stderr[-300:]}")
        return
    if len(load_jsonl(receipts)) != 1:
        fail("replay_receipt", "replay produced a second kernel receipt; dispatch must "
             "be replay-safe by decision-row hash")
    inbox2 = json.loads((root / "episodes/CANDIDATE-INBOX.json").read_text(encoding="utf-8"))
    if inbox2 != inbox:
        fail("replay_write", "replay changed CANDIDATE-INBOX.json again")


def check_agent_only(worktree: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="u26-agent-"))
    try:
        build_root(tmp, worktree)
        run([sys.executable, str(worktree / "scripts/identity_card.py"),
             "--root", str(tmp), "--offline"], worktree)
        comments = tmp / "comments.json"
        comments.write_text(json.dumps([
            {"body": "AGENT UPDATE: looks good, APPROVE recommended.",
             "user": {"name": "cli-claude-code-linux", "displayName": "agent"}},
        ]), encoding="utf-8")
        run([sys.executable, str(worktree / "scripts/identity_card_listen.py"),
             "--root", str(tmp), "--offline", "--comments-file", str(comments)],
            worktree)
        if load_jsonl(tmp / "episodes/_loop_state/approve-receipts.jsonl"):
            fail("agent_comment_approved",
                 "an agent-authored comment produced a kernel receipt; approval "
                 "grammar is HUMAN-ONLY")
        inbox = json.loads((tmp / "episodes/CANDIDATE-INBOX.json").read_text(encoding="utf-8"))
        rosa = next(c for c in inbox["candidates"] if c["name"] == "Rosa Marchetti")
        if rosa.get("email"):
            fail("agent_comment_wrote", "an agent-only comment stream wrote an address")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", default=".", type=Path)
    parser.add_argument("--owned", action="append", default=[])
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--summary", type=Path, default=Path("fix-summary.md"))
    parser.add_argument("--exported-summary", type=Path)
    args = parser.parse_args()

    worktree = args.worktree.resolve()
    check(worktree)
    if not FAILURES:
        check_agent_only(worktree)

    if args.exported_summary:
        if not args.summary.is_file():
            fail("summary_missing", f"{args.summary} was not written")
        else:
            body = args.summary.read_text(encoding="utf-8").lower()
            missing = [s for s in SUMMARY_SECTIONS if s not in body]
            if missing:
                fail("summary_sections", f"fix-summary.md is missing {', '.join(missing)}")
            else:
                args.exported_summary.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.summary, args.exported_summary)

    if args.patch and args.owned:
        add = git(worktree, "add", "--", *args.owned)
        if add.returncode != 0:
            fail("git_add_failed", add.stderr.strip())
        for line in git(worktree, "status", "--porcelain").stdout.splitlines():
            code, path = line[:2], line[3:].strip('"')
            if code != "??" and path not in args.owned:
                fail("outside_owned_files", f"{path} changed; this task owns {args.owned}")
        diff = git(worktree, "diff", "--cached", "--binary", "--", *args.owned)
        if not diff.stdout.strip():
            fail("empty_patch", "nothing staged; no owned file was edited")
        else:
            args.patch.parent.mkdir(parents=True, exist_ok=True)
            args.patch.write_text(diff.stdout, encoding="utf-8")

    if FAILURES:
        print("u26_one_card_seam_check: FAIL")
        for line in FAILURES:
            print(f"  {line}")
        return 1
    print("u26_one_card_seam_check: PASS — one card created (idempotent), a human "
          "APPROVE executed through the kernel exactly once (replay-safe), the address "
          "landed in CANDIDATE-INBOX.json with provenance, the close comment exists, "
          "and agent-authored comments can never approve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
