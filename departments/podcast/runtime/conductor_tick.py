"""Daily shadow conductor node."""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from factory import conductor, runrecord


LOGGER = logging.getLogger(__name__)


def _emit(state: Path, dept: Path, *, started: float, status: str, errors=(), artifacts=()) -> None:
    runrecord.emit_record(
        state,
        department="podcast",
        node="conductor_tick",
        status=status,
        release=runrecord.read_release(dept),
        trigger={"kind": "time", "id": "podcast-daily", "dedupe_key": f"{datetime.now(timezone.utc).date().isoformat()}-conductor_tick"},
        duration_ms=int((time.perf_counter() - started) * 1000),
        errors=list(errors),
        artifacts=list(artifacts),
        external_actions_taken=0,
    )


def run(state_dir: Path, dept_dir: Path) -> dict:
    state, dept = Path(state_dir), Path(dept_dir)
    started = time.perf_counter()
    try:
        result = conductor.tick(dept, state, holder="conductor-daily")
    except Exception as exc:
        _emit(state, dept, started=started, status="error", errors=[type(exc).__name__])
        raise
    artifacts = [str(state / "lease-refusals.jsonl")] if not result["held_lease"] else [str(state / "conductor-shadow.jsonl"), str(state / "conductor-heartbeat.json")]
    _emit(state, dept, started=started, status="ok", artifacts=artifacts)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--dept-dir", type=Path, required=True)
    parser.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    try:
        run(args.state_dir, args.dept_dir)
    except Exception:
        LOGGER.exception("conductor tick crashed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
