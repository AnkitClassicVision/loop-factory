"""Shared deterministic helpers for outreach governance sensors."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import runrecord


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def append_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def emit(state_dir: Path, node: str, started: float, artifact: Path,
         errors: list[str] | None = None) -> None:
    errors = errors or []
    runrecord.emit_record(
        state_dir, department="outreach", node=node,
        status="error" if errors else "ok",
        release=runrecord.read_release(state_dir.parent),
        trigger={"kind": "time", "id": "outreach-daily",
                 "dedupe_key": f"{utcnow().date().isoformat()}-{node}"},
        engine=None, model=None, auth_class=None, usage=None,
        cost={"lane": "flat_subscription", "model_calls": 0},
        duration_ms=int((time.perf_counter() - started) * 1000),
        errors=errors, artifacts=[str(artifact)], receipts=[str(artifact)],
        evaluator=None, approval=None, external_actions_taken=0,
    )


def resolve(root: Path, configured: str) -> Path:
    path = Path(configured)
    return path if path.is_absolute() else root / path
