"""Regression proof for the podcast daily heal-chain wiring."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from departments.podcast.runtime import heal_apply, heal_select, heal_verify
from factory import runrecord


REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_SCRIPT = REPO_ROOT / "departments/podcast/runtime/podcast_daily.sh"
NOW = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)


def _script_lines() -> list[str]:
    return DAILY_SCRIPT.read_text(encoding="utf-8").splitlines()


def _line_index(lines: list[str], needle: str) -> int:
    return next(index for index, line in enumerate(lines) if needle in line)


def _write_incident(state_dir: Path, fingerprint: str = "fp-heal") -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "incidents.json").write_text(
        json.dumps(
            {
                fingerprint: {
                    "fingerprint": fingerprint,
                    "state": "open",
                    "failure_class": "timer_failed",
                    "evidence": ["systemd://podcast-fixture.timer"],
                    "setpoint": "timer healthy",
                }
            }
        ),
        encoding="utf-8",
    )


def _records(state_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (state_dir / "runs-v2.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]


def _assert_one_valid_record(state_dir: Path, node: str, status: str = "ok") -> dict:
    records = _records(state_dir)
    assert len(records) == 1
    record = records[0]
    assert runrecord.validate_record(record) == record
    assert record["department"] == "podcast"
    assert record["node"] == node
    assert record["status"] == status
    assert record["external_actions_taken"] == 0
    return record


def test_daily_script_launcher_wraps_heal_lane_in_order_after_manager_chain():
    lines = _script_lines()
    escalate = _line_index(lines, "runtime/escalate_outbox.py")
    manager = _line_index(lines, "factory/manager.py")
    approvals = _line_index(lines, "factory/human_in_the_loop.py")
    select = _line_index(lines, "runtime/heal_select.py")
    apply = _line_index(lines, "runtime/heal_apply.py")
    verify = _line_index(lines, "runtime/heal_verify.py")

    # The heal lane now lives in a run_heal_phase() function defined above the
    # chain, so EXECUTION order is proven via its call site, and step order is
    # proven within the function body.
    heal_call = max(i for i, l in enumerate(lines) if l.strip() == "run_heal_phase")
    assert escalate < manager < approvals < heal_call
    assert select < apply < verify
    # Audit fix round 1 (2026-08-03): heal steps are FAIL-CLOSED per incident.
    # The old pin asserted `|| true` on every heal invocation — enshrining the
    # allow-on-failure defect the audit flagged. The contract is now: no
    # suppression on the heal commands; a nonzero step appends a
    # manager-visible failure receipt and halts that incident's lane.
    for index in (select, apply, verify):
        assert 'factory/launch.py" --department "${DEPARTMENT}" -- python3' in lines[index]
        assert "--state-dir \"${STATE_DIR}\"" in lines[index]
        assert "--fingerprint \"${fingerprint}\"" in lines[index]
        assert "--shadow" in lines[index]
        assert "|| true" not in lines[index]
    assert "--playbook \"${playbook}\"" in lines[apply]
    script = "\n".join(lines)
    assert "append_heal_failure" in script
    assert "heal_failures.jsonl" in script
    assert "--playbook \"${playbook}\"" in lines[verify]


def test_daily_script_rotates_observations_at_5000_lines():
    line = next(
        line for line in _script_lines() if "runtime/rotate_observations.py" in line
    )

    assert 'factory/launch.py" --department "${DEPARTMENT}" -- python3' in line
    assert "--state-dir \"${STATE_DIR}\" --max-lines 5000" in line


def test_daily_script_regenerates_feed_estate_board_and_department_board():
    text = DAILY_SCRIPT.read_text(encoding="utf-8")

    assert 'python3 -m factory.boardfeed --repo-root "${REPO}"' in text
    assert (
        'python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" '
        '--out "${REPO}/estate/state/board.html"'
    ) in text
    assert (
        'python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" '
        '--department "${DEPARTMENT}" '
        '--out "${REPO}/estate/state/${DEPARTMENT}-board.html"'
    ) in text


def test_heal_select_happy_path_appends_one_valid_record(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    _write_incident(state_dir)

    selected = heal_select.run_select(state_dir, "fp-heal", now=NOW)

    assert selected is not None
    assert selected["id"] == "restart_user_timer"
    _assert_one_valid_record(state_dir, "heal_select")


def test_heal_apply_happy_path_appends_one_valid_record(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    _write_incident(state_dir)

    receipt = heal_apply.run_apply(
        state_dir,
        "fp-heal",
        "restart_user_timer",
        shadow=True,
        now=NOW,
    )

    assert receipt["result"] == "proposed"
    assert receipt["mode"] == "proposed"
    _assert_one_valid_record(state_dir, "heal_apply")


def test_heal_verify_happy_path_appends_one_valid_record(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    _write_incident(state_dir)

    receipt = heal_verify.run_verify(
        state_dir,
        "fp-heal",
        "restart_user_timer",
        prober=lambda _incident: (False, "fixture condition cleared"),
        shadow=True,
        now=NOW,
    )

    assert receipt["result"] == "verified"
    _assert_one_valid_record(state_dir, "heal_verify")


def test_heal_node_failure_records_error_and_exits_nonzero(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "heals.jsonl").mkdir()

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "departments/podcast/runtime/heal_select.py"),
            "--state-dir",
            str(state_dir),
            "--fingerprint",
            "fp-heal",
            "--shadow",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    record = _assert_one_valid_record(state_dir, "heal_select", status="error")
    assert record["errors"] == ["IsADirectoryError"]


def test_heal_records_carry_utc_date_node_dedupe_keys(tmp_path):
    state_dir = tmp_path / "podcast" / "state"
    _write_incident(state_dir)
    before = datetime.now(timezone.utc).date().isoformat()

    heal_select.run_select(state_dir, "fp-heal", now=NOW)
    heal_apply.run_apply(
        state_dir,
        "fp-heal",
        "restart_user_timer",
        shadow=True,
        now=NOW,
    )
    heal_verify.run_verify(
        state_dir,
        "fp-heal",
        "restart_user_timer",
        prober=lambda _incident: False,
        shadow=True,
        now=NOW,
    )

    after = datetime.now(timezone.utc).date().isoformat()
    records = _records(state_dir)
    assert len(records) == 3
    for record in records:
        node = record["node"]
        assert record["trigger"] == {
            "kind": "time",
            "id": "podcast-daily",
            "dedupe_key": record["trigger"]["dedupe_key"],
        }
        assert record["trigger"]["dedupe_key"] in {
            f"{before}-{node}",
            f"{after}-{node}",
        }


def test_heal_run_record_append_failure_is_fail_closed(tmp_path, monkeypatch):
    state_dir = tmp_path / "podcast" / "state"
    _write_incident(state_dir)

    def fail_append(_state_dir, _record):
        raise OSError("simulated append failure")

    monkeypatch.setattr(runrecord, "append_record", fail_append)
    with pytest.raises(OSError, match="simulated append failure"):
        heal_select.run_select(state_dir, "fp-heal", now=NOW)

    assert not (state_dir / "runs-v2.jsonl").exists()
