from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "factory" / "decision_push_back.py"


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _run(
    decisions: Path, destination: Path, department: str = "podcast"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--decisions",
            str(decisions),
            "--department",
            department,
            "--dest",
            str(destination),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_only_named_department_crosses(tmp_path):
    decisions = tmp_path / "decisions.jsonl"
    destination = tmp_path / "returned.jsonl"
    wanted = {"department": "podcast", "packet_id": "p-1", "decision": "approve"}
    other = {"department": "sales", "packet_id": "s-1", "decision": "approve"}
    _write_rows(decisions, [wanted, other])

    result = _run(decisions, destination)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1 rows added, 1 rows skipped"
    assert _rows(destination) == [wanted]


def test_rows_without_packet_id_are_skipped_and_counted(tmp_path):
    decisions = tmp_path / "decisions.jsonl"
    destination = tmp_path / "returned.jsonl"
    valid = {"department": "podcast", "packet_id": "p-1", "decision": "skip"}
    _write_rows(
        decisions,
        [
            valid,
            {"department": "podcast", "decision": "approve"},
            {"department": "podcast", "packet_id": "", "decision": "fix"},
        ],
    )

    result = _run(decisions, destination)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1 rows added, 2 rows skipped"
    assert _rows(destination) == [valid]


def test_second_run_is_idempotent(tmp_path):
    decisions = tmp_path / "decisions.jsonl"
    destination = tmp_path / "returned.jsonl"
    row = {"department": "podcast", "packet_id": "p-1", "decision": "approve"}
    _write_rows(decisions, [row])

    assert _run(decisions, destination).returncode == 0
    before = destination.read_bytes()
    second = _run(decisions, destination)

    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "0 rows added, 0 rows skipped"
    assert destination.read_bytes() == before


def test_new_decision_arrives_on_next_run(tmp_path):
    decisions = tmp_path / "decisions.jsonl"
    destination = tmp_path / "returned.jsonl"
    first = {"department": "podcast", "packet_id": "p-1", "decision": "fix"}
    second = {"department": "podcast", "packet_id": "p-1", "decision": "approve"}
    _write_rows(decisions, [first])
    assert _run(decisions, destination).returncode == 0
    _write_rows(decisions, [first, second])

    result = _run(decisions, destination)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1 rows added, 0 rows skipped"
    assert _rows(destination) == [first, second]


def test_fix_notes_survive_unchanged(tmp_path):
    decisions = tmp_path / "decisions.jsonl"
    destination = tmp_path / "returned.jsonl"
    fix = {
        "department": "podcast",
        "packet_id": "p-fix",
        "decision": "fix",
        "notes": "Use the booking sheet, not the CRM.\nKeep both lines.",
        "resume_hint": "podcast/escalation: repair source",
    }
    _write_rows(decisions, [fix])

    result = _run(decisions, destination)

    assert result.returncode == 0, result.stderr
    assert _rows(destination) == [fix]


def test_empty_selection_exits_zero_without_touching_destination(tmp_path):
    decisions = tmp_path / "decisions.jsonl"
    destination = tmp_path / "returned.jsonl"
    existing = {"department": "podcast", "packet_id": "old", "decision": "skip"}
    _write_rows(decisions, [{"department": "sales", "packet_id": "s-1"}])
    _write_rows(destination, [existing])
    before = destination.read_bytes()

    result = _run(decisions, destination)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0 rows added, 1 rows skipped"
    assert destination.read_bytes() == before


def test_missing_decisions_file_fails_without_truncating_destination(tmp_path):
    decisions = tmp_path / "missing.jsonl"
    destination = tmp_path / "returned.jsonl"
    existing = {"department": "podcast", "packet_id": "old", "decision": "approve"}
    _write_rows(destination, [existing])
    before = destination.read_bytes()

    result = _run(decisions, destination)

    assert result.returncode != 0
    assert "cannot read decisions file" in result.stderr
    assert destination.read_bytes() == before
