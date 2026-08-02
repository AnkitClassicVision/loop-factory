"""Concurrent-safe append primitives for local JSONL source records."""
from __future__ import annotations

import fcntl
import json
import os
import threading
from pathlib import Path
from typing import Any


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def open_no_follow(path: str | Path, flags: int, mode: int = 0o600) -> int:
    """Open an absolute path without following any symlink component."""
    path = Path(path).absolute()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open("/", directory_flags)
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(
            path.name, flags | os.O_NOFOLLOW, mode, dir_fd=directory_fd
        )
    finally:
        os.close(directory_fd)


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def append_jsonl(path: str | Path, row: dict[str, Any]) -> Path:
    """Append one canonical JSON line under an exclusive advisory lock."""
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    with _thread_lock(path):
        fd = open_no_follow(
            path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600
        )
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return path
