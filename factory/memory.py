"""Local-first records and memory for a department, with a pluggable durable
backend seam.

Every department keeps its records LOCALLY, always, in plain inspectable files
under `departments/<dept>/state/`:

  runs.jsonl        append-only episodic history (every node run, manager tick)
  heartbeats.jsonl  liveness signal (append-only)
  STATE.json        manager memory (atomic write, monotonic epoch)
  approval_queue.jsonl  pending human decisions
  MANAGER_BRIEF.md  the human surface

Durable copies are OPTIONAL and go through the Backend seam. `LocalBackend` is
the always-on default (a no-op ship — the local files ARE the store). Remote
backends (S3, Open Brain, anything else) are seams: they raise until wired
deliberately, mirroring the kernel's KMSSigner pattern — a missing backend must
BLOCK a ship call, never silently pretend it archived.

Record order contract (single-writer discipline): runs append -> STATE update
-> heartbeat. On restart, runs.jsonl is authoritative; STATE is a cache.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class BackendNotWired(RuntimeError):
    """A durable backend was requested but has not been deliberately wired."""


class LocalStore:
    """The always-on local record store for one department."""

    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)

    # --- append-only records --------------------------------------------- #

    def append_run(self, row: dict) -> dict:
        row = dict(row)
        row.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        self._append(self.state_dir / "runs.jsonl", row)
        return row

    def heartbeat(self, **payload) -> dict:
        row = {"ts": datetime.now(timezone.utc).isoformat(), "ok": True, **payload}
        self._append(self.state_dir / "heartbeats.jsonl", row)
        return row

    # --- manager memory (atomic, monotonic epoch) ------------------------- #

    def read_state(self) -> dict | None:
        path = self.state_dir / "STATE.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def write_state(self, state: dict) -> dict:
        prior = self.read_state() or {}
        state = dict(state)
        state["epoch"] = int(prior.get("epoch", -1)) + 1
        path = self.state_dir / "STATE.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return state

    # --- reading records --------------------------------------------------- #

    def runs(self) -> list[dict]:
        return self._load(self.state_dir / "runs.jsonl")

    def heartbeats(self) -> list[dict]:
        return self._load(self.state_dir / "heartbeats.jsonl")

    # --- internals --------------------------------------------------------- #

    @staticmethod
    def _append(path: Path, row: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    @staticmethod
    def _load(path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]


# --------------------------------------------------------------------------- #
# Durable backend seam
# --------------------------------------------------------------------------- #

class Backend:
    """Ship local record files to a durable copy. Implementations must be
    idempotent and must never delete local files."""

    name = "abstract"

    def ship(self, department: str, paths: list[Path]) -> dict:
        raise NotImplementedError


class LocalBackend(Backend):
    """The default: local files ARE the store. Ship is a verified no-op."""

    name = "local"

    def ship(self, department: str, paths: list[Path]) -> dict:
        existing = [str(p) for p in paths if Path(p).exists()]
        return {"backend": self.name, "department": department, "shipped": existing,
                "note": "local-first: files already durable on this machine"}


class S3Backend(Backend):
    """Seam only. Wire deliberately (bucket, IAM write-only role, SSE) before use."""

    name = "s3"

    def __init__(self, bucket: str | None = None):
        self.bucket = bucket

    def ship(self, department: str, paths: list[Path]) -> dict:
        raise BackendNotWired(
            "S3 backend is a seam: create the bucket + write-only IAM role, then "
            "implement ship() deliberately (never wire ambient credentials here)")


class OpenBrainBackend(Backend):
    """Seam only. Sanitized digests to an Open Brain / OB-style memory surface."""

    name = "open_brain"

    def ship(self, department: str, paths: list[Path]) -> dict:
        raise BackendNotWired(
            "Open Brain backend is a seam: wire the capture tool and a redaction "
            "pass first — raw records must never ship to a memory surface")


_BACKENDS = {"local": LocalBackend, "s3": S3Backend, "open_brain": OpenBrainBackend}


def backends_from_charter(charter: dict) -> list[Backend]:
    """The charter's memory.backends list, plus the always-on local backend."""
    names = list((charter.get("memory") or {}).get("backends") or [])
    out: list[Backend] = [LocalBackend()]
    for name in names:
        if name == "local":
            continue
        cls = _BACKENDS.get(name)
        if cls is None:
            raise BackendNotWired(f"unknown memory backend '{name}' in charter")
        out.append(cls())
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Department record store utilities")
    sub = parser.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tail", help="print the last N runs")
    t.add_argument("--state-dir", required=True)
    t.add_argument("-n", type=int, default=10)
    args = parser.parse_args()
    store = LocalStore(args.state_dir)
    for row in store.runs()[-args.n:]:
        print(json.dumps(row))


if __name__ == "__main__":
    main()
