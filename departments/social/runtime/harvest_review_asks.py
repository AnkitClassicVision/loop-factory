#!/usr/bin/env python3
"""Harvest Linear review returns and surface overdue asks to the manager outbox."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import human_in_the_loop


DECISIONS = {"APPROVE": "approved", "SKIP": "skipped", "FIX": "fix_requested"}


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _save_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _decision(comments: list[dict]) -> str | None:
    for comment in reversed(comments):
        body = str(comment.get("body", "")).strip().upper()
        first = body.split(maxsplit=1)[0] if body else ""
        if first in DECISIONS:
            return DECISIONS[first]
    return None


def harvest(
    ledger: Path,
    outbox: Path,
    receipt: Path,
    *,
    reader: Path,
    fixture: Path | None = None,
    return_sla_hours: float = 48,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = _load_jsonl(ledger)
    checked = returned = breaches = 0
    for row in rows:
        if row.get("status") != "open":
            continue
        identifier = row.get("card_identifier")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("open ask is missing card_identifier")
        checked += 1
        command = [sys.executable, str(reader), "--issue", identifier, "--shadow"]
        if fixture:
            command.extend(["--fixture", str(fixture)])
        result = subprocess.run(command, check=True, text=True, capture_output=True)
        comments = json.loads(result.stdout)
        verdict = _decision(comments)
        if verdict:
            row["status"] = verdict
            row["returned_at"] = now.isoformat()
            returned += 1
            continue
        created = _parse_time(str(row.get("ts", "")))
        age_hours = (now - created).total_seconds() / 3600
        if age_hours > return_sla_hours and not row.get("sla_breached_at"):
            row["sla_breached_at"] = now.isoformat()
            row["sla_hours"] = return_sla_hours
            escalation = human_in_the_loop.escalate(
                "social",
                f"review ask {identifier} exceeded {return_sla_hours:g}h return SLA",
                outbox,
                context={"card_identifier": identifier, "age_hours": round(age_hours, 2)},
                owner="ankit",
                deadline=(now + timedelta(hours=return_sla_hours)).isoformat(),
                next_action="Review the overdue card and record an approve, decline, or hold decision",
            )
            if not escalation.get("escalated"):
                raise RuntimeError("strict social escalation was blocked")
            breaches += 1
    _save_jsonl(ledger, rows)
    result = {
        "status": "complete",
        "checked": checked,
        "returns_recorded": returned,
        "sla_breaches": breaches,
        "return_sla_hours": return_sla_hours,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--outbox", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reader", type=Path, required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--return-sla-hours", type=float, default=48)
    args = parser.parse_args(argv)
    harvest(
        args.ledger, args.outbox, args.out, reader=args.reader,
        fixture=args.fixture, return_sla_hours=args.return_sla_hours,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
