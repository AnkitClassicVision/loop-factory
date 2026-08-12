"""Weekly alarm-only runtime wrapper for the deterministic floor compiler."""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory import runrecord
from factory.floor_compiler import compile_floors


LOGGER = logging.getLogger(__name__)


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _latest_history(state_dir: Path) -> dict | None:
    path = state_dir / "floors-history.jsonl"
    if not path.exists():
        return None
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else None


def _emit(state_dir: Path, *, started: float, status: str, errors=(), artifacts=()) -> None:
    runrecord.emit_record(
        state_dir,
        department="podcast",
        node="floor_compiler_run",
        status=status,
        release=runrecord.read_release(state_dir.parent),
        trigger={"kind": "time", "id": "podcast-daily", "dedupe_key": f"{datetime.now(timezone.utc).date().isoformat()}-floor_compiler_run"},
        duration_ms=int((time.perf_counter() - started) * 1000),
        errors=list(errors),
        artifacts=list(artifacts),
        external_actions_taken=0,
    )


def run(state_dir: Path, dept_dir: Path, *, now: datetime | None = None):
    state, dept, current = Path(state_dir), Path(dept_dir), _utc(now)
    started = time.perf_counter()
    try:
        latest = _latest_history(state)
        if latest is not None:
            last = datetime.fromisoformat(latest["computed_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
            if current - last < timedelta(days=7):
                _emit(state, started=started, status="ok", artifacts=["skipped_not_due: newest floors history is younger than 7 days"])
                return None
        result = compile_floors(dept, state, now=current)
        status = "unknown" if result["status"] == "unconfigured" else ("alarm" if result["status"] == "frozen" or result["changes"] else "ok")
        observation = {
            "ts": current.isoformat(), "sensor": "floors", "subject": "floor-compiler",
            "status": status, "evidence": str(state / "floors-history.jsonl"),
            "detail": result["reason"], "metrics": {"changes": len(result["changes"])},
        }
        state.mkdir(parents=True, exist_ok=True)
        with (state / "observations.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(observation, sort_keys=True) + "\n")
        # Node health, not finding health (repo convention, see
        # expectation_reconcile): alarms travel in the observation; the run
        # record stays "ok" unless the node itself crashed.
        _emit(state, started=started, status="ok", artifacts=[str(state / "floors-history.jsonl"), str(state / "observations.jsonl")])
        return observation
    except Exception as exc:
        _emit(state, started=started, status="error", errors=[type(exc).__name__])
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--dept-dir", type=Path, required=True)
    parser.add_argument("--shadow", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    try:
        run(args.state_dir, args.dept_dir)
    except Exception:
        LOGGER.exception("floor compiler crashed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
