"""Budget telemetry producer — derives budget_used.json from executed records.

Every department's daily script passes --budget <state>/budget_used.json to the
manager, whose Sense treats an absent file as the breach
budget_telemetry_missing ("spend is unverifiable"). This producer is the wire:
it aggregates the department's own run ledger (runs-v2.jsonl, the executed
receipts of the chain) over the manager's rolling window and writes the usage
file the manager reads. Department-agnostic: paths and the department name come
from the caller; nothing here knows any department's ceilings.

Fail-closed contract:
  * Evidence failures ABORT and DELETE any previous output — a stale file must
    never let yesterday's numbers pass as today's evidence. No file means the
    manager keeps breaching, which is the correct alarm state.
  * Failures: missing/unreadable ledger, an unparseable line, a record from
    another department or an unknown schema, an unparseable timestamp,
    metered-lane model calls (spend that cannot be priced), or a kernel budget
    ledger that exists but cannot be replayed.
  * dollars only ever comes from priced evidence (the kernel BudgetBroker
    ledger); run records carry no dollar amounts and contribute 0.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kernel.gateways.budget import BudgetBroker

RUNS_LEDGER = "runs-v2.jsonl"
KERNEL_BUDGET_LEDGER = Path("kernel") / "budget.jsonl"
RUN_SCHEMA = "run-record/v2"
METERED_LANE = "metered_forbidden"
BUDGET_KINDS = ("model_calls", "dollars", "worker_minutes")


class TelemetryError(RuntimeError):
    """Raised when spend cannot be derived from trustworthy evidence."""


def _parse_ts(value, line_no):
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise TelemetryError(f"line {line_no}: unparseable ts {value!r}") from exc
    if ts.tzinfo is None:
        raise TelemetryError(f"line {line_no}: naive ts {value!r}")
    return ts


def derive_from_runs(runs_path, department, window_start):
    """Aggregate in-window usage; validate EVERY line (a corrupt old row means
    the whole ledger is suspect, not just the window)."""
    try:
        lines = Path(runs_path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TelemetryError(f"run ledger unreadable: {exc}") from exc

    usage = {"model_calls": 0, "dollars": 0.0, "worker_minutes": 0.0}
    in_window = 0
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise TelemetryError(f"line {line_no}: unparseable record") from exc
        if not isinstance(record, dict):
            raise TelemetryError(f"line {line_no}: record is not an object")
        if record.get("schema") != RUN_SCHEMA:
            raise TelemetryError(
                f"line {line_no}: unknown schema {record.get('schema')!r}"
            )
        if record.get("department") != department:
            raise TelemetryError(
                f"line {line_no}: department {record.get('department')!r} "
                f"in the {department} ledger"
            )
        if _parse_ts(record.get("ts"), line_no) < window_start:
            continue

        in_window += 1
        cost = record.get("cost")
        if isinstance(cost, dict):
            calls = cost.get("model_calls")
            if not isinstance(calls, int) or isinstance(calls, bool) or calls < 0:
                raise TelemetryError(f"line {line_no}: invalid cost.model_calls")
            if cost.get("lane") == METERED_LANE and calls > 0:
                raise TelemetryError(
                    f"line {line_no}: {calls} model call(s) on the metered "
                    "lane — unpriceable spend"
                )
            usage["model_calls"] += calls
        duration_ms = record.get("duration_ms")
        if duration_ms is not None:
            if (
                isinstance(duration_ms, bool)
                or not isinstance(duration_ms, (int, float))
                or duration_ms < 0
            ):
                raise TelemetryError(f"line {line_no}: invalid duration_ms")
            usage["worker_minutes"] += duration_ms / 60000.0
    return usage, in_window


def merge_broker(usage, ledger_path):
    """Per-kind max() with the kernel reservation ledger when one exists —
    the broker sees reserved-but-uncommitted spend the run ledger cannot."""
    if not Path(ledger_path).exists():
        return usage, False
    broker = BudgetBroker(ledger_path)
    if not broker.telemetry_ok:
        raise TelemetryError("kernel budget ledger exists but cannot be replayed")
    for kind in BUDGET_KINDS:
        usage[kind] = max(usage[kind], broker.usage(kind))
    return usage, True


def produce(state_dir, department, out_path, window_days, now):
    window_start = now - timedelta(days=window_days)
    usage, in_window = derive_from_runs(
        Path(state_dir) / RUNS_LEDGER, department, window_start
    )
    usage, broker_seen = merge_broker(
        usage, Path(state_dir) / KERNEL_BUDGET_LEDGER
    )
    return {
        "model_calls": usage["model_calls"],
        "dollars": round(usage["dollars"], 4),
        "worker_minutes": round(usage["worker_minutes"], 3),
        "window_days": window_days,
        "window_start": window_start.isoformat(),
        "generated_at": now.isoformat(),
        "records": in_window,
        "source": RUNS_LEDGER + ("+kernel/budget.jsonl" if broker_seen else ""),
    }


def _write_atomic(out_path, payload):
    out_path = Path(out_path)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, out_path)


def _remove_stale(out_path):
    for path in (Path(out_path), Path(str(out_path) + ".tmp")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--department", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--now", default=None, help="ISO timestamp (tests)")
    args = parser.parse_args(argv)

    now = (
        datetime.fromisoformat(args.now)
        if args.now
        else datetime.now(timezone.utc)
    )
    try:
        payload = produce(
            args.state_dir, args.department, args.out, args.window_days, now
        )
        _write_atomic(args.out, payload)
    except (TelemetryError, OSError) as exc:
        _remove_stale(args.out)
        print(f"budget_telemetry: REFUSED — {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
