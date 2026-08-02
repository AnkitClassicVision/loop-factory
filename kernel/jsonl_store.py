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


def trusted_root_for(path: str | Path) -> Path:
    """Return the nearest lexical ``state`` root, or the file's parent."""
    path = Path(path).absolute()
    for parent in path.parents:
        if parent.name == "state":
            return parent
    return path.parent


def open_no_follow(
    path: str | Path,
    flags: int,
    mode: int = 0o600,
    *,
    trusted_root: str | Path | None = None,
) -> int:
    """Open ``path`` without following symlinks beneath a trusted root.

    The trusted root itself is opened normally so a symlinked mount or parent
    remains usable. Every descendant directory and the target file use
    ``O_NOFOLLOW``.
    """
    path = Path(path).absolute()
    root = Path(trusted_root or trusted_root_for(path)).absolute()
    relative = path.relative_to(root)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(
            relative.parts[-1], flags | os.O_NOFOLLOW, mode, dir_fd=directory_fd
        )
    finally:
        os.close(directory_fd)


def ensure_parent_dirs(
    path: str | Path, *, trusted_root: str | Path | None = None
) -> Path:
    """Create parent directories without traversing descendant symlinks."""
    path = Path(path).absolute()
    root = Path(trusted_root or trusted_root_for(path)).absolute()
    root.mkdir(parents=True, exist_ok=True)
    relative_parent = path.parent.relative_to(root)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in relative_parent.parts:
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory_fd)
            except FileExistsError:
                pass
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
    finally:
        os.close(directory_fd)
    return root


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def append_jsonl(
    path: str | Path,
    row: dict[str, Any],
    *,
    trusted_root: str | Path | None = None,
) -> Path:
    """Append one canonical JSON line under an exclusive advisory lock."""
    path = Path(path).absolute()
    root = ensure_parent_dirs(path, trusted_root=trusted_root)
    line = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    with _thread_lock(path):
        fd = open_no_follow(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
            trusted_root=root,
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
