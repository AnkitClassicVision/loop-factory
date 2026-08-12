from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "factory" / "reescalate.py"
FIXED_NOW = "2026-08-05T12:00:00+00:00"


def _row(**changes) -> dict:
    row = {
        "ts": "2000-01-01T00:00:00+00:00",
        "row_hash": "hash-1",
        "card_identifier": "ANK-1",
        "status": "open",
        "first_raised": "2000-01-01T00:00:00+00:00",
        "department": "mailroom",
        "urgency": "normal",
    }
    row.update(changes)
    return row


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _run(ledger: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--ledger", str(ledger), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _rows(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sender_config(tmp_path: Path, template: list[str]) -> tuple[Path, Path]:
    calls = tmp_path / "calls.jsonl"
    sender = tmp_path / "sender.py"
    sender.write_text(
        "import json, pathlib, sys\n"
        f"path = pathlib.Path({str(calls)!r})\n"
        "with path.open('a') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "reescalation": {
                    "sender": [sys.executable, str(sender), *template]
                }
            }
        ),
        encoding="utf-8",
    )
    return config, calls


def test_now_omitted_uses_current_utc_time(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_row()])

    result = _run(ledger, "--plan-only")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["due"][0]["card_identifier"] == "ANK-1"


def test_now_supplied_still_controls_cadence(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(
        ledger,
        [_row(first_raised="2026-08-04T12:00:00+00:00")],
    )

    result = _run(ledger, "--now", FIXED_NOW, "--plan-only")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["due"] == []


def test_sender_renders_department_and_first_raised(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    missing_context = _row(
        row_hash="hash-2",
        card_identifier="ANK-2",
        last_ping_at="2000-01-01T00:00:00+00:00",
        reescalation_count=1,
    )
    missing_context.pop("department")
    missing_context.pop("first_raised")
    _write_rows(ledger, [_row(), missing_context])
    config, calls = _sender_config(
        tmp_path,
        ["{card_identifier}", "{department}", "{first_raised}"],
    )

    result = _run(ledger, "--now", FIXED_NOW, "--config", str(config))

    assert result.returncode == 0, result.stderr
    assert _rows(calls) == [
        ["ANK-1", "mailroom", "2000-01-01T00:00:00+00:00"],
        ["ANK-2", "", ""],
    ]


def test_ping_advances_state_once_without_changing_status(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_row(status="fix_requested")])
    config, calls = _sender_config(
        tmp_path,
        ["{card_identifier}", "{reescalation_count}"],
    )

    first = _run(ledger, "--now", FIXED_NOW, "--config", str(config))
    second = _run(ledger, "--now", FIXED_NOW, "--config", str(config))

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["due"] == []
    assert _rows(calls) == [["ANK-1", "1"]]
    appended = _rows(ledger)[-1]
    assert appended["reescalation_count"] == 1
    assert appended["last_ping_at"] == FIXED_NOW
    assert appended["status"] == "fix_requested"
