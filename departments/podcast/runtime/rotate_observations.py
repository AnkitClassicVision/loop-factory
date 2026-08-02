"""Conservatively compact and rotate podcast observation evidence."""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
OBSERVATIONS_FILE = "observations.jsonl"


def _duplicate_key(row: dict[str, Any]) -> str:
    values = [row.get(name) for name in ("sensor", "subject", "status", "detail")]
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _read_lines(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.readlines()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"unreadable input {path}: {exc}") from exc


def _analyze(lines: list[str], max_lines: int) -> tuple[list[str], list[str], dict[str, int | bool]]:
    parsed: list[dict[str, Any] | None] = []
    malformed = 0
    winners: dict[str, tuple[str, int]] = {}

    for index, line in enumerate(lines):
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("row is not an object")
        except (json.JSONDecodeError, ValueError, TypeError):
            parsed.append(None)
            malformed += 1
            continue
        parsed.append(value)
        key = _duplicate_key(value)
        timestamp = str(value.get("ts", ""))
        current = winners.get(key)
        if current is None or timestamp > current[0] or (timestamp == current[0] and index > current[1]):
            winners[key] = (timestamp, index)

    keep_indexes = {
        index
        for index, value in enumerate(parsed)
        if value is None or winners[_duplicate_key(value)][1] == index
    }
    compacted = [line for index, line in enumerate(lines) if index in keep_indexes]
    deduped = len(lines) - len(compacted)
    overflow = max(0, len(compacted) - max_lines)
    archived_lines = compacted[:overflow]
    kept_lines = compacted[overflow:]
    receipt: dict[str, int | bool] = {
        "kept": len(kept_lines),
        "deduped": deduped,
        "archived": len(archived_lines),
        "dry_run": False,
        "malformed": malformed,
    }
    return kept_lines, archived_lines, receipt


def _append_archive(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.writelines(lines)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_rewrite(path: Path, lines: list[str]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def rotate(state_dir: Path, max_lines: int, dry_run: bool = False) -> dict[str, int | bool]:
    if max_lines < 0:
        raise ValueError("--max-lines must be zero or greater")
    observations_path = state_dir / OBSERVATIONS_FILE
    lines = _read_lines(observations_path)
    kept_lines, archived_lines, receipt = _analyze(lines, max_lines)
    receipt["dry_run"] = dry_run
    if dry_run or (not archived_lines and receipt["deduped"] == 0):
        return receipt

    archive_path = state_dir / f"observations-archive-{datetime.now(timezone.utc):%Y%m%d}.jsonl"
    try:
        _append_archive(archive_path, archived_lines)
        _atomic_rewrite(observations_path, kept_lines)
    except OSError as exc:
        raise RuntimeError(f"failed to rotate {observations_path}: {exc}") from exc
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--max-lines", type=int, default=5000)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        receipt = rotate(args.state_dir, args.max_lines, args.dry_run)
    except (RuntimeError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 1
    print(json.dumps(receipt, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
