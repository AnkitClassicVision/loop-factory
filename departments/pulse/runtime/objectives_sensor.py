"""Observe pulse objectives from local aggregate state only."""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def observe(root: Path, now: datetime | None = None) -> Path:
    root = Path(root)
    state = root / "state"
    now = now or datetime.now(timezone.utc)
    values: dict[str, int | float] = {}
    intake_path = state / "intake.json"
    if intake_path.exists():
        current_readable = json.loads(intake_path.read_text(encoding="utf-8")).get("readable", [])
        intake_ledger_path = state / "intake_ledger.json"
        history = json.loads(intake_ledger_path.read_text(encoding="utf-8")).get("notes", {}) if intake_ledger_path.exists() else {}
        readable = sorted(set(current_readable) | {name for name, entry in history.items() if entry.get("classification") == "readable"})
        if readable:
            ledger_path = state / "digest_ledger.json"
            notes = json.loads(ledger_path.read_text(encoding="utf-8")).get("notes", {}) if ledger_path.exists() else {}
            covered = 0
            for name in readable:
                entry = notes.get(name)
                if not isinstance(entry, dict) or not entry.get("digest"):
                    continue
                try:
                    elapsed = _parse(str(entry["digested_ts"])) - _parse(str(entry["intake_ts"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if 0 <= elapsed.total_seconds() <= 24 * 3600:
                    covered += 1
            values["digest_coverage"] = covered * 100 / len(readable)

    asks_path = state / "asks.jsonl"
    if asks_path.exists():
        rows = [json.loads(line) for line in asks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        overdue = 0
        for row in rows:
            if row.get("status") != "open":
                continue
            try:
                if (now - _parse(str(row["ts"]))).total_seconds() > 48 * 3600:
                    overdue += 1
            except (KeyError, TypeError, ValueError):
                continue
        values["ask_return_integrity"] = overdue

    receipt = state / "objectives_observed.json"
    _atomic_json(receipt, {"schema": "objectives-observed/v1", "ts": now.isoformat(), "values": values})
    LOGGER.info("observed %d objective values", len(values))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("departments/pulse"))
    args = parser.parse_args()
    observe(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
