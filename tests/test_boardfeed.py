from __future__ import annotations

import json
from pathlib import Path

from factory.boardfeed import build_feed
from factory.runrecord import append_record, build_record


NOW = "2026-08-02T20:00:00+00:00"


def _department(root: Path, name: str = "demodept") -> Path:
    department = root / "departments" / name
    state = department / "state"
    state.mkdir(parents=True)
    (state / "STATE.json").write_text(
        json.dumps(
            {
                "department": name,
                "epoch": 4,
                "last_cycle_at": "2026-08-02T19:30:00+00:00",
                "autonomy_state": "shadow",
                "open_findings": [],
                "escalations": 2,
            }
        ),
        encoding="utf-8",
    )
    (state / "heartbeats.jsonl").write_text(
        json.dumps({"ts": "2026-08-02T19:30:01+00:00", "ok": True}) + "\n",
        encoding="utf-8",
    )
    return department


def _record(
    department: Path,
    *,
    run_id: str,
    ts: str = "2026-08-02T19:00:00+00:00",
    node: str = "collect",
    status: str = "ok",
    engine: str | None = "codex",
    model: str | None = "model-a",
    auth_class: str | None = "oauth_cli",
    input_tokens: int = 10,
    output_tokens: int = 5,
    model_calls: int = 1,
    metered: bool = False,
) -> dict:
    record = build_record(
        schema="run-record/v2",
        rev=2,
        run_id=run_id,
        department=department.name,
        node=node,
        epoch=4,
        ts=ts,
        attempt=1,
        round=None,
        release={"hash": "abc123", "source_ref": "local"},
        trigger={"kind": "manual", "id": f"trigger-{run_id}", "dedupe_key": run_id},
        engine=engine,
        model=model,
        auth_class=auth_class,
        usage={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read": 0,
            "cache_creation": 0,
        },
        cost={
            "lane": "metered_forbidden" if metered else "flat_subscription",
            "model_calls": model_calls,
        },
        duration_ms=50,
        status=status,
        errors=[],
        artifacts=[],
        receipts=[{"kind": "local"}],
        evaluator=None,
        approval=None,
        external_actions_taken=0,
    )
    append_record(department / "state", record)
    return record


def _build(root: Path, **kwargs) -> tuple[list[dict], dict]:
    out = root / "feed.ndjson"
    receipt = build_feed(root, out=out, now=NOW, **kwargs)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    return rows, receipt


def _of_kind(rows: list[dict], kind: str) -> list[dict]:
    return [row for row in rows if row["kind"] == kind]


def _daily(rows: list[dict], department: str = "demodept") -> dict:
    return next(
        row
        for row in _of_kind(rows, "metrics")
        if row["department"] == department and row["data"]["metric_type"] == "daily_rollup"
    )


def test_dept_status_comes_from_state_and_last_heartbeat(tmp_path):
    _department(tmp_path)

    rows, receipt = _build(tmp_path)

    status = _of_kind(rows, "dept_status")[0]
    assert status["department"] == "demodept"
    assert status["data"] == {
        "autonomy_state": "shadow",
        "epoch": 4,
        "last_cycle_at": "2026-08-02T19:30:00+00:00",
        "ok": True,
        "open_findings": 0,
        "escalations": 2,
    }
    assert receipt["departments"] == 1


def test_active_runs_include_only_last_24_hours(tmp_path):
    department = _department(tmp_path)
    _record(department, run_id="recent", node="work")
    _record(department, run_id="old", ts="2026-08-01T18:59:59+00:00")

    rows, _ = _build(tmp_path)

    active = _of_kind(rows, "active_run")
    assert [row["data"]["run_id"] for row in active] == ["recent"]
    assert active[0]["data"]["node"] == "work"
    assert active[0]["data"]["attempt"] == 1


def test_auth_blocked_run_emits_auth_andon(tmp_path):
    department = _department(tmp_path)
    _record(
        department,
        run_id="auth-stop",
        status="blocked",
        auth_class="blocked",
        model_calls=0,
        input_tokens=0,
        output_tokens=0,
    )

    rows, _ = _build(tmp_path)

    incidents = [row for row in _of_kind(rows, "andon") if row["data"]["code"] == "AUTH"]
    assert len(incidents) == 1
    assert incidents[0]["data"]["run_id"] == "auth-stop"


def test_metered_violation_is_policy_andon_and_not_a_stat(tmp_path):
    department = _department(tmp_path)
    _record(
        department,
        run_id="clean",
        input_tokens=20,
        output_tokens=8,
        model_calls=2,
    )
    _record(
        department,
        run_id="metered",
        input_tokens=900,
        output_tokens=700,
        model_calls=9,
        metered=True,
    )

    rows, _ = _build(tmp_path)

    incidents = [row for row in _of_kind(rows, "andon") if row["data"]["code"] == "POLICY"]
    assert len(incidents) == 1
    assert incidents[0]["data"]["run_id"] == "metered"
    assert _daily(rows)["data"] | {} == {
        "metric_type": "daily_rollup",
        "period": "2026-08-02",
        "runs": 1,
        "ok": 1,
        "error": 0,
        "blocked": 0,
        "tokens_in": 20,
        "tokens_out": 8,
        "model_calls": 2,
    }


def test_open_approval_age_uses_supplied_now(tmp_path):
    department = _department(tmp_path)
    queue = department / "state" / "approval_queue.jsonl"
    queue.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "card_ref": "ANK-123",
                        "status": "pending_approval",
                        "queued_at": "2026-08-02T19:00:00+00:00",
                    }
                ),
                json.dumps(
                    {
                        "card_ref": "ANK-124",
                        "status": "approved",
                        "queued_at": "2026-08-02T18:00:00+00:00",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows, _ = _build(tmp_path)

    approvals = _of_kind(rows, "approval")
    assert len(approvals) == 1
    assert approvals[0]["data"]["age_s"] == 3600
    assert approvals[0]["data"]["card_ref"] == "ANK-123"


def test_lane_telemetry_aggregates_separately_across_two_engines(tmp_path):
    department = _department(tmp_path)
    _record(department, run_id="c1", input_tokens=4, output_tokens=2)
    _record(department, run_id="c2", input_tokens=6, output_tokens=3)
    _record(
        department,
        run_id="g1",
        engine="glm",
        model="model-b",
        auth_class="local_model",
        input_tokens=7,
        output_tokens=4,
        model_calls=3,
    )

    rows, _ = _build(tmp_path)

    lanes = {
        row["data"]["lane"]: row["data"]
        for row in _of_kind(rows, "metrics")
        if row["data"]["metric_type"] == "lane_telemetry"
    }
    assert lanes["codex"]["calls"] == 2
    assert lanes["codex"]["tokens_in"] == 10
    assert lanes["codex"]["tokens_out"] == 5
    assert lanes["glm"]["calls"] == 3
    assert lanes["glm"]["auth_class"] == "local_model"


def test_objective_rows_are_emitted_from_charter_objective_setpoints(tmp_path):
    department = _department(tmp_path)
    (department / "charter.yaml").write_text(
        """department: demodept
owner: owner
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [autonomy_state]
setpoints:
  objectives:
    publish_reliability:
      label: Publish reliability
      setpoint: 100
      minimum: 95
      target: 100
      unit: percent
""",
        encoding="utf-8",
    )

    rows, _ = _build(tmp_path)

    objective = next(
        row for row in _of_kind(rows, "metrics") if row["data"]["metric_type"] == "objective"
    )
    assert objective["data"] == {
        "metric_type": "objective",
        "objective_id": "publish_reliability",
        "label": "Publish reliability",
        "setpoint": 100,
        "minimum": 95,
        "target": 100,
        "observed": "unknown",
        "unit": "percent",
    }


def test_objective_rows_are_omitted_without_objective_mapping(tmp_path):
    _department(tmp_path)

    rows, _ = _build(tmp_path)

    assert not [
        row
        for row in _of_kind(rows, "metrics")
        if row["data"].get("metric_type") == "objective"
    ]


def test_malformed_jsonl_is_counted_in_final_feed_health(tmp_path):
    department = _department(tmp_path)
    (department / "state" / "runs-v2.jsonl").write_text("{not-json}\n", encoding="utf-8")

    rows, receipt = _build(tmp_path)

    assert rows[-1]["kind"] == "feed_health"
    assert rows[-1]["data"] == {"malformed": 1}
    assert receipt["malformed"] == 1


def test_same_inputs_and_now_produce_byte_identical_feeds(tmp_path):
    department = _department(tmp_path)
    _record(department, run_id="stable")
    first = tmp_path / "first.ndjson"
    second = tmp_path / "second.ndjson"

    build_feed(tmp_path, out=first, now=NOW)
    build_feed(tmp_path, out=second, now=NOW)

    assert first.read_bytes() == second.read_bytes()


def test_department_filter_restricts_feed_to_one_department(tmp_path):
    _department(tmp_path, "demodept")
    _department(tmp_path, "otherdept")

    rows, receipt = _build(tmp_path, department="demodept")

    represented = {row["department"] for row in rows if row["kind"] != "feed_health"}
    assert represented == {"demodept"}
    assert receipt["departments"] == 1


def test_open_state_finding_emits_flat_andon(tmp_path):
    department = _department(tmp_path)
    state_path = department / "state" / "STATE.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["open_findings"] = [
        {
            "code": "stalled",
            "severity": "warning",
            "detail": "no receipt",
            "observed": 2,
            "setpoint": 1,
        }
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    rows, _ = _build(tmp_path)

    incident = _of_kind(rows, "andon")[0]
    assert incident["data"] == {
        "severity": "warning",
        "code": "stalled",
        "detail": "no receipt",
        "observed": 2,
        "setpoint": 1,
    }


def test_script_node_records_count_in_rollup_without_lane_rows(tmp_path):
    """Regression: script nodes (engine/auth None) must count as runs."""
    department = _department(tmp_path)
    record = build_record(
        schema="run-record/v2",
        rev=2,
        run_id="script-1",
        department=department.name,
        node="sensor_node",
        epoch=4,
        ts="2026-08-02T19:10:00+00:00",
        attempt=1,
        round=None,
        release=None,
        trigger={"kind": "time", "id": "daily", "dedupe_key": "d-sensor"},
        engine=None,
        model=None,
        auth_class=None,
        usage=None,
        cost=None,
        duration_ms=12,
        status="ok",
        errors=[],
        artifacts=[],
        receipts=[],
        evaluator=None,
        approval=None,
        external_actions_taken=0,
    )
    append_record(department / "state", record)

    rows, _ = _build(tmp_path)

    daily = _daily(rows)["data"]
    assert daily["runs"] == 1
    assert daily["ok"] == 1
    assert daily["tokens_in"] == 0
    lanes = [
        row
        for row in _of_kind(rows, "metrics")
        if row["data"].get("metric_type") == "lane_telemetry"
    ]
    assert lanes == []
