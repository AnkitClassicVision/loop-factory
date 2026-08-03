from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory import rollup
from factory.boardfeed import build_feed
from factory.external_departments import MIRROR_SCHEMA, refresh
from factory.runrecord import build_record


NOW = "2026-08-02T20:00:00+00:00"
NAME = "sales"
OBJECTIVE = "invented_contacts_reviewed"


def _config(repo: Path, source: Path) -> Path:
    path = repo / "estate" / "external_departments.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"- name: {NAME}",
                f"  root: {source}",
                f"  state: {source / 'state'}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _source(tmp_path: Path, *, complete: bool = True) -> tuple[Path, Path, Path]:
    repo = tmp_path / "factory-repo"
    source = tmp_path / "invented-external" / NAME
    state = source / "state"
    state.mkdir(parents=True)
    _config(repo, source)
    if complete:
        (source / "charter.yaml").write_text(
            f"""department: {NAME}
owner: fixture-owner
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [autonomy_state]
setpoints:
  objectives:
    {OBJECTIVE}:
      label: Invented contacts reviewed
      setpoint: 10
      minimum: 5
      target: 10
      unit: contacts
""",
            encoding="utf-8",
        )
        (state / "STATE.json").write_text(
            json.dumps(
                {
                    "department": NAME,
                    "epoch": 3,
                    "last_cycle_at": "2026-08-02T19:30:00+00:00",
                    "autonomy_state": "shadow",
                    "open_findings": [],
                    "escalations": 1,
                }
            ),
            encoding="utf-8",
        )
        (state / "heartbeats.jsonl").write_text(
            json.dumps({"ts": "2026-08-02T19:31:00+00:00", "ok": True}) + "\n",
            encoding="utf-8",
        )
        (state / "objectives_observed.json").write_text(
            json.dumps(
                {
                    "schema": "objectives-observed/v1",
                    "ts": "2026-08-02T19:40:00+00:00",
                    "values": {OBJECTIVE: 3},
                }
            ),
            encoding="utf-8",
        )
    return repo, source, state


def _record(*, department: str = NAME, run_id: str = "invented-contact-run") -> dict:
    return build_record(
        schema="run-record/v2",
        rev=2,
        run_id=run_id,
        department=department,
        node="review_invented_contacts",
        epoch=3,
        ts="2026-08-02T19:45:00+00:00",
        attempt=1,
        round=None,
        release={"hash": "fixture-hash", "source_ref": "fixture"},
        trigger={"kind": "manual", "id": "fixture", "dedupe_key": run_id},
        engine="codex",
        model="fixture-model",
        auth_class="oauth_cli",
        usage={
            "input_tokens": 8,
            "output_tokens": 4,
            "cache_read": 0,
            "cache_creation": 0,
        },
        cost={"lane": "flat_subscription", "model_calls": 1},
        duration_ms=20,
        status="ok",
        errors=[],
        artifacts=[{"fixture": "Invented Contact Alpha"}],
        receipts=[{"kind": "fixture"}],
        evaluator=None,
        approval=None,
        external_actions_taken=0,
    )


def _write_runs(state: Path, *rows: object) -> None:
    (state / "runs-v2.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _mirror(repo: Path) -> dict:
    return json.loads(
        (repo / "estate" / "state" / "external" / NAME / "mirror.json").read_text(
            encoding="utf-8"
        )
    )


def _receipt(repo: Path) -> dict:
    return json.loads(
        (
            repo
            / "estate"
            / "state"
            / "external"
            / NAME
            / "refresh-receipt.json"
        ).read_text(encoding="utf-8")
    )


def test_harvest_mirrors_only_validated_run_rows_and_counts_invalid(tmp_path):
    repo, _, state = _source(tmp_path)
    _write_runs(state, _record(), {"schema": "not-a-run"}, "not-an-object")

    result = refresh(repo)

    mirror = _mirror(repo)
    receipt = _receipt(repo)
    assert mirror["schema"] == MIRROR_SCHEMA
    assert [row["run_id"] for row in mirror["runs"]] == ["invented-contact-run"]
    assert receipt["valid_runs"] == 1
    assert receipt["invalid_runs"] == 2
    assert result["invalid"] == 2


def test_missing_source_files_are_mirrored_as_unknown_without_failure(tmp_path):
    repo, _, _ = _source(tmp_path, complete=False)

    result = refresh(repo)

    mirror = _mirror(repo)
    assert result["mirrored"] == 1
    assert mirror["state"] is None
    assert mirror["heartbeat"] is None
    assert mirror["objectives_observed"] is None
    assert mirror["display"] is None
    assert mirror["runs"] == []
    assert set(mirror["source_health"]["absent"]) == {
        "charter",
        "heartbeats",
        "objectives_observed",
        "runs_v2",
        "state",
    }


def test_config_absent_is_noop_and_creates_no_external_state(tmp_path):
    repo = tmp_path / "factory-repo"

    result = refresh(repo)

    assert result["configured"] == 0
    assert result["mirrored"] == 0
    assert not (repo / "estate" / "state" / "external").exists()


def test_refresh_never_changes_any_external_source_file(tmp_path):
    repo, source, state = _source(tmp_path)
    _write_runs(state, _record())
    before = {
        path.relative_to(source): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(source.rglob("*"))
        if path.is_file()
    }

    refresh(repo)

    after = {
        path.relative_to(source): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(source.rglob("*"))
        if path.is_file()
    }
    assert after == before


def test_refresh_refuses_output_symlink_that_would_escape_repo(tmp_path):
    repo, _, _ = _source(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    state_root = repo / "estate" / "state"
    state_root.mkdir(parents=True)
    (state_root / "external").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="unsafe external mirror output path"):
        refresh(repo)

    assert list(outside.iterdir()) == []


def test_refresh_is_byte_deterministic_and_leaves_no_temp_files(tmp_path):
    repo, _, state = _source(tmp_path)
    _write_runs(state, _record(run_id="run-b"), _record(run_id="run-a"))

    refresh(repo)
    target = repo / "estate" / "state" / "external" / NAME
    first = {path.name: path.read_bytes() for path in sorted(target.iterdir())}
    refresh(repo)
    second = {path.name: path.read_bytes() for path in sorted(target.iterdir())}

    assert first == second
    assert set(second) == {"mirror.json", "refresh-receipt.json"}


def test_config_name_owns_identity_and_mismatched_run_is_invalid(tmp_path):
    repo, _, state = _source(tmp_path)
    _write_runs(state, _record(department="different-name"))

    refresh(repo)

    assert _mirror(repo)["name"] == NAME
    assert _mirror(repo)["runs"] == []
    assert _receipt(repo)["invalid_runs"] == 1


def test_invalid_optional_sources_are_counted_not_crashed(tmp_path):
    repo, source, state = _source(tmp_path)
    (state / "STATE.json").write_text("[]", encoding="utf-8")
    (state / "heartbeats.jsonl").write_text("{bad-json}\n", encoding="utf-8")
    (state / "objectives_observed.json").write_text("{}", encoding="utf-8")
    (source / "charter.yaml").write_text("department: broken\n", encoding="utf-8")

    result = refresh(repo)

    assert result["invalid"] == 4
    assert _mirror(repo)["state"] is None
    assert _mirror(repo)["heartbeat"] is None
    assert _mirror(repo)["objectives_observed"] is None
    assert _mirror(repo)["display"] is None


def test_boardfeed_renders_external_status_run_objective_and_breach_andon(tmp_path):
    repo, _, state = _source(tmp_path)
    _write_runs(state, _record())
    rollup_result = rollup.rebuild(repo)
    assert rollup_result["complete"] is True
    refresh(repo)

    output = repo / "feed.ndjson"
    receipt = build_feed(repo, out=output, now=NOW)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    external = [row for row in rows if row["department"] == NAME]

    assert receipt["departments"] == 1
    assert any(row["kind"] == "dept_status" for row in external)
    assert any(
        row["kind"] == "active_run"
        and row["data"]["run_id"] == "invented-contact-run"
        for row in external
    )
    objective = next(
        row
        for row in external
        if row["kind"] == "metrics"
        and row["data"].get("metric_type") == "objective"
    )
    assert objective["data"]["observed"] == 3
    assert objective["data"]["minimum"] == 5
    breach = next(
        row
        for row in external
        if row["kind"] == "andon"
        and row["data"].get("code") == "OBJECTIVE_BELOW_MIN"
    )
    assert breach["data"]["detail"] == (
        "Invented contacts reviewed: observed 3 below minimum 5"
    )


def test_boardfeed_does_not_fabricate_configured_department_without_mirror(tmp_path):
    repo, _, _ = _source(tmp_path)
    result = rollup.rebuild(repo)
    assert result["complete"] is True
    output = repo / "feed.ndjson"

    receipt = build_feed(repo, out=output, now=NOW)
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert receipt["departments"] == 0
    assert not [row for row in rows if row["department"] == NAME]


def test_service_refreshes_external_mirrors_before_boardfeed():
    service = (
        Path(__file__).resolve().parents[1]
        / "templates"
        / "systemd"
        / "estate-board.service"
    ).read_text(encoding="utf-8")

    refresh_line = "ExecStart=/usr/bin/python3 -m factory.external_departments --repo-root ."
    boardfeed_line = "ExecStart=/usr/bin/python3 -m factory.boardfeed --repo-root ."
    assert refresh_line in service
    assert service.index(refresh_line) < service.index(boardfeed_line)
