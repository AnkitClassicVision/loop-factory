from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from factory import rollup, scores
from factory.board import render_board
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
    (department / "charter.yaml").write_text(
        f"""department: {name}
owner: owner
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [autonomy_state]
""",
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
    telemetry_calls = model_calls if model_calls > 0 else int(auth_class == "blocked")
    telemetry_path = department / "state" / "telemetry.jsonl"
    for index in range(telemetry_calls):
        telemetry = {
            "schema_version": "step-telemetry/v1",
            "ts": ts,
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": engine or "script",
            "gen_ai.request.model": model,
            "gen_ai.response.model": model,
            "gen_ai.usage.input_tokens": input_tokens if index == 0 else 0,
            "gen_ai.usage.output_tokens": output_tokens if index == 0 else 0,
            "gen_ai.response.finish_reasons": ["stop"],
            "duration_ms": 50,
            "error.type": None,
            "loopfactory.cost_usd": 0,
            "loopfactory.auth.route": (
                "vault_api_key" if metered else auth_class
            ),
            "loopfactory.engine": engine,
            "loopfactory.price.schema_version": "model-prices/v1",
            "loopfactory.price.effective_date": "2026-08-02",
            "loopfactory.telemetry.source": "runner_reported",
            "loopfactory.department": department.name,
            "loopfactory.run_id": run_id,
            "loopfactory.step_id": f"{node}-{index}",
            "loopfactory.node": node,
            "estimated": False,
        }
        with telemetry_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(telemetry) + "\n")
    return record


def _build(root: Path, *, rebuild_rollup: bool = True, **kwargs) -> tuple[list[dict], dict]:
    if rebuild_rollup:
        result = rollup.rebuild(root)
        assert result["complete"] is True
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
        if row["department"] == department
        and row["data"].get("metric_type") == "daily_rollup"
    )


def test_dept_status_comes_from_rollup_and_last_heartbeat(tmp_path):
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
        "escalations": "unknown",
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
    assert active[0]["data"]["attempt"] == "unknown"


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
        "evaluator_pass_rate": "unknown",
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
        if row["data"].get("metric_type") == "lane_telemetry"
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
        row
        for row in _of_kind(rows, "metrics")
        if row["data"].get("metric_type") == "objective"
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

    result = rollup.rebuild(tmp_path)
    assert result["complete"] is False
    rows, receipt = _build(tmp_path, rebuild_rollup=False)

    assert rows[-1]["kind"] == "feed_health"
    assert rows[-1]["data"]["projection_status"] == "incomplete"
    assert rows[-1]["data"]["projection_reason"] == "rollup_rebuild_incomplete"
    assert receipt["projection_status"] == "incomplete"


def test_same_inputs_and_now_produce_byte_identical_feeds(tmp_path):
    department = _department(tmp_path)
    _record(department, run_id="stable")
    result = rollup.rebuild(tmp_path)
    assert result["complete"] is True
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
        "detail": "unknown",
        "observed": "unknown",
        "setpoint": "unknown",
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


def test_score_and_receipt_summaries_come_from_canonical_rollup(tmp_path):
    department = _department(tmp_path)
    score = scores.build_score(
        name="quality",
        value=1,
        label="pass",
        explanation="fixture",
        source="script",
        judge_model=None,
        config_version="v1",
        target_ref={
            "department": department.name,
            "run_id": "canonical-run",
            "step_id": "qa",
            "node": "qa",
        },
        ts="2026-08-02T19:15:00+00:00",
    )
    (department / "state" / "scores.jsonl").write_text(
        json.dumps(score) + "\n", encoding="utf-8"
    )
    (department / "state" / "receipt.json").write_text(
        json.dumps(
            {
                "receipt_id": "receipt-1",
                "run_id": "canonical-run",
                "node": "qa",
                "status": "ok",
                "verified": True,
                "ts": "2026-08-02T19:16:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    rows, _ = _build(tmp_path)

    assert _daily(rows)["data"]["evaluator_pass_rate"] == 100
    groups = {
        row["data"].get("group"): row["data"]
        for row in _of_kind(rows, "metrics")
    }
    assert groups["canonical scores"]["total"] == 1
    assert groups["canonical scores"]["pass"] == 1
    assert groups["canonical receipts"]["total"] == 1
    assert groups["canonical receipts"]["verified"] == 1


def test_missing_rollup_is_incomplete_and_never_reads_direct_runs(tmp_path):
    department = _department(tmp_path)
    _record(department, run_id="direct-only", node="must-not-render")

    rows, receipt = _build(tmp_path, rebuild_rollup=False)

    assert _of_kind(rows, "active_run") == []
    health = _of_kind(rows, "feed_health")[0]["data"]
    assert health["projection_status"] == "incomplete"
    assert health["projection_reason"] == "rollup_missing"
    assert receipt["projection_status"] == "incomplete"
    assert [row for row in rows if row["kind"] != "feed_health"] == []
    page = render_board(tmp_path / "feed.ndjson", tmp_path / "board.html")
    assert "STALE / INCOMPLETE CANONICAL ROLLUP" in page
    assert "must-not-render" not in page


def test_fresh_rollup_does_not_invent_department_from_direct_metadata(tmp_path):
    _department(tmp_path, "canonical")
    result = rollup.rebuild(tmp_path)
    assert result["complete"] is True
    _department(tmp_path, "phantom-direct")

    rows, receipt = _build(tmp_path, rebuild_rollup=False)

    represented = {
        row["department"] for row in rows if row["kind"] != "feed_health"
    }
    assert represented == {"canonical"}
    assert receipt["departments"] == 1


def test_partial_rollup_schema_is_incomplete_and_renders_warning(tmp_path):
    db_path = tmp_path / "estate" / "state" / "rollup.sqlite3"
    db_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(db_path)
    try:
        for entity in rollup.ENTITIES:
            connection.execute(f"CREATE TABLE {entity} (id TEXT PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    rows, receipt = _build(
        tmp_path,
        rebuild_rollup=False,
        rollup_max_age_seconds=10**10,
    )

    health = _of_kind(rows, "feed_health")[0]["data"]
    assert health["projection_status"] == "incomplete"
    assert health["projection_reason"] == "rollup_unreadable"
    assert receipt["projection_status"] == "incomplete"
    page = render_board(tmp_path / "feed.ndjson", tmp_path / "board.html")
    assert "STALE / INCOMPLETE CANONICAL ROLLUP" in page


def test_stale_rollup_wins_over_phantom_direct_run_and_renders_warning(tmp_path):
    department = _department(tmp_path)
    _record(department, run_id="canonical-run", node="canonical-step")
    db_path = tmp_path / "estate" / "state" / "rollup.sqlite3"
    result = rollup.rebuild(tmp_path, db_path)
    assert result["complete"] is True

    _record(department, run_id="phantom-direct-run", node="phantom-step")
    stale_epoch = 1_754_164_800  # 2025-08-02T20:00:00Z
    os.utime(db_path, (stale_epoch, stale_epoch))

    rows, _ = _build(tmp_path, rebuild_rollup=False)

    active_run_ids = [row["data"]["run_id"] for row in _of_kind(rows, "active_run")]
    assert active_run_ids == ["canonical-run"]
    health = _of_kind(rows, "feed_health")[0]
    assert health["data"]["projection_status"] == "stale"
    assert health["data"]["rollup_age_s"] > health["data"]["rollup_max_age_s"]

    page = render_board(tmp_path / "feed.ndjson", tmp_path / "board.html")
    assert "STALE / INCOMPLETE CANONICAL ROLLUP" in page
    assert "phantom-step" not in page
