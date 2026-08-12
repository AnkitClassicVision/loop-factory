"""Deterministically dispatch approved card actions, shadow-first.

The listener persists human decisions before this kernel runs.  This module
binds each execution receipt to the exact decision row so a replay is a no-op
while a later approval for revised content remains dispatchable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class DispatchInputError(ValueError):
    """Raised when dispatch inputs cannot be read safely."""


RunHandler = Callable[[list[str], str], Any]
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+)[^\s]+|((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s]+"
)


def _canonical(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _decision_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(row).encode("utf-8")).hexdigest()


def _load_jsonl(
    path: str | Path,
    label: str,
    *,
    may_be_absent: bool = False,
    malformed_is_fatal: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    source = Path(path)
    if may_be_absent and not source.exists():
        return [], 0
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DispatchInputError(f"{label} could not be read: {exc}") from exc
    rows: list[dict[str, Any]] = []
    malformed = 0
    nonblank = 0
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        nonblank += 1
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            if malformed_is_fatal:
                raise DispatchInputError(
                    f"{label} line {line_number} is invalid JSON: {exc}"
                ) from exc
            malformed += 1
            continue
        if not isinstance(value, dict):
            if malformed_is_fatal:
                raise DispatchInputError(f"{label} line {line_number} is not an object")
            malformed += 1
            continue
        rows.append(value)
    if nonblank and not rows:
        raise DispatchInputError(f"{label} has no parseable JSON object rows")
    return rows, malformed


def _registry(value: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        raise DispatchInputError("registry must be a JSON object")
    clean: dict[str, list[str]] = {}
    for kind, argv in value.items():
        if (
            not isinstance(kind, str)
            or not kind
            or isinstance(argv, (str, bytes))
            or not isinstance(argv, Sequence)
            or not argv
            or not all(isinstance(item, str) and item for item in argv)
        ):
            raise DispatchInputError("registry entries must map non-empty kinds to argv lists")
        clean[kind] = list(argv)
    return clean


def _default_run_handler(argv: list[str], payload_json: str) -> tuple[int, str, str]:
    result = subprocess.run(
        argv,
        input=payload_json,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    return result.returncode, result.stdout, result.stderr


def _handler_result(value: Any) -> tuple[int, str]:
    if isinstance(value, int) and not isinstance(value, bool):
        return value, ""
    if isinstance(value, tuple) and value:
        code = value[0]
        stderr = value[2] if len(value) > 2 else ""
        if isinstance(code, int) and not isinstance(code, bool):
            return code, stderr if isinstance(stderr, str) else ""
    code = getattr(value, "returncode", None)
    stderr = getattr(value, "stderr", "")
    if isinstance(code, int) and not isinstance(code, bool):
        return code, stderr if isinstance(stderr, str) else ""
    raise TypeError("run_handler must return an exit code, tuple, or CompletedProcess")


def _stderr_tail(value: str) -> str:
    redacted = _SECRET_RE.sub(lambda match: (match.group(1) or match.group(2)) + "[REDACTED]", value)
    return redacted[-200:]


def _age_seconds(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()))


def _matching_decision(
    decisions: list[dict[str, Any]], card_identifier: str, row_hash: str
) -> dict[str, Any] | None:
    for row in reversed(decisions):
        hashes = row.get("row_hashes")
        hash_matches = (
            row.get("row_hash") == row_hash
            or isinstance(hashes, list) and row_hash in hashes
        )
        if (
            row.get("card_identifier") == card_identifier
            and row.get("decision") == "approve"
            and hash_matches
        ):
            return row
    return None


def dispatch_pending(
    ledger_path: str | Path,
    decisions_path: str | Path,
    receipts_path: str | Path,
    registry: Mapping[str, Sequence[str]],
    *,
    apply: bool = False,
    run_handler: RunHandler | None = None,
) -> dict[str, Any]:
    """Report or execute the latest approved, unreceipted action per card."""
    ledger, ledger_malformed = _load_jsonl(ledger_path, "ledger")
    decisions, decisions_malformed = _load_jsonl(decisions_path, "decisions")
    receipts, receipts_malformed = _load_jsonl(
        receipts_path, "receipts", may_be_absent=True, malformed_is_fatal=True
    )
    handlers = _registry(registry)
    report: dict[str, Any] = {
        "scanned": len(ledger),
        "pending": [],
        "dispatched": 0,
        "failed": 0,
        "unhandled": 0,
        "skipped_receipted": 0,
        "mode": "apply" if apply else "shadow",
        "malformed": ledger_malformed + decisions_malformed + receipts_malformed,
    }

    latest_by_card: dict[str, dict[str, Any]] = {}
    source_by_hash: dict[str, dict[str, Any]] = {}
    for row in ledger:
        row_hash = row.get("row_hash")
        if isinstance(row_hash, str) and row_hash and row.get("packet_text") is not None:
            source_by_hash[row_hash] = row
        identifier = row.get("card_identifier")
        if isinstance(identifier, str) and identifier:
            latest_by_card[identifier] = row

    receipt_keys = {
        (row.get("card_identifier"), row.get("decision_hash")) for row in receipts
        if isinstance(row.get("card_identifier"), str)
        and isinstance(row.get("decision_hash"), str)
    }
    runner = run_handler or _default_run_handler
    for identifier, ledger_row in latest_by_card.items():
        status = ledger_row.get("status")
        row_hash = ledger_row.get("row_hash")
        if not isinstance(status, str) or not status.startswith("decided:approve"):
            continue
        if not isinstance(row_hash, str) or not row_hash:
            report["malformed"] += 1
            continue
        decision = _matching_decision(decisions, identifier, row_hash)
        if decision is None:
            report["malformed"] += 1
            continue
        digest = _decision_hash(decision)
        if (identifier, digest) in receipt_keys:
            report["skipped_receipted"] += 1
            continue
        kind = decision.get("kind") or ledger_row.get("kind")
        item: dict[str, Any] = {
            "card_identifier": identifier,
            "kind": kind if isinstance(kind, str) else "",
            "handler_argv": handlers.get(kind) if isinstance(kind, str) else None,
            "decision_age_seconds": _age_seconds(decision.get("ts")),
            "decision_hash": digest,
        }
        argv = handlers.get(kind) if isinstance(kind, str) else None
        if argv is None:
            item["unhandled"] = True
            report["unhandled"] += 1
            report["pending"].append(item)
            continue
        item["unhandled"] = False
        report["pending"].append(item)
        if not apply:
            continue
        payload = dict(decision)
        source_row = source_by_hash.get(row_hash)
        if source_row is not None:
            payload["approved_row"] = source_row
        try:
            exit_code, stderr = _handler_result(runner(list(argv), _canonical(payload)))
        except Exception as exc:
            report["failed"] += 1
            item["failure"] = {"exception_class": type(exc).__name__, "stderr_tail": ""}
            continue
        if exit_code != 0:
            report["failed"] += 1
            item["failure"] = {
                "exit_code": exit_code,
                "stderr_tail": _stderr_tail(stderr),
            }
            continue
        receipt = {
            "card_identifier": identifier,
            "decision_hash": digest,
            "handler_kind": kind,
            "ts": datetime.now(timezone.utc).isoformat(),
            "handler_exit": 0,
        }
        output = Path(receipts_path)
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt, sort_keys=True) + "\n")
        except OSError as exc:
            raise DispatchInputError(f"receipts could not be written: {exc}") from exc
        receipt_keys.add((identifier, digest))
        report["dispatched"] += 1
    return report


def _load_registry(path: str | Path) -> dict[str, list[str]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DispatchInputError(f"registry could not be read: {exc}") from exc
    return _registry(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dispatch approved card actions")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = dispatch_pending(
            args.ledger,
            args.decisions,
            args.receipts,
            _load_registry(args.registry),
            apply=args.apply,
        )
    except DispatchInputError as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "mode": "apply" if args.apply else "shadow"}))
        else:
            print(f"approve dispatch refused: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, sort_keys=True))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
