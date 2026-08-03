"""Build an idempotent daily digest from readable notes and ask replies."""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _atomic_json(path: Path, value: object) -> None:
    _atomic_text(path, json.dumps(value, sort_keys=True, indent=2) + "\n")


def _rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_digest(root: Path) -> Path:
    root = Path(root)
    state = root / "state"
    now = _now()
    digest_name = f"digest-{now.date().isoformat()}.md"
    digest_path = state / digest_name
    intake = json.loads((state / "intake.json").read_text(encoding="utf-8"))
    ledger_path = state / "digest_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {"notes": {}, "replies": {}}
    ledger.setdefault("notes", {})
    ledger.setdefault("replies", {})
    intake_history_path = state / "intake_ledger.json"
    intake_history = json.loads(intake_history_path.read_text(encoding="utf-8")).get("notes", {}) if intake_history_path.exists() else {}
    additions: list[str] = []

    for name in intake.get("readable", []):
        if name in ledger["notes"]:
            continue
        text = (root / "inbox" / name).read_text(encoding="utf-8").strip()
        stamp = str(intake_history.get(name, {}).get("intake_ts", now.isoformat()))
        ledger["notes"][name] = {
            "digest": digest_name,
            "intake_ts": stamp,
            "digested_ts": now.isoformat(),
        }
        additions.append(f"## Note: {name}\n\n{text}\n")

    asks_path = state / "asks.jsonl"
    asks = _rows(asks_path)
    changed_asks = False
    for ask in asks:
        ask_id = str(ask.get("ask_id", ""))
        reply = root / "replies" / f"{ask_id}.txt"
        if ask.get("status") != "open" or not reply.is_file():
            continue
        reply_text = reply.read_text(encoding="utf-8").strip()
        ask["status"] = "harvested"
        ask["harvested_ts"] = now.isoformat()
        changed_asks = True
        if ask_id not in ledger["replies"]:
            ledger["replies"][ask_id] = {"digest": digest_name, "harvested_ts": now.isoformat()}
            additions.append(f"## Clarification: {ask_id}\n\n{reply_text}\n")

    if changed_asks:
        _atomic_text(asks_path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in asks))
    existing = digest_path.read_text(encoding="utf-8") if digest_path.exists() else f"# Pulse digest draft: {now.date().isoformat()}\n\n"
    if additions:
        existing += "\n".join(additions)
    _atomic_text(digest_path, existing)
    _atomic_json(ledger_path, ledger)
    LOGGER.info("added %d items to %s", len(additions), digest_name)
    return digest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("departments/pulse"))
    args = parser.parse_args()
    build_digest(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
