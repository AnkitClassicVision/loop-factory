"""Create shadow clarification asks for unreadable fixture notes."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def create_asks(root: Path) -> Path:
    root = Path(root)
    state = root / "state"
    intake = json.loads((state / "intake.json").read_text(encoding="utf-8"))
    asks_path = state / "asks.jsonl"
    rows = _load_rows(asks_path)
    existing_notes = {str(row["note"]) for row in rows}
    created = 0
    for note in intake.get("unreadable", []):
        if note in existing_notes:
            continue
        ask_id = hashlib.sha256(str(note).encode("utf-8")).hexdigest()[:16]
        rows.append({"ask_id": ask_id, "note": note, "ts": _now(), "status": "open"})
        draft = (
            "# Clarification requested\n\n"
            f"Reference: `{ask_id}`\n\n"
            "Please provide a readable plain-text replacement in the declared replies directory.\n"
            "This is a shadow draft and was not dispatched.\n"
        )
        _atomic_text(state / "outbox" / f"ask-{ask_id}.md", draft)
        existing_notes.add(str(note))
        created += 1
    body = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    _atomic_text(asks_path, body)
    LOGGER.info("created %d shadow ask drafts", created)
    return asks_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("departments/pulse"))
    args = parser.parse_args()
    create_asks(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
