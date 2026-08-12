"""Factory-standard human-in-the-loop for every department.

Moves any department's pending approval queue into the Hermes bot outbox so a
person can approve or reject from a phone, then applies the reply to the queue.
This bridge is SHADOW-safe and never sends a department message.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from factory.escalation_contract import (
    EscalationContractError,
    OPEN,
    RE_ESCALATED,
    RESOLVED,
    open_escalation,
    re_escalate,
    resolve as resolve_contract,
    validate as validate_escalation,
)


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _locked_write(path: Path, build_content) -> None:
    with _exclusive_lock(path):
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        _atomic_write(path, build_content(existing))


def _save(path: Path, rows: list[dict]) -> None:
    content = "\n".join(json.dumps(row) for row in rows)
    _locked_write(path, lambda _existing: content + ("\n" if rows else ""))


def _serialized_rows(rows: list[dict]) -> str:
    content = "\n".join(json.dumps(row) for row in rows)
    return content + ("\n" if rows else "")


def _append(path: Path, rows: list[dict]) -> None:
    with _exclusive_lock(path):
        _append_locked(path, rows)


def _append_locked(path: Path, rows: list[dict]) -> None:
    """Append rows while the caller already owns ``path``'s exclusive lock."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    suffix = "".join(json.dumps(row) + "\n" for row in rows)
    _atomic_write(path, existing + suffix)


def _escalation_ledger(outbox: Path, ledger_path: str | Path | None) -> Path:
    return Path(ledger_path) if ledger_path is not None else outbox.with_name("escalations.jsonl")


def _latest_escalations(ledger: Path, *, receipt_root: Path | None = None) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in _load(ledger):
        if not isinstance(row, dict):
            raise EscalationContractError("escalation ledger row must be a mapping")
        root = receipt_root if row.get("status") == RESOLVED else None
        validated = validate_escalation(row, receipt_root=root)
        latest[validated["id"]] = validated
    return latest


def _append_reescalation_packet(outbox: Path, escalation: dict) -> None:
    _append(outbox, [{
        "kind": "re_escalation",
        "schema": "human-outbox-escalation/v1",
        "escalation": escalation,
        "department": escalation["department"],
        "issue": escalation["finding"],
        "ts": escalation["raised_at"],
        "eli5": f"[{escalation['department']}] still needs you: {escalation['finding']}",
    }])


def resolve_escalation(
    ledger_path: str | Path,
    escalation_id: str,
    *,
    owner: str,
    decided_at: str,
    action: str,
    receipt_path: str | Path,
    receipt_root: str | Path,
) -> dict:
    ledger = Path(ledger_path)
    root = Path(receipt_root)
    with _exclusive_lock(ledger):
        latest = _latest_escalations(ledger, receipt_root=root)
        current = latest.get(escalation_id)
        if current is None:
            return {"resolved": False, "reason": "escalation_not_found"}
        if current["status"] == RESOLVED:
            return {"resolved": False, "reason": "escalation_already_resolved"}
        receipt_file = Path(receipt_path)
        receipt_file = receipt_file.resolve() if receipt_file.is_absolute() else (root / receipt_file).resolve()
        try:
            relative_receipt = receipt_file.relative_to(root.resolve()).as_posix()
        except ValueError:
            return {"resolved": False, "reason": "receipt_outside_root"}
        import hashlib
        try:
            digest = hashlib.sha256(receipt_file.read_bytes()).hexdigest()
        except OSError:
            return {"resolved": False, "reason": "receipt_unreadable"}
        try:
            resolved = resolve_contract(
                current,
                owner=owner,
                decided_at=decided_at,
                action=action,
                receipt={"schema": "file-sha256/v1", "path": relative_receipt, "sha256": digest},
                receipt_root=root,
            )
        except EscalationContractError as exc:
            return {"resolved": False, "reason": str(exc)}
        _append_locked(ledger, [resolved])
    return {"resolved": True, "escalation_id": resolved["id"], "status": resolved["status"]}


def reconcile_escalations(
    ledger_path: str | Path,
    *,
    now: str | None = None,
    outbox_path: str | Path | None = None,
    receipt_root: str | Path | None = None,
) -> dict:
    ledger = Path(ledger_path)
    root = Path(receipt_root) if receipt_root is not None else ledger.parent
    current_time = datetime.fromisoformat((now or datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
    latest = _latest_escalations(ledger, receipt_root=root)
    reissued = []
    for escalation in latest.values():
        if escalation["status"] not in {OPEN, RE_ESCALATED}:
            continue
        deadline = datetime.fromisoformat(escalation["deadline"].replace("Z", "+00:00"))
        if deadline >= current_time:
            continue
        next_row = re_escalate(escalation, at=current_time.isoformat())
        reissued.append(next_row)
    if reissued:
        _append(ledger, reissued)
        if outbox_path is not None:
            outbox = Path(outbox_path)
            for escalation in reissued:
                _append_reescalation_packet(outbox, escalation)
    return {"re_escalated": len(reissued), "ledger": str(ledger)}


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
        _append(outbox, packets)
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

    # The queue must be read and validated under the same fence as the
    # transition write. Otherwise two responders can both observe pending and
    # both claim the same decision.
    with _exclusive_lock(queue):
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
        _atomic_write(queue, _serialized_rows(rows))  # durable before any side effect

    if approved and on_approved is not None:
        try:
            on_approved(str(queue))
            hook_update = {"hook": "ok", "hook_exit_code": 0}
        except Exception as exc:  # visible, never silent
            hook_update = {
                "status": "approved_hook_failed",
                "hook": f"failed: {exc}",
            }
            return_code = getattr(exc, "returncode", None)
            if return_code is not None:
                hook_update["hook_exit_code"] = return_code
        with _exclusive_lock(queue):
            rows = _load(queue)
            target = next(
                (row for row in rows if row.get("decision_id") == decision_id),
                None,
            )
            if target is not None:
                target.update(hook_update)
                _atomic_write(queue, _serialized_rows(rows))
                return {"applied": True, "status": target.get("status")}
        return {"applied": True, "status": "approved_hook_failed"}
    return {"applied": True, "status": "approved" if approved else "rejected"}


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
        result = subprocess.run(shlex.split(rendered), check=False)
        returncode = getattr(result, "returncode", None)
        if not isinstance(returncode, int) or isinstance(returncode, bool) or returncode != 0:
            error = RuntimeError(
                f"approval hook exited with nonzero status {returncode!r}"
            )
            error.returncode = returncode
            raise error

    return _hook


def escalate(
    department: str,
    issue: str,
    outbox_path,
    context: dict | None = None,
    *,
    meaning: str | None = None,
    needs: str | None = None,
    actions: list[dict] | None = None,
    fyi_only: bool = False,
    owner: str | None = None,
    deadline: str | None = None,
    next_action: str | None = None,
    ledger_path: str | Path | None = None,
) -> dict:
    """Escalate an issue the department could NOT self-heal to the Hermes bot.

    Writes an escalation packet to the same outbox the bot watches, so any
    unhealable error reaches Ankit on his phone. Distinct kind='escalation' so
    the bot presents it as a heads-up, not an approve/reject. Never sends."""
    outbox = Path(outbox_path)
    raised_at = datetime.now(timezone.utc).isoformat()
    try:
        escalation = open_escalation(
            department=department,
            finding=issue,
            owner=owner,
            deadline=deadline,
            next_action=next_action,
            raised_at=raised_at,
            evidence=context or {"issue": issue},
            fyi_only=fyi_only,
        )
    except EscalationContractError as exc:
        return {"escalated": False, "blocked": True, "reason": str(exc)}
    packet = {
        "kind": "escalation",
        "schema": "human-outbox-escalation/v1",
        "escalation": escalation,
        "department": department,
        "issue": issue,
        "context": context or {},
        "ts": raised_at,
        "eli5": f"[{department}] needs you: {issue}",
    }
    if meaning is None and needs is None and actions is None and not fyi_only:
        packet["card_gap"] = True
        packet["eli5"] = f"[unclear card] {packet['eli5']}"
    else:
        card = {
            "what_it_means": meaning,
            "what_it_needs": needs,
        }
        if fyi_only:
            card["fyi_only"] = True
        else:
            card["approvable_actions"] = actions or []
        packet["card"] = card
    ledger = _escalation_ledger(outbox, ledger_path)
    _append(ledger, [escalation])
    _append(outbox, [packet])
    return {
        "escalated": True,
        "issue": issue,
        "escalation_id": escalation["id"],
        "status": escalation["status"],
        "ledger": str(ledger),
    }


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
    esc_parser.add_argument("--owner", required=True)
    esc_parser.add_argument("--deadline", required=True)
    esc_parser.add_argument("--next-action", required=True)
    esc_parser.add_argument("--ledger")

    resolve_parser = subparsers.add_parser("resolve-escalation")
    resolve_parser.add_argument("--ledger", required=True)
    resolve_parser.add_argument("--escalation-id", required=True)
    resolve_parser.add_argument("--owner", required=True)
    resolve_parser.add_argument("--decided-at", required=True)
    resolve_parser.add_argument("--action", required=True)
    resolve_parser.add_argument("--receipt", required=True)
    resolve_parser.add_argument("--receipt-root", required=True)

    reconcile_parser = subparsers.add_parser("reconcile-escalations")
    reconcile_parser.add_argument("--ledger", required=True)
    reconcile_parser.add_argument("--outbox")
    reconcile_parser.add_argument("--now")

    args = parser.parse_args()
    if args.command == "push":
        result = push(args.queue, args.department, args.outbox)
    elif args.command == "escalate":
        result = escalate(
            args.department,
            args.issue,
            args.outbox,
            owner=args.owner,
            deadline=args.deadline,
            next_action=args.next_action,
            ledger_path=args.ledger,
        )
    elif args.command == "resolve-escalation":
        result = resolve_escalation(
            args.ledger,
            args.escalation_id,
            owner=args.owner,
            decided_at=args.decided_at,
            action=args.action,
            receipt_path=args.receipt,
            receipt_root=args.receipt_root,
        )
    elif args.command == "reconcile-escalations":
        result = reconcile_escalations(args.ledger, now=args.now, outbox_path=args.outbox)
    else:
        hook = _run_cmd_hook(args.on_approve_cmd) if args.on_approve_cmd else None
        result = apply(args.queue, args.decision_id, args.verdict, on_approved=hook)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
