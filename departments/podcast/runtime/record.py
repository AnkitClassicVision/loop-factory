"""Append watchdog receipts and advance department state in fenced order."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "podcast" / "state"


class EpochError(RuntimeError):
    """Raised when a writer attempts to reuse or skip a state epoch."""


def utc_now() -> str:
    """Return an ISO 8601 timestamp explicitly anchored to UTC."""
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, value: Any) -> None:
    """Write JSON through a same-directory temporary file and os.replace."""
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
    except (OSError, ValueError) as exc:
        raise EpochError(f"cannot read existing state: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EpochError(f"existing state is not an object: {path}")
    return value


def _current_epoch(path: Path) -> int:
    state = _read_state(path)
    try:
        return int(state.get("epoch", -1))
    except (TypeError, ValueError) as exc:
        raise EpochError(f"existing state epoch is invalid: {path}") from exc


def write_record(
    state_dir: str | Path,
    node: str,
    payload_summary: Any,
    *,
    intended_epoch: int | None = None,
    shadow: bool = True,
    now: str | None = None,
) -> dict[str, Any]:
    """Record one node run: runs.jsonl, STATE.json, then heartbeat.

    STATE is a single-writer fence. The intended epoch must be exactly one
    greater than the epoch observed before the receipt append. The state is
    checked again after the append so a racing writer cannot be overwritten.
    """
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "STATE.json"
    previous = _current_epoch(state_path)
    epoch = previous + 1 if intended_epoch is None else int(intended_epoch)
    if epoch != previous + 1:
        relation = "already reached" if previous >= epoch else "would skip"
        raise EpochError(
            f"refusing epoch {epoch}: on-disk epoch {previous} {relation} the intended sequence"
        )

    timestamp = now or utc_now()
    receipt = {
        "node": str(node),
        "epoch": epoch,
        "timestamp": timestamp,
        "shadow": bool(shadow),
        "payload_summary": payload_summary,
    }

    # Required durable order: receipt first.
    runs_path = state_dir / "runs.jsonl"
    with runs_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())

    # Fence immediately before the atomic STATE replacement.
    observed_after_append = _current_epoch(state_path)
    if observed_after_append >= epoch:
        raise EpochError(
            f"refusing epoch {epoch}: on-disk epoch advanced to {observed_after_append}"
        )
    if observed_after_append != previous:
        raise EpochError(
            f"refusing epoch {epoch}: on-disk epoch changed from {previous} "
            f"to {observed_after_append}"
        )

    prior_state = _read_state(state_path)
    next_state = dict(prior_state)
    next_state.update(
        {
            "department": "podcast",
            "epoch": epoch,
            "last_node": str(node),
            "last_run_at": timestamp,
            "last_payload_summary": payload_summary,
            "shadow": bool(shadow),
        }
    )
    atomic_write_json(state_path, next_state)

    # Required durable order: heartbeat only after STATE succeeds.
    heartbeat_path = state_dir / "heartbeat"
    heartbeat_path.write_text(
        json.dumps({"ts": timestamp, "epoch": epoch, "node": str(node)}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return receipt


def _parse_payload(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a fenced podcast node receipt")
    parser.add_argument("node", nargs="?", help="runtime node name")
    parser.add_argument("--node", dest="node_option", help="runtime node name")
    parser.add_argument("--payload", required=True, help="JSON or short text summary")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--intended-epoch", type=int, default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", dest="shadow", action="store_true", default=True)
    mode.add_argument("--live", dest="shadow", action="store_false")
    args = parser.parse_args()
    node = args.node_option or args.node
    if not node:
        parser.error("a node name is required")
    receipt = write_record(
        args.state_dir,
        node,
        _parse_payload(args.payload),
        intended_epoch=args.intended_epoch,
        shadow=args.shadow,
    )
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
