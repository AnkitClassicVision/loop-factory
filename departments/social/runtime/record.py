"""Append social node receipts and advance state in fenced durable order."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "social" / "state"


class EpochError(RuntimeError):
    """A writer attempted to reuse or skip a state epoch."""


class RecordsLockTimeout(RuntimeError):
    """The department records lock was not acquired in time."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def records_lock(
    state_dir: str | Path, *, timeout: float = 3.0
) -> Iterator[None]:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / ".records.lock"
    deadline = time.monotonic() + max(0.0, float(timeout))
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise RecordsLockTimeout(
                        f"timed out acquiring records lock: {lock_path}"
                    ) from exc
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EpochError(f"cannot read existing state: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EpochError(f"existing state is not an object: {path}")
    return value


def _current_epoch(path: Path) -> int:
    state = _read_state(path)
    epoch = state.get("epoch", -1)
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise EpochError(f"existing state epoch is invalid: {path}")
    return epoch


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def write_record(
    state_dir: str | Path,
    node: str,
    payload_summary: Any,
    *,
    intended_epoch: int | None = None,
    shadow: bool = True,
    now: str | None = None,
    lock_timeout: float = 3.0,
) -> dict[str, Any]:
    """Write runs.jsonl, then STATE.json, then heartbeats.jsonl under one lock."""
    state_dir = Path(state_dir)
    with records_lock(state_dir, timeout=lock_timeout):
        state_path = state_dir / "STATE.json"
        previous = _current_epoch(state_path)
        epoch = previous + 1 if intended_epoch is None else intended_epoch
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise EpochError("intended epoch must be an integer")
        if epoch != previous + 1:
            relation = "already reached" if previous >= epoch else "would skip"
            raise EpochError(
                f"refusing epoch {epoch}: on-disk epoch {previous} {relation} "
                "the intended sequence"
            )

        timestamp = now or utc_now()
        receipt = {
            "node": str(node),
            "epoch": epoch,
            "timestamp": timestamp,
            "shadow": bool(shadow),
            "payload_summary": payload_summary,
        }

        _append_jsonl(state_dir / "runs.jsonl", receipt)

        prior_state = _read_state(state_path)
        next_state = dict(prior_state)
        next_state.update(
            {
                "department": "social",
                "epoch": epoch,
                "last_node": str(node),
                "last_run_at": timestamp,
                "last_payload_summary": payload_summary,
                "shadow": bool(shadow),
            }
        )
        atomic_write_json(state_path, next_state)

        _append_jsonl(
            state_dir / "heartbeats.jsonl",
            {"ts": timestamp, "epoch": epoch, "node": str(node)},
        )
        return receipt


def _parse_payload(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a fenced social node receipt")
    parser.add_argument("node", nargs="?", help="runtime node name")
    parser.add_argument("--node", dest="node_option", help="runtime node name")
    parser.add_argument("--payload", required=True, help="JSON or short text summary")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--out", required=True)
    parser.add_argument("--intended-epoch", type=int, default=None)
    parser.add_argument("--lock-timeout", type=float, default=3.0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", dest="shadow", action="store_true", default=True)
    mode.add_argument("--live", dest="shadow", action="store_false")
    args = parser.parse_args(argv)
    node = args.node_option or args.node
    if not node:
        parser.error("a node name is required")
    out = Path(args.out)
    try:
        receipt = write_record(
            args.state_dir,
            node,
            _parse_payload(args.payload),
            intended_epoch=args.intended_epoch,
            shadow=args.shadow,
            lock_timeout=args.lock_timeout,
        )
        atomic_write_json(out, receipt)
        return 0
    except (EpochError, RecordsLockTimeout, OSError, ValueError) as exc:
        atomic_write_json(out, {"status": "blocked", "reason": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
