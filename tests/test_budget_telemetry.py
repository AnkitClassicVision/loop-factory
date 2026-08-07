"""Budget telemetry producer: usage derives from executed records, and every
failure path deletes the output so a stale file can never impersonate fresh
evidence (no file -> the manager's budget_telemetry_missing breach stands)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from factory.budget_telemetry import main  # noqa: E402
from factory.manager import sense, compare, DEFAULT_THRESHOLDS  # noqa: E402

NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
DEPT = "sales"


def _record(ts, cost=None, duration_ms=5, department=DEPT,
            schema="run-record/v2"):
    return {
        "schema": schema, "department": department,
        "ts": ts.isoformat(), "cost": cost, "duration_ms": duration_ms,
        "node": "x", "status": "ok",
    }


def _write_runs(state_dir, records):
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "runs-v2.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    return path


def _run(state_dir, out, extra=()):
    return main([
        "--state-dir", str(state_dir), "--department", DEPT,
        "--out", str(out), "--now", NOW.isoformat(), *extra,
    ])


def test_happy_path_sums_only_the_window(tmp_path):
    _write_runs(tmp_path, [
        _record(NOW - timedelta(days=1),
                cost={"lane": "flat_subscription", "model_calls": 3},
                duration_ms=120000),
        _record(NOW - timedelta(days=2), cost=None, duration_ms=60000),
        _record(NOW - timedelta(days=8),  # outside the window
                cost={"lane": "flat_subscription", "model_calls": 99},
                duration_ms=600000),
    ])
    out = tmp_path / "budget_used.json"
    assert _run(tmp_path, out) == 0
    used = json.loads(out.read_text(encoding="utf-8"))
    assert used["model_calls"] == 3
    assert used["worker_minutes"] == 3.0
    assert used["dollars"] == 0.0
    assert used["records"] == 2
    assert used["window_days"] == 7
    assert not (tmp_path / "budget_used.json.tmp").exists()


def test_window_boundary_is_inclusive(tmp_path):
    _write_runs(tmp_path, [
        _record(NOW - timedelta(days=7),
                cost={"lane": "flat_subscription", "model_calls": 1}),
    ])
    out = tmp_path / "budget_used.json"
    assert _run(tmp_path, out) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["model_calls"] == 1


def test_missing_ledger_refuses_and_removes_stale_output(tmp_path):
    out = tmp_path / "budget_used.json"
    out.write_text('{"model_calls": 0}', encoding="utf-8")  # yesterday's file
    assert _run(tmp_path, out) == 1
    assert not out.exists()


def test_corrupt_line_refuses_even_outside_the_window(tmp_path):
    path = _write_runs(tmp_path, [_record(NOW - timedelta(days=1))])
    path.write_text("{corrupt\n" + path.read_text(encoding="utf-8"),
                    encoding="utf-8")
    out = tmp_path / "budget_used.json"
    out.write_text("{}", encoding="utf-8")
    assert _run(tmp_path, out) == 1
    assert not out.exists()


def test_metered_lane_calls_are_unpriceable_spend(tmp_path):
    _write_runs(tmp_path, [
        _record(NOW, cost={"lane": "metered_forbidden", "model_calls": 1}),
    ])
    out = tmp_path / "budget_used.json"
    assert _run(tmp_path, out) == 1
    assert not out.exists()


def test_metered_lane_with_zero_calls_is_fine(tmp_path):
    _write_runs(tmp_path, [
        _record(NOW, cost={"lane": "metered_forbidden", "model_calls": 0}),
    ])
    assert _run(tmp_path, tmp_path / "budget_used.json") == 0


def test_wrong_department_record_refuses(tmp_path):
    _write_runs(tmp_path, [_record(NOW, department="podcast")])
    assert _run(tmp_path, tmp_path / "budget_used.json") == 1


def test_unknown_schema_refuses(tmp_path):
    _write_runs(tmp_path, [_record(NOW, schema="run-record/v1")])
    assert _run(tmp_path, tmp_path / "budget_used.json") == 1


def test_naive_timestamp_refuses(tmp_path):
    row = _record(NOW)
    row["ts"] = "2026-08-07T12:00:00"  # no offset
    _write_runs(tmp_path, [row])
    assert _run(tmp_path, tmp_path / "budget_used.json") == 1


def test_broker_ledger_merges_per_kind_max(tmp_path):
    _write_runs(tmp_path, [
        _record(NOW, cost={"lane": "flat_subscription", "model_calls": 2}),
    ])
    kdir = tmp_path / "kernel"
    kdir.mkdir()
    rows = [
        {"rid": "model_calls-1-aa", "kind": "model_calls", "amount": 5,
         "now": 1},
        {"rid": "dollars-1-bb", "kind": "dollars", "amount": 3, "now": 1},
        {"event": "commit", "rid": "dollars-1-bb", "actual": 2.5},
    ]
    (kdir / "budget.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    out = tmp_path / "budget_used.json"
    assert _run(tmp_path, out) == 0
    used = json.loads(out.read_text(encoding="utf-8"))
    assert used["model_calls"] == 5      # broker reservation > run ledger
    assert used["dollars"] == 2.5        # committed actual
    assert used["source"].endswith("kernel/budget.jsonl")


def test_unreplayable_broker_ledger_refuses(tmp_path):
    _write_runs(tmp_path, [_record(NOW)])
    kdir = tmp_path / "kernel"
    kdir.mkdir()
    (kdir / "budget.jsonl").write_text("{corrupt\n", encoding="utf-8")
    out = tmp_path / "budget_used.json"
    out.write_text("{}", encoding="utf-8")
    assert _run(tmp_path, out) == 1
    assert not out.exists()


def test_manager_reads_the_produced_file_and_clears_the_breach(tmp_path):
    _write_runs(tmp_path, [
        _record(NOW, cost={"lane": "flat_subscription", "model_calls": 400},
                duration_ms=1000),
    ])
    out = tmp_path / "budget_used.json"
    assert _run(tmp_path, out) == 0
    sensed = sense(tmp_path, budget_path=out)
    assert sensed["budget_telemetry_missing"] is False
    assert sensed["budget_unreadable"] is False
    codes = {f["code"]: f for f in compare(sensed, DEFAULT_THRESHOLDS)}
    assert "budget_telemetry_missing" not in codes
    # 400 of the 450-call sales ceiling would breach; DEFAULT ceiling is 900,
    # so 400/900 stays under the 0.8 near-line — prove the near-line fires
    # when usage actually crosses it.
    tight = dict(DEFAULT_THRESHOLDS)
    tight["budget_ceilings"] = {"model_calls": 450}
    near = {f["code"] for f in compare(sensed, tight)}
    assert "budget_near:model_calls" in near
