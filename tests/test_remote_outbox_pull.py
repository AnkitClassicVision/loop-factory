from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "factory" / "remote_outbox_pull.py"


def _row(name: str) -> str:
    return json.dumps({"decision_id": name, "packet": {"eli5": name}})


def _run(source: Path, destination: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--dest",
            str(destination),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_local_source_pull_preserves_order_and_skips_blank_lines(tmp_path):
    source = tmp_path / "remote.jsonl"
    destination = tmp_path / "mailroom.jsonl"
    source.write_text(f"{_row('one')}\n\n{_row('two')}\n", encoding="utf-8")

    result = _run(source, destination)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "2 rows added"
    assert destination.read_text(encoding="utf-8").splitlines() == [
        _row("one"),
        _row("two"),
    ]


def test_second_pull_is_idempotent(tmp_path):
    source = tmp_path / "remote.jsonl"
    destination = tmp_path / "mailroom.jsonl"
    source.write_text(_row("one") + "\n", encoding="utf-8")

    assert _run(source, destination).returncode == 0
    before = destination.read_bytes()
    second = _run(source, destination)

    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "0 rows added"
    assert destination.read_bytes() == before


def test_incremental_source_row_arrives_on_next_pull(tmp_path):
    source = tmp_path / "remote.jsonl"
    destination = tmp_path / "mailroom.jsonl"
    source.write_text(_row("one") + "\n", encoding="utf-8")
    assert _run(source, destination).returncode == 0

    with source.open("a", encoding="utf-8") as handle:
        handle.write(_row("two") + "\n")
    result = _run(source, destination)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1 rows added"
    assert destination.read_text(encoding="utf-8").splitlines() == [
        _row("one"),
        _row("two"),
    ]


def test_empty_source_is_quiet_success_and_does_not_touch_destination(tmp_path):
    source = tmp_path / "remote.jsonl"
    destination = tmp_path / "mailroom.jsonl"
    source.write_text("", encoding="utf-8")
    destination.write_text(_row("existing") + "\n", encoding="utf-8")
    before = destination.read_bytes()

    result = _run(source, destination)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0 rows added"
    assert destination.read_bytes() == before


def test_unreachable_source_fails_without_changing_destination(tmp_path):
    source = tmp_path / "missing.jsonl"
    destination = tmp_path / "mailroom.jsonl"
    destination.write_text(_row("existing") + "\n", encoding="utf-8")
    before = destination.read_bytes()

    result = _run(source, destination)

    assert result.returncode != 0
    assert "cannot read source" in result.stderr
    assert destination.read_bytes() == before


def test_malformed_source_line_is_refused_without_changing_destination(tmp_path):
    source = tmp_path / "remote.jsonl"
    destination = tmp_path / "mailroom.jsonl"
    source.write_text(_row("valid") + "\nnot-json\n", encoding="utf-8")
    destination.write_text(_row("existing") + "\n", encoding="utf-8")
    before = destination.read_bytes()

    result = _run(source, destination)

    assert result.returncode != 0
    assert "line 2 is invalid JSON" in result.stderr
    assert destination.read_bytes() == before
