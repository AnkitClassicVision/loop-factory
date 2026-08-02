from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from factory.runrecord import append_record, build_record, new_run_id, validate_record


def _fields(**overrides):
    fields = {
        "schema": "run-record/v2",
        "rev": 2,
        "run_id": new_run_id(),
        "department": "example",
        "node": "collect",
        "epoch": 7,
        "ts": "2026-08-02T19:45:01+00:00",
        "attempt": 1,
        "round": None,
        "release": {"hash": "abc123", "source_ref": "local"},
        "trigger": {"kind": "manual", "id": "trigger-1", "dedupe_key": "key-1"},
        "engine": "codex",
        "model": "example-model",
        "auth_class": "oauth_cli",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read": 0,
            "cache_creation": 0,
        },
        "cost": {"lane": "flat_subscription", "model_calls": 1},
        "duration_ms": 25,
        "status": "ok",
        "errors": [],
        "artifacts": ["local-artifact.json"],
        "receipts": [{"kind": "local"}],
        "evaluator": None,
        "approval": None,
        "external_actions_taken": 0,
    }
    fields.update(overrides)
    return fields


def test_new_run_ids_are_unique_and_time_sortable():
    run_ids = [new_run_id() for _ in range(100)]

    assert len(set(run_ids)) == 100
    assert all(re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", value) for value in run_ids)
    assert [value[:16] for value in sorted(run_ids)] == sorted(
        value[:16] for value in run_ids
    )


def test_happy_path_build_append_and_reload_roundtrip(tmp_path):
    record = build_record(**_fields())

    path = append_record(tmp_path, record)
    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert loaded == record
    assert validate_record(loaded) == record


def test_bad_auth_class_names_field():
    with pytest.raises(ValueError, match="auth_class"):
        build_record(**_fields(auth_class="api_key"))


def test_bad_status_names_field():
    with pytest.raises(ValueError, match="status"):
        build_record(**_fields(status="finished"))


def test_bad_trigger_kind_names_field():
    trigger = {"kind": "webhook", "id": "trigger-1", "dedupe_key": "key-1"}
    with pytest.raises(ValueError, match=r"trigger\.kind"):
        build_record(**_fields(trigger=trigger))


def test_unknown_top_level_key_is_rejected():
    with pytest.raises(ValueError, match="prompt"):
        build_record(**_fields(prompt="must not be stored"))


def test_metered_forbidden_sets_violation_flag():
    record = build_record(
        **_fields(cost={"lane": "metered_forbidden", "model_calls": 1})
    )

    assert record["metered_violation"] is True
    assert validate_record(record) == record


def test_external_actions_taken_rejects_bool_and_other_non_ints():
    for bad_value in (False, "0", 0.0, None):
        with pytest.raises(ValueError, match="external_actions_taken"):
            build_record(**_fields(external_actions_taken=bad_value))


def test_concurrent_appends_produce_complete_json_lines(tmp_path):
    def write_batch(worker: int) -> None:
        for index in range(50):
            record = build_record(
                **_fields(run_id=f"worker-{worker}-{index}", epoch=index)
            )
            append_record(tmp_path, record)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write_batch, worker) for worker in range(2)]
        for future in futures:
            future.result()

    lines = (tmp_path / "runs-v2.jsonl").read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == 100
    assert len({record["run_id"] for record in records}) == 100
    assert all(validate_record(record) == record for record in records)


def test_append_creates_missing_parent_directory(tmp_path):
    state_dir = tmp_path / "missing" / "state"

    path = append_record(state_dir, build_record(**_fields()))

    assert path == state_dir / "runs-v2.jsonl"
    assert path.is_file()


def test_missing_required_field_names_field():
    fields = _fields()
    del fields["receipts"]

    with pytest.raises(ValueError, match="receipts"):
        build_record(**fields)


def test_bad_nested_cost_lane_names_field():
    with pytest.raises(ValueError, match=r"cost\.lane"):
        build_record(**_fields(cost={"lane": "api", "model_calls": 1}))
