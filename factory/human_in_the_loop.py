"""Factory-standard human-in-the-loop for every department.

Moves any department's pending approval queue into the Hermes bot outbox so a
person can approve or reject from a phone, then applies the reply to the queue.
This bridge is SHADOW-safe and never sends a department message.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _save(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(row) for row in rows)
    path.write_text(content + ("\n" if rows else ""), encoding="utf-8")


def push(queue_path, department: str, outbox_path) -> dict:
    """Append decision packets for unpushed pending rows and update the queue."""
    queue = Path(queue_path)
    outbox = Path(outbox_path)
    rows = _load(queue)
    packets = []

    for row in rows:
        if row.get("status") != "pending_approval" or row.get("decision_id"):
            continue
        contact_id = row.get("contact_id")
        queued_at = row.get("queued_at", "")
        decision_id = f"{department}-approve-{contact_id}-{queued_at[:19]}"
        packet = {
            "decision_id": decision_id,
            "department": department,
            "packet": {
                "eli5": f"Approve {department} message to {contact_id}?",
                "draft": row["draft"],
                "options": ["APPROVE", "REJECT"],
            },
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        packets.append(packet)
        row["decision_id"] = decision_id

    if packets:
        outbox.parent.mkdir(parents=True, exist_ok=True)
        with outbox.open("a", encoding="utf-8") as handle:
            for packet in packets:
                handle.write(json.dumps(packet) + "\n")
    _save(queue, rows)
    return {"pushed": len(packets), "outbox": str(outbox)}


def apply(queue_path, decision_id: str, verdict: str, on_approved=None) -> dict:
    """Apply a Hermes approval verdict to its matching department queue row.

    When a row is APPROVED and ``on_approved`` is supplied, the hook is called
    with the queue path after the approval is persisted. This is the factory
    seam that lets a department act on an approval (e.g. run its send
    connector) without coupling this generic bridge to any department. The hook
    fires only on approval, only when provided, and never on reject or no-match.

    Hardened per Codex review P0 #1: the verdict must be EXACTLY "APPROVE" or
    "REJECT" (case-insensitive, whole token; "APPROVE_ALL" is not an approval);
    only a row still in pending_approval can transition (a replayed decision_id
    is a no-op); and the hook outcome is recorded on the row: a failed hook
    leaves status "approved_hook_failed", never a silent success.
    """
    queue = Path(queue_path)
    word = verdict.strip().upper()
    if word not in {"APPROVE", "REJECT"}:
        return {"applied": False, "error": f"unknown verdict {verdict!r}"}
    approved = word == "APPROVE"
    rows = _load(queue)
    target = None
    for row in rows:
        if row.get("decision_id") == decision_id:
            if row.get("status") != "pending_approval":
                return {"applied": False, "error": "not pending (replay or already decided)"}
            target = row
            break
    if target is None:
        return {"applied": False}
    target["status"] = "approved" if approved else "rejected"
    _save(queue, rows)  # the decision is durable before any side effect
    if approved and on_approved is not None:
        try:
            on_approved(str(queue))
            target["hook"] = "ok"
        except Exception as exc:  # visible, never silent
            target["status"] = "approved_hook_failed"
            target["hook"] = f"failed: {exc}"
        _save(queue, rows)
    return {"applied": True, "status": target["status"]}


def _run_cmd_hook(command: str):
    """Return an on_approved hook that runs a shell command after approval.

    The literal token ``{queue}`` in the command is replaced with the queue
    path. Used by the CLI so a department can pass its own send connector as a
    command (e.g. the sales approve->send runner) with no import coupling.
    """
    import shlex
    import subprocess

    def _hook(queue: str) -> None:
        rendered = command.replace("{queue}", queue)
        subprocess.run(shlex.split(rendered), check=False)

    return _hook


def escalate(department: str, issue: str, outbox_path, context: dict | None = None) -> dict:
    """Escalate an issue the department could NOT self-heal to the Hermes bot.

    Writes an escalation packet to the same outbox the bot watches, so any
    unhealable error reaches Ankit on his phone. Distinct kind='escalation' so
    the bot presents it as a heads-up, not an approve/reject. Never sends."""
    outbox = Path(outbox_path)
    outbox.parent.mkdir(parents=True, exist_ok=True)
    packet = {
        "kind": "escalation",
        "department": department,
        "issue": issue,
        "context": context or {},
        "ts": datetime.now(timezone.utc).isoformat(),
        "eli5": f"[{department}] needs you: {issue}",
    }
    with outbox.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(packet) + "\n")
    return {"escalated": True, "issue": issue}


def main() -> None:
    parser = argparse.ArgumentParser(description="Factory approval queue <-> Hermes bridge (never sends)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    push_parser = subparsers.add_parser("push")
    push_parser.add_argument("--queue", required=True)
    push_parser.add_argument("--department", required=True)
    push_parser.add_argument("--outbox", required=True)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--queue", required=True)
    apply_parser.add_argument("--decision-id", required=True)
    apply_parser.add_argument("--verdict", required=True)
    apply_parser.add_argument(
        "--on-approve-cmd",
        default=None,
        help="shell command run only on APPROVE; '{queue}' is replaced with the queue path",
    )

    esc_parser = subparsers.add_parser("escalate")
    esc_parser.add_argument("--department", required=True)
    esc_parser.add_argument("--issue", required=True)
    esc_parser.add_argument("--outbox", required=True)

    args = parser.parse_args()
    if args.command == "push":
        result = push(args.queue, args.department, args.outbox)
    elif args.command == "escalate":
        result = escalate(args.department, args.issue, args.outbox)
    else:
        hook = _run_cmd_hook(args.on_approve_cmd) if args.on_approve_cmd else None
        result = apply(args.queue, args.decision_id, args.verdict, on_approved=hook)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
