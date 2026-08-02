import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from factory import rollup, scores
from kernel import lock_service
from kernel import receipts
from kernel.jsonl_store import append_jsonl


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, value=1_786_000_000.0):
        self.value = value

    def __call__(self):
        return self.value


def _service(tmp_path, prices):
    return lock_service.LockService(
        receipts.LocalSigner(key="test"),
        budget_ledger=tmp_path / "state" / "kernel" / "budget.jsonl",
        freq_ledger=tmp_path / "state" / "kernel" / "frequency.jsonl",
        nonce_ledger=tmp_path / "state" / "kernel" / "nonces.jsonl",
        telemetry_path=tmp_path / "state" / "telemetry.jsonl",
        price_table_path=prices,
        clock=Clock(),
    )


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def test_model_call_records_exact_usage_cost_and_budget_actual(tmp_path):
    prices = tmp_path / "prices.json"
    prices.write_text(
        json.dumps(
            {
                "schema_version": "model-prices/v1",
                "effective_date": "2026-08-02",
                "ratified": True,
                "models": {
                    "requested-model": {
                        "input_usd_per_1m_tokens": 2,
                        "output_usd_per_1m_tokens": 8,
                    },
                    "resolved-model": {
                        "input_usd_per_1m_tokens": 2,
                        "output_usd_per_1m_tokens": 8,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    service = _service(tmp_path, prices)
    issued = service.request_model("sanitized prompt", sanitized=True)

    result = service.call_model(
        "sanitized prompt",
        issued["receipt"],
        runner=lambda _prompt: {
            "output": "private generated body",
            "provider": "openai",
            "request_model": "requested-model",
            "response_model": "resolved-model",
            "usage": {"input_tokens": 1_000, "output_tokens": 500},
            "finish_reasons": ["stop"],
            "auth_route": "oauth_cli",
            "engine": "codex_oauth",
            "estimated": True,
        },
        run_id="run-1",
        step_id="step-2",
        node="draft",
    )

    assert result == "private generated body"
    telemetry = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")
    assert len(telemetry) == 1
    row = telemetry[0]
    assert row["gen_ai.usage.input_tokens"] == 1_000
    assert row["gen_ai.usage.output_tokens"] == 500
    assert row["loopfactory.cost_usd"] == pytest.approx(0.006)
    assert row["loopfactory.auth.route"] == "oauth_cli"
    assert row["loopfactory.run_id"] == "run-1"
    assert row["estimated"] is True
    assert row["loopfactory.price.schema_version"] == "model-prices/v1"
    assert row["loopfactory.telemetry.source"] == "runner_reported"
    assert "sanitized prompt" not in json.dumps(row)
    assert "private generated body" not in json.dumps(row)
    budget = _read_jsonl(tmp_path / "state" / "kernel" / "budget.jsonl")
    assert budget[-1] == {"event": "commit", "rid": budget[0]["rid"], "actual": 1}
    with pytest.raises(lock_service.budget_mod.BudgetExceeded):
        service.budget.commit(budget[0]["rid"], -1)


def test_runner_cannot_persist_unreviewed_model_or_engine_identifiers(tmp_path):
    secret_hex = "736563726574"
    prices = tmp_path / "prices.json"
    prices.write_text(
        json.dumps(
            {
                "schema_version": "model-prices/v1",
                "effective_date": "2026-08-02",
                "ratified": True,
                "models": {},
            }
        ),
        encoding="utf-8",
    )
    service = _service(tmp_path, prices)
    issued = service.request_model("safe", sanitized=True)
    with pytest.raises(lock_service.LockServiceDown):
        service.call_model(
            "safe",
            issued["receipt"],
            runner=lambda _: {
                "output": "ok",
                "request_model": secret_hex,
                "engine": secret_hex,
            },
        )
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[-1]
    assert row["gen_ai.request.model"] is None
    assert row["loopfactory.engine"] is None
    assert secret_hex not in json.dumps(row)


def test_legacy_runner_records_null_usage_and_failure_records_error(tmp_path):
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"models": {}}), encoding="utf-8")
    service = _service(tmp_path, prices)
    issued = service.request_model("legacy", sanitized=True)
    assert service.call_model("legacy", issued["receipt"], runner=lambda _: "ok") == "ok"
    legacy = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[-1]
    assert legacy["gen_ai.usage.input_tokens"] is None
    assert legacy["gen_ai.usage.output_tokens"] is None
    assert legacy["loopfactory.cost_usd"] is None
    assert legacy["loopfactory.auth.route"] == "blocked"

    failed = service.request_model("fails", sanitized=True)
    with pytest.raises(lock_service.LockServiceDown) as raised:
        service.call_model(
            "fails", failed["receipt"], runner=lambda _: (_ for _ in ()).throw(RuntimeError("secret body"))
        )
    failure = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[-1]
    assert failure["error.type"] == "RuntimeError"
    assert "secret body" not in json.dumps(failure)
    assert "secret body" not in str(raised.value)
    budget = _read_jsonl(tmp_path / "state" / "kernel" / "budget.jsonl")
    assert budget[-1]["event"] == "commit"

    revoked = service.request_model("revoked", sanitized=True)
    service.revoke(revoked["nonce"])
    with pytest.raises(lock_service.model.GatewayDenied):
        service.call_model("revoked", revoked["receipt"], runner=lambda _: "never")
    denied = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[-1]
    assert denied["error.type"] == "GatewayDenied"


def test_known_usage_refuses_unratified_price_table(tmp_path):
    prices = tmp_path / "prices.json"
    prices.write_text(
        json.dumps(
            {
                "schema_version": "model-prices/v1",
                "effective_date": "2026-08-02",
                "ratified": False,
                "models": {"m": {"input_usd_per_1m_tokens": 1, "output_usd_per_1m_tokens": 2}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not owner-ratified"):
        lock_service.model.compute_cost_usd("m", 1, 1, prices)


def test_declared_model_refuses_unratified_table_before_runner(tmp_path):
    prices = tmp_path / "prices.json"
    prices.write_text(
        json.dumps(
            {
                "schema_version": "model-prices/v1",
                "effective_date": "2026-08-02",
                "ratified": False,
                "models": {
                    "m": {
                        "input_usd_per_1m_tokens": None,
                        "output_usd_per_1m_tokens": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    service = _service(tmp_path, prices)
    issued = service.request_model("safe", sanitized=True)
    invoked = []

    with pytest.raises(lock_service.LockServiceDown):
        service.call_model(
            "safe",
            issued["receipt"],
            runner=lambda _: invoked.append(True) or "must not run",
            provider_name="openai",
            request_model="m",
            auth_route="oauth_cli",
            engine="codex_oauth",
        )

    assert invoked == []
    budget = _read_jsonl(tmp_path / "state" / "kernel" / "budget.jsonl")
    assert [row.get("event", "reserve") for row in budget] == ["reserve", "release"]


def test_caller_provider_uses_the_same_allowlist_and_refuses_before_runner(tmp_path):
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"models": {}}), encoding="utf-8")
    service = _service(tmp_path, prices)
    issued = service.request_model("safe", sanitized=True)
    invoked = []

    with pytest.raises(lock_service.LockServiceDown):
        service.call_model(
            "safe",
            issued["receipt"],
            runner=lambda _: invoked.append(True) or "must not run",
            provider_name="untrusted-provider",
        )

    assert invoked == []
    row = _read_jsonl(tmp_path / "state" / "telemetry.jsonl")[-1]
    assert row["gen_ai.provider.name"] is None


def test_success_survives_telemetry_loss_and_failure_chains_it(tmp_path, monkeypatch, caplog):
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"models": {}}), encoding="utf-8")
    service = _service(tmp_path, prices)

    def append_failure(*_args, **_kwargs):
        raise OSError("telemetry unavailable")

    monkeypatch.setattr(lock_service.model, "append_jsonl", append_failure)
    issued = service.request_model("safe", sanitized=True)
    assert service.call_model("safe", issued["receipt"], runner=lambda _: "kept") == "kept"
    assert "model telemetry append failed" in caplog.text

    failed = service.request_model("fails", sanitized=True)
    with pytest.raises(lock_service.LockServiceDown) as raised:
        service.call_model(
            "fails",
            failed["receipt"],
            runner=lambda _: (_ for _ in ()).throw(RuntimeError("provider failed")),
        )
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert isinstance(raised.value.__cause__.__cause__, OSError)


def test_model_and_budget_share_the_kernel_jsonl_module_identity():
    assert lock_service.model.append_jsonl is append_jsonl
    assert lock_service.budget_mod.append_jsonl is append_jsonl


def test_social_file_loaded_kernel_round_trips_model_without_pythonpath(tmp_path):
    runtime_dir = PROJECT_ROOT / "departments" / "social" / "runtime"
    state_dir = tmp_path / "state"
    child = r"""
import importlib.util
import json
import sys
from pathlib import Path

runtime_dir = Path.cwd().resolve()
assert Path(sys.path[0] or ".").resolve() == runtime_dir
bridge_path = runtime_dir.parents[2] / "kernel" / "bridge.py"
spec = importlib.util.spec_from_file_location("social_kernel_bridge", bridge_path)
bridge = importlib.util.module_from_spec(spec)
sys.modules["social_kernel_bridge"] = bridge
spec.loader.exec_module(bridge)

kernel = bridge.load_kernel(Path(sys.argv[1]))
issued = kernel.request_model("sanitized", sanitized=True)
result = kernel.call_model("sanitized", issued["receipt"], runner=lambda _: "ok")
assert result == "ok"
lock_service_module = sys.modules["lock_service"]
assert lock_service_module.model.append_jsonl is lock_service_module.budget_mod.append_jsonl
assert lock_service_module.model.append_jsonl is sys.modules["kernel.jsonl_store"].append_jsonl
rows = [
    json.loads(line)
    for line in (Path(sys.argv[1]) / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
]
assert len(rows) == 1
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["OE_KERNEL_SIGNING_KEY"] = "launch-context-test"

    completed = subprocess.run(
        [sys.executable, "-c", child, str(state_dir)],
        cwd=runtime_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert len(_read_jsonl(state_dir / "telemetry.jsonl")) == 1


def test_candidate_price_table_covers_review_lanes():
    table = json.loads((PROJECT_ROOT / "factory" / "prices.json").read_text(encoding="utf-8"))
    assert table["ratified"] is False
    assert {
        "claude-subscription/default",
        "codex-oauth/default",
        "glm-oauth/default",
        "gemini-subscription/default",
        "kimi-subscription/default",
        "openrouter/x-ai/grok-default",
    } <= set(table["models"])
    for model in table["models"].values():
        if model["input_usd_per_1m_tokens"] is None:
            assert model["output_usd_per_1m_tokens"] is None
            assert model["estimated"] is False
            assert model["price_status"] == "unknown_placeholder"


def test_jsonl_append_is_concurrent_and_score_records_are_separate(tmp_path):
    path = tmp_path / "state" / "concurrent.jsonl"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda value: append_jsonl(path, {"value": value}), range(100)))
    assert sorted(row["value"] for row in _read_jsonl(path)) == list(range(100))

    score = scores.build_score(
        name="qa",
        value=1.0,
        label="pass",
        explanation="deterministic validator passed",
        source="script",
        judge_model=None,
        config_version="qa/v1",
        target_ref={"run_id": "run-1", "step_id": "step-2", "node": "qa", "department": "alpha"},
        ts="2026-08-02T10:00:00+00:00",
    )
    target = scores.append_score(tmp_path / "state", score)
    assert target.name == "scores.jsonl"
    assert _read_jsonl(target) == [score]
    assert not (tmp_path / "state" / "runs.jsonl").exists()
    with pytest.raises(ValueError):
        scores.build_score(
            name="qa",
            value=1,
            label="pass",
            explanation="x",
            source="judge",
            judge_model=None,
            config_version="v1",
            target_ref={"run_id": None, "step_id": None, "node": "qa", "department": "alpha"},
        )


def test_jsonl_allows_symlinked_parent_but_refuses_symlink_below_state(tmp_path):
    real_root = tmp_path / "real-root"
    state_dir = real_root / "state"
    state_dir.mkdir(parents=True)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real_root, target_is_directory=True)

    linked_path = linked_root / "state" / "telemetry.jsonl"
    append_jsonl(linked_path, {"ok": True})
    assert _read_jsonl(state_dir / "telemetry.jsonl") == [{"ok": True}]

    outside = tmp_path / "outside"
    outside.mkdir()
    (state_dir / "redirected").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        append_jsonl(
            state_dir / "redirected" / "nested" / "telemetry.jsonl",
            {"ok": False},
        )
    assert list(outside.iterdir()) == []


def test_budget_reservations_replay_inside_cross_process_transaction(tmp_path):
    ledger = tmp_path / "budget.jsonl"
    brokers = [
        lock_service.budget_mod.BudgetBroker(ledger, {"model_calls": 100})
        for _ in range(10)
    ]
    with ThreadPoolExecutor(max_workers=10) as pool:
        reservation_ids = list(
            pool.map(
                lambda pair: pair[1].reserve("model_calls", 1, pair[0]),
                enumerate(brokers),
            )
        )
    rows = _read_jsonl(ledger)
    assert len(rows) == 10
    assert len(set(reservation_ids)) == 10
    reloaded = lock_service.budget_mod.BudgetBroker(ledger, {"model_calls": 100})
    assert reloaded.usage("model_calls") == 10


def test_budget_release_checks_reservation_before_append(tmp_path):
    ledger = tmp_path / "budget.jsonl"
    broker = lock_service.budget_mod.BudgetBroker(ledger, {"model_calls": 100})

    with pytest.raises(KeyError):
        broker.release("missing")

    assert not ledger.exists() or _read_jsonl(ledger) == []
    reloaded = lock_service.budget_mod.BudgetBroker(ledger, {"model_calls": 100})
    reservation_id = reloaded.reserve("model_calls", 1, 1)
    reloaded.release(reservation_id)
    assert lock_service.budget_mod.BudgetBroker(
        ledger, {"model_calls": 100}
    ).usage("model_calls") == 0


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _rollup_fixture(root):
    alpha = root / "departments" / "alpha" / "state"
    beta = root / "departments" / "beta" / "state"
    estate = root / "estate" / "state"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    estate.mkdir(parents=True)
    (alpha / "STATE.json").write_text(
        json.dumps({"epoch": 4, "ok": True, "last_cycle_at": "2026-08-02T12:00:00Z"}), encoding="utf-8"
    )
    (beta / "STATE.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (estate / "STATE.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    _write_jsonl(
        alpha / "runs.jsonl",
        [
            {"run_id": "active-1", "node": "extract", "status": "active", "timestamp": "2026-08-02T10:00:00Z"},
            {"run_id": "active-1", "node": "qa", "status": "active", "timestamp": "2026-08-02T10:01:00Z"},
        ],
    )
    _write_jsonl(
        alpha / "telemetry.jsonl",
        [
            {
                "schema_version": "step-telemetry/v1",
                "ts": "2026-08-02T10:01:00Z",
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "m",
                "gen_ai.response.model": "m",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 20,
                "gen_ai.response.finish_reasons": ["stop"],
                "duration_ms": 12,
                "error.type": None,
                "loopfactory.cost_usd": 0.25,
                "loopfactory.auth.route": "oauth_cli",
                "loopfactory.engine": "engine",
                "loopfactory.price.schema_version": "model-prices/v1",
                "loopfactory.price.effective_date": "2026-08-02",
                "loopfactory.telemetry.source": "runner_reported",
                "loopfactory.department": "alpha",
                "loopfactory.run_id": "active-1",
                "loopfactory.step_id": "step-qa",
                "loopfactory.node": "qa",
                "estimated": False,
            }
        ],
    )
    _write_jsonl(
        alpha / "scores.jsonl",
        [
            scores.build_score(
                name="qa", value=1, label="pass", explanation="ok", source="script",
                judge_model=None, config_version="v1",
                target_ref={"run_id": "active-1", "step_id": "step-qa", "node": "qa", "department": "alpha"},
                ts="2026-08-02T10:02:00Z",
            ),
            scores.build_score(
                name="qa", value=0, label="fail", explanation="failed", source="script",
                judge_model=None, config_version="v1",
                target_ref={"run_id": "older", "step_id": "step-qa", "node": "qa", "department": "alpha"},
                ts="2026-08-01T10:02:00Z",
            ),
        ],
    )
    _write_jsonl(alpha / "incidents.jsonl", [{"code": "engine_down", "status": "open", "severity": "breach", "ts": "2026-08-02T09:00:00Z"}])
    (alpha / "receipt.json").write_text(
        json.dumps({"receipt_id": "rec-1", "run_id": "active-1", "node": "qa", "status": "ok", "verified": True}), encoding="utf-8"
    )
    _write_jsonl(
        estate / "decisions_outbox.jsonl",
        [
            {"decision_id": "newer", "department": "alpha", "ts": "2026-08-02T11:00:00Z", "packet": {"draft": "must not export"}},
            {"decision_id": "older", "department": "beta", "ts": "2026-08-01T11:00:00Z", "packet": {"draft": "must not export"}},
        ],
    )


def test_fresh_rollup_export_answers_board_minimum_queries_deterministically(tmp_path):
    root = tmp_path / "repo"
    _rollup_fixture(root)
    db_one = tmp_path / "one.sqlite3"
    db_two = tmp_path / "two.sqlite3"
    export_one = tmp_path / "export-one"
    export_two = tmp_path / "export-two"

    first = rollup.rebuild(root, db_one)
    assert first["complete"] is True
    rollup.export_ndjson(db_one, export_one)
    second = rollup.rebuild(root, db_two)
    rollup.export_ndjson(db_two, export_two)
    assert first["counts"] == second["counts"]
    for entity in rollup.ENTITIES:
        assert (export_one / f"{entity}.ndjson").read_bytes() == (export_two / f"{entity}.ndjson").read_bytes()

    connection = sqlite3.connect(db_one)
    try:
        assert connection.execute(
            "SELECT run_id, current_step FROM run WHERE status = 'active' ORDER BY ts"
        ).fetchall() == [("active-1", "qa")]
        assert connection.execute(
            "SELECT department, substr(ts,1,10), round(sum(cost_usd),2) FROM step_telemetry GROUP BY department, substr(ts,1,10)"
        ).fetchall() == [("alpha", "2026-08-02", 0.25)]
        assert connection.execute(
            "SELECT round(avg(CASE WHEN label = 'pass' THEN 1.0 ELSE 0.0 END),2) FROM score"
        ).fetchone() == (0.5,)
        open_incidents = connection.execute(
            "SELECT code FROM incident WHERE status = 'open' ORDER BY code"
        ).fetchall()
        assert ("engine_down",) in open_incidents
        assert connection.execute(
            "SELECT decision_id FROM approval WHERE status IN ('pending','pending_approval') ORDER BY queued_at"
        ).fetchall() == [("older",), ("newer",)]
    finally:
        connection.close()
    exported_approvals = (export_one / "approval.ndjson").read_text(encoding="utf-8")
    assert "must not export" not in exported_approvals
    assert "explanation" not in (export_one / "score.ndjson").read_text(encoding="utf-8")


def test_unreadable_or_symlinked_source_records_finding_without_replacing_complete_rollup(tmp_path):
    root = tmp_path / "repo"
    _rollup_fixture(root)
    db_path = tmp_path / "rollup.sqlite3"
    complete = rollup.rebuild(root, db_path)
    assert complete["complete"] is True
    before = db_path.read_bytes()

    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps({"loopfactory.cost_usd": 999}) + "\n", encoding="utf-8")
    source = root / "departments" / "beta" / "state" / "telemetry.jsonl"
    source.symlink_to(outside)
    incomplete = rollup.rebuild(root, db_path)

    assert incomplete["complete"] is False
    assert db_path.read_bytes() == before
    incomplete_path = Path(incomplete["database"])
    assert incomplete_path.name.endswith(".incomplete")
    connection = sqlite3.connect(incomplete_path)
    try:
        codes = [row[0] for row in connection.execute("SELECT code FROM incident")]
    finally:
        connection.close()
    assert any(code.startswith("source_unreadable:") for code in codes)
    with pytest.raises(ValueError, match="incomplete"):
        rollup.export_ndjson(incomplete_path, tmp_path / "blocked-export")
    cli_db = tmp_path / "cli.sqlite3"
    cli_export = tmp_path / "cli-export"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "loopfactory.py"),
            "rollup",
            "--root",
            str(root),
            "--db",
            str(cli_db),
            "--export",
            str(cli_export),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert not cli_export.exists()


def test_missing_required_state_is_an_incomplete_rollup(tmp_path):
    root = tmp_path / "repo"
    _rollup_fixture(root)
    (root / "departments" / "beta" / "state" / "STATE.json").unlink()

    result = rollup.rebuild(root, tmp_path / "rollup.sqlite3")

    assert result["complete"] is False
    connection = sqlite3.connect(result["database"])
    try:
        assert connection.execute(
            "SELECT count(*) FROM incident WHERE code = 'source_missing'"
        ).fetchone() == (1,)
    finally:
        connection.close()
