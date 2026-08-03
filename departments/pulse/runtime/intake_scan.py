"""Classify fixture inbox notes without interpreting their content."""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)


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


def scan(root: Path) -> Path:
    root = Path(root)
    inbox = root / "inbox"
    readable: list[str] = []
    unreadable: list[str] = []
    for note in sorted(inbox.glob("*.txt")) if inbox.exists() else []:
        try:
            text = note.read_text(encoding="utf-8")
            target = readable if any(line.strip() for line in text.splitlines()) else unreadable
        except UnicodeDecodeError:
            target = unreadable
        target.append(note.name)
    receipt = root / "state" / "intake.json"
    _atomic_json(receipt, {"readable": readable, "unreadable": unreadable})
    history_path = root / "state" / "intake_ledger.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else {"notes": {}}
    notes = history.setdefault("notes", {})
    stamp = datetime.now(timezone.utc).isoformat()
    for name in readable:
        notes.setdefault(name, {"classification": "readable", "intake_ts": stamp})
    for name in unreadable:
        notes.setdefault(name, {"classification": "unreadable", "intake_ts": stamp})
    _atomic_json(history_path, history)
    LOGGER.info("classified %d readable and %d unreadable notes", len(readable), len(unreadable))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("departments/pulse"))
    args = parser.parse_args()
    scan(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
