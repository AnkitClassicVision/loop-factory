from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "factory" / "outbox_listen.py"


def _reader(tmp_path: Path, *, exit_code: int = 0) -> tuple[Path, Path]:
    data = tmp_path / "reader_data.json"
    data.write_text("{}\n", encoding="utf-8")
    script = tmp_path / "reader.py"
    script.write_text(
        "import json, pathlib, sys\n"
        f"data=pathlib.Path({str(data)!r})\n"
        f"calls=pathlib.Path({str(tmp_path / 'reader_calls.jsonl')!r})\n"
        "with calls.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "payload=json.loads(data.read_text())\n"
        "print(json.dumps(payload.get(sys.argv[1], [])))\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return script, data


def _recorder(tmp_path: Path, name: str, *, exit_code: int = 0) -> Path:
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import json, pathlib, sys\n"
        f"calls=pathlib.Path({str(tmp_path / (name + '_calls.jsonl'))!r})\n"
        "with calls.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return script


def _ledger_row(row_hash: str, issue: str, **changes) -> dict:
    row = {
        "ts": "2026-07-28T12:00:00+00:00",
        "row_hash": row_hash,
        "department": "generic-label",
        "kind": "approval",
        "summary": "Review item",
        "card_identifier": issue,
        "card_url": f"https://example.test/{issue}",
        "status": "open",
    }
    row.update(changes)
    return row


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _config(
    tmp_path: Path,
    ledger: Path,
    reader: Path,
    closer: Path,
    ack: Path | None = None,
    **listener_changes,
) -> Path:
    listener = {
        "reader": [sys.executable, str(reader), "{issue}"],
        "closer": [sys.executable, str(closer), "{issue}", "{state}"],
        "close_enabled": True,
        "decisions_file": str(tmp_path / "decisions.jsonl"),
    }
    if ack is not None:
        listener["ack"] = [sys.executable, str(ack), "{issue}", "{body}"]
    listener.update(listener_changes)
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"ledger_file": str(ledger), "listener": listener}),
        encoding="utf-8",
    )
    return path


def _run(config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config), "--once", *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _calls(tmp_path: Path, name: str) -> list[list[str]]:
    return _rows(tmp_path / f"{name}_calls.jsonl")


def test_approve_records_decision_updates_ledger_closes_and_acks(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-1", "ANK-101")])
    reader, data = _reader(tmp_path)
    data.write_text(
        json.dumps(
            {
                "ANK-101": [
                    {
                        "body": "APPROVE\nProceed.",
                        "createdAt": "2026-07-28T13:00:00Z",
                    }
                ]
            }
        )
    )
    closer = _recorder(tmp_path, "closer")
    ack = _recorder(tmp_path, "ack")
    config = _config(tmp_path, ledger, reader, closer, ack)

    assert _run(config).returncode == 0

    decision = _rows(tmp_path / "decisions.jsonl")[0]
    assert decision["decision"] == "approve"
    assert decision["first_line"] == "APPROVE"
    assert decision["source"] == "linear-comment"
    assert _rows(ledger)[-1]["status"] == "decided:approve"
    assert _calls(tmp_path, "closer") == [["ANK-101", "Agent Done"]]
    assert _calls(tmp_path, "ack") == [
        [
            "ANK-101",
            "AGENT DONE: decision recorded (approve). This card's loop is closed.",
        ]
    ]


def test_skip_is_detected(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-skip", "ANK-102")])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-102": [{"body": "  SKIP because obsolete"}]}))
    closer = _recorder(tmp_path, "closer")

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0

    assert _rows(tmp_path / "decisions.jsonl")[0]["decision"] == "skip"
    assert _rows(ledger)[-1]["status"] == "decided:skip"


def test_agent_marker_approve_lookalike_is_ignored(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-agent", "ANK-103")])
    reader, data = _reader(tmp_path)
    data.write_text(
        json.dumps({"ANK-103": [{"body": "AGENT UPDATE: APPROVE plan after QA"}]})
    )
    closer = _recorder(tmp_path, "closer")

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0

    assert not (tmp_path / "decisions.jsonl").exists()
    assert len(_rows(ledger)) == 1
    assert _calls(tmp_path, "closer") == []


def test_newest_human_reply_wins_on_conflict(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-conflict", "ANK-104")])
    reader, data = _reader(tmp_path)
    data.write_text(
        json.dumps(
            {
                "ANK-104": [
                    {"body": "APPROVE", "createdAt": "2026-07-28T10:00:00Z"},
                    {"body": "SKIP", "createdAt": "2026-07-28T11:00:00Z"},
                ]
            }
        )
    )
    closer = _recorder(tmp_path, "closer")

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0

    assert _rows(tmp_path / "decisions.jsonl")[0]["decision"] == "skip"


def test_decided_card_is_not_reprocessed_on_second_tick(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-once", "ANK-105")])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-105": [{"body": "APPROVE"}]}))
    closer = _recorder(tmp_path, "closer")
    config = _config(tmp_path, ledger, reader, closer)

    assert _run(config).returncode == 0
    assert _run(config).returncode == 0

    assert len(_rows(tmp_path / "decisions.jsonl")) == 1
    assert len(_calls(tmp_path, "reader")) == 1
    assert len(_calls(tmp_path, "closer")) == 1


def test_reader_failure_leaves_card_open_and_returns_three(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-fail", "ANK-106")])
    reader, _ = _reader(tmp_path, exit_code=1)
    closer = _recorder(tmp_path, "closer")

    result = _run(_config(tmp_path, ledger, reader, closer))

    assert result.returncode == 3
    assert len(_rows(ledger)) == 1
    assert not (tmp_path / "decisions.jsonl").exists()
    assert _calls(tmp_path, "closer") == []


def test_dry_run_reports_decision_and_changes_no_decision_state(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    original = _ledger_row("hash-dry", "ANK-107")
    _write_rows(ledger, [original])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-107": [{"body": "APPROVE"}]}))
    closer = _recorder(tmp_path, "closer")
    ack = _recorder(tmp_path, "ack")

    result = _run(_config(tmp_path, ledger, reader, closer, ack), "--dry-run")

    assert result.returncode == 0
    assert "dry-run would record approve" in result.stderr
    assert _rows(ledger) == [original]
    assert not (tmp_path / "decisions.jsonl").exists()
    assert _calls(tmp_path, "closer") == []
    assert _calls(tmp_path, "ack") == []


def test_invalid_config_exits_two(tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text("ledger_file: ledger.jsonl\nlistener: nope\n")

    result = _run(config)

    assert result.returncode == 2
    assert "invalid config" in result.stderr


def test_decisions_file_remains_append_only_across_ticks(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-a", "ANK-108")])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-108": [{"body": "APPROVE"}]}))
    closer = _recorder(tmp_path, "closer")
    config = _config(tmp_path, ledger, reader, closer)
    assert _run(config).returncode == 0
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_ledger_row("hash-b", "ANK-109")) + "\n")
    data.write_text(json.dumps({"ANK-109": [{"body": "SKIP"}]}))

    assert _run(config).returncode == 0

    decisions = _rows(tmp_path / "decisions.jsonl")
    assert [row["card_identifier"] for row in decisions] == ["ANK-108", "ANK-109"]
    assert [row["decision"] for row in decisions] == ["approve", "skip"]


def test_fix_records_full_notes_stays_open_and_is_polled_again(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-fix", "ANK-110")])
    reader, data = _reader(tmp_path)
    body = "FIX: change the hook\nAsk a question.\n" + ("x" * 2100)
    data.write_text(json.dumps({"ANK-110": [{"body": body}]}))
    closer = _recorder(tmp_path, "closer")
    ack = _recorder(tmp_path, "ack")
    config = _config(tmp_path, ledger, reader, closer, ack)

    assert _run(config).returncode == 0
    decision = _rows(tmp_path / "decisions.jsonl")[0]
    assert decision["decision"] == "fix"
    assert decision["notes"] == body[:2000]
    assert len(decision["notes"]) == 2000
    assert _rows(ledger)[-1]["status"] == "fix_requested"
    assert _rows(ledger)[-1]["notes_hash"]
    assert _calls(tmp_path, "closer") == []
    assert _calls(tmp_path, "ack") == [[
        "ANK-110",
        "AGENT UPDATE: fix request recorded and routed. Reply APPROVE or SKIP "
        "after the revised payload lands.",
    ]]

    assert _run(config).returncode == 0
    assert len(_calls(tmp_path, "reader")) == 2
    assert len(_rows(tmp_path / "decisions.jsonl")) == 1
    assert len(_calls(tmp_path, "ack")) == 1


def test_bare_fix_then_different_fix_then_approve_closes(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-fixes", "ANK-111")])
    reader, data = _reader(tmp_path)
    closer = _recorder(tmp_path, "closer")
    ack = _recorder(tmp_path, "ack")
    config = _config(tmp_path, ledger, reader, closer, ack)

    data.write_text(json.dumps({"ANK-111": [{"body": "FIX\nFirst notes"}]}))
    assert _run(config).returncode == 0
    data.write_text(json.dumps({"ANK-111": [{"body": "FIX\nSecond notes"}]}))
    assert _run(config).returncode == 0
    data.write_text(json.dumps({"ANK-111": [{"body": "APPROVE revised payload"}]}))
    assert _run(config).returncode == 0

    decisions = _rows(tmp_path / "decisions.jsonl")
    assert [row["decision"] for row in decisions] == ["fix", "fix", "approve"]
    assert decisions[0]["first_line"] == "FIX"
    assert _rows(ledger)[-1]["status"] == "decided:approve"
    assert _calls(tmp_path, "closer") == [["ANK-111", "Agent Done"]]
    assert len(_calls(tmp_path, "ack")) == 3


def test_agent_marked_fix_is_ignored(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-agent-fix", "ANK-112")])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-112": [{"body": "AGENT UPDATE: FIX the thing"}]}))
    closer = _recorder(tmp_path, "closer")

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0
    assert not (tmp_path / "decisions.jsonl").exists()
    assert len(_rows(ledger)) == 1


def test_newest_fix_wins_over_approve_and_skip(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-newest-fix", "ANK-113")])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-113": [
        {"body": "APPROVE", "createdAt": "2026-07-28T10:00:00Z"},
        {"body": "SKIP", "createdAt": "2026-07-28T11:00:00Z"},
        {"body": "FIX: revise", "createdAt": "2026-07-28T12:00:00Z"},
    ]}))
    closer = _recorder(tmp_path, "closer")

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0
    assert _rows(tmp_path / "decisions.jsonl")[0]["decision"] == "fix"
    assert _calls(tmp_path, "closer") == []
