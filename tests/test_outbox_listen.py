from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from factory import outbox_listen


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
    data.write_text(json.dumps({"ANK-102": [{"body": "  SKIP because obsolete", "createdAt": "2026-07-28T13:00:00Z"}]}))
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
                    {"body": "APPROVE", "createdAt": "2026-07-28T13:00:00Z"},
                    {"body": "SKIP", "createdAt": "2026-07-28T14:00:00Z"},
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
    data.write_text(json.dumps({"ANK-105": [{"body": "APPROVE", "createdAt": "2026-07-28T13:00:00Z"}]}))
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
    data.write_text(json.dumps({"ANK-107": [{"body": "APPROVE", "createdAt": "2026-07-28T13:00:00Z"}]}))
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
    data.write_text(json.dumps({"ANK-108": [{"body": "APPROVE", "createdAt": "2026-07-28T13:00:00Z"}]}))
    closer = _recorder(tmp_path, "closer")
    config = _config(tmp_path, ledger, reader, closer)
    assert _run(config).returncode == 0
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_ledger_row("hash-b", "ANK-109")) + "\n")
    data.write_text(json.dumps({"ANK-109": [{"body": "SKIP", "createdAt": "2026-07-28T13:00:00Z"}]}))

    assert _run(config).returncode == 0

    decisions = _rows(tmp_path / "decisions.jsonl")
    assert [row["card_identifier"] for row in decisions] == ["ANK-108", "ANK-109"]
    assert [row["decision"] for row in decisions] == ["approve", "skip"]


def test_fix_records_full_notes_stays_open_and_is_polled_again(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-fix", "ANK-110")])
    reader, data = _reader(tmp_path)
    body = "FIX: change the hook\nAsk a question.\n" + ("x" * 2100)
    data.write_text(json.dumps({"ANK-110": [{"body": body, "createdAt": "2026-07-28T13:00:00Z"}]}))
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
    # Owner decision 2026-08-05: a FIX parks the card under the human owner in
    # Agent Needs Input. It does NOT close, so the re-escalation keeps working.
    assert _calls(tmp_path, "closer") == [["ANK-110", "Agent Needs Input"]]
    assert len(_calls(tmp_path, "ack")) == 1
    ack_body = _calls(tmp_path, "ack")[0][1]
    assert ack_body.startswith("AGENT UPDATE:")
    assert "owned by ankit" in ack_body
    assert "Agent Needs Input" in ack_body

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

    data.write_text(json.dumps({"ANK-111": [{"body": "FIX\nFirst notes", "createdAt": "2026-07-28T13:00:00Z"}]}))
    assert _run(config).returncode == 0
    data.write_text(json.dumps({"ANK-111": [{"body": "FIX\nSecond notes", "createdAt": "2026-07-28T14:00:00Z"}]}))
    assert _run(config).returncode == 0
    data.write_text(json.dumps({"ANK-111": [{"body": "APPROVE revised payload", "createdAt": "2026-07-28T15:00:00Z"}]}))
    assert _run(config).returncode == 0

    decisions = _rows(tmp_path / "decisions.jsonl")
    assert [row["decision"] for row in decisions] == ["fix", "fix", "approve"]
    assert decisions[0]["first_line"] == "FIX"
    assert _rows(ledger)[-1]["status"] == "decided:approve"
    # Each FIX parks the card under the owner; only the final APPROVE closes it.
    assert _calls(tmp_path, "closer") == [
        ["ANK-111", "Agent Needs Input"],
        ["ANK-111", "Agent Needs Input"],
        ["ANK-111", "Agent Done"],
    ]
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
    assert _calls(tmp_path, "closer") == [["ANK-113", "Agent Needs Input"]]


def test_one_reply_settles_every_ledger_row_sharing_a_card(tmp_path):
    """Production shape: 36 ledger rows collapsed onto 13 cards, ANK-293 alone
    carrying 13 of them. One human APPROVE must produce exactly ONE decision,
    poll the card ONCE, move state ONCE, and retire every row sharing the card."""
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(
        ledger,
        [
            _ledger_row("hash-a", "ANK-999"),
            _ledger_row("hash-b", "ANK-999"),
            _ledger_row("hash-c", "ANK-999"),
        ],
    )
    reader, data = _reader(tmp_path)
    data.write_text(
        json.dumps(
            {"ANK-999": [{"body": "APPROVE\nGo.", "createdAt": "2026-08-05T13:00:00Z"}]}
        )
    )
    closer = _recorder(tmp_path, "closer")
    ack = _recorder(tmp_path, "ack")
    config = _config(tmp_path, ledger, reader, closer, ack)

    assert _run(config).returncode == 0

    decisions = _rows(tmp_path / "decisions.jsonl")
    assert len(decisions) == 1
    assert decisions[0]["row_hashes"] == ["hash-a", "hash-b", "hash-c"]
    assert decisions[0]["row_hash"] == "hash-a"
    assert len(_calls(tmp_path, "reader")) == 1
    assert len(_calls(tmp_path, "closer")) == 1
    assert len(_calls(tmp_path, "ack")) == 1

    statuses = {row["row_hash"]: row["status"] for row in _rows(ledger)}
    assert statuses == {
        "hash-a": "decided:approve",
        "hash-b": "decided:approve",
        "hash-c": "decided:approve",
    }

    assert _run(config).returncode == 0
    assert len(_rows(tmp_path / "decisions.jsonl")) == 1
    assert len(_calls(tmp_path, "reader")) == 1


def test_reused_card_old_receipt_cannot_settle_new_row_but_newer_reply_can(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [
        _ledger_row("old", "ANK-REUSED", first_raised="2026-08-08T12:00:00Z"),
    ])
    reader, data = _reader(tmp_path)
    old_comment = {
        "id": "old-reply",
        "body": "APPROVE",
        "createdAt": "2026-08-08T13:00:00Z",
    }
    data.write_text(json.dumps({"ANK-REUSED": [old_comment]}))
    closer = _recorder(tmp_path, "closer")
    config = _config(tmp_path, ledger, reader, closer)

    assert _run(config).returncode == 0
    assert _rows(tmp_path / "decisions.jsonl")[0]["row_hashes"] == ["old"]

    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_ledger_row(
            "new", "ANK-REUSED", first_raised="2026-08-08T14:00:00Z"
        )) + "\n")
    before = ledger.read_bytes()
    assert _run(config).returncode == 0
    assert ledger.read_bytes() == before
    assert len(_rows(tmp_path / "decisions.jsonl")) == 1

    data.write_text(json.dumps({"ANK-REUSED": [old_comment, {
        "id": "new-reply",
        "body": "APPROVE",
        "createdAt": "2026-08-08T15:00:00Z",
    }]}))
    assert _run(config).returncode == 0
    receipts = _rows(tmp_path / "decisions.jsonl")
    assert len(receipts) == 2
    assert receipts[-1]["row_hashes"] == ["new"]
    assert _rows(ledger)[-1]["status"] == "decided:approve"


def test_decisions_require_aware_post_instance_timestamps(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [
        _ledger_row("missing", "ANK-MISSING", first_raised="2026-08-08T14:00:00Z"),
        _ledger_row("naive", "ANK-NAIVE", first_raised="2026-08-08T14:00:00Z"),
        _ledger_row("early", "ANK-EARLY", first_raised="2026-08-08T14:00:00Z"),
    ])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({
        "ANK-MISSING": [{"body": "APPROVE"}],
        "ANK-NAIVE": [{"body": "SKIP", "createdAt": "2026-08-08T15:00:00"}],
        "ANK-EARLY": [{"body": "FIX", "createdAt": "2026-08-08T13:00:00Z"}],
    }))
    closer = _recorder(tmp_path, "closer")

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0
    assert [row["status"] for row in _rows(ledger)] == ["open", "open", "open"]
    assert not (tmp_path / "decisions.jsonl").exists()
    assert _calls(tmp_path, "closer") == []


def test_fix_decision_row_carries_everything_an_agent_needs_to_resume(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(
        ledger,
        [
            _ledger_row(
                "hash-resume",
                "ANK-777",
                department="podcast",
                kind="escalation",
                summary="Which guest manifest field is missing?",
                packet_text="Department: podcast\nKind: escalation\nmanifest_unknown",
                first_raised="2026-08-01T09:00:00+00:00",
            )
        ],
    )
    reader, data = _reader(tmp_path)
    data.write_text(
        json.dumps(
            {"ANK-777": [{"body": "FIX-NOTES: use the booking sheet, not the CRM", "createdAt": "2026-08-01T10:00:00Z"}]}
        )
    )
    closer = _recorder(tmp_path, "closer")
    config = _config(tmp_path, ledger, reader, closer)

    assert _run(config).returncode == 0
    decision = _rows(tmp_path / "decisions.jsonl")[0]
    assert decision["decision"] == "fix"
    assert decision["owner"] == "ankit"
    assert decision["notes"] == "FIX-NOTES: use the booking sheet, not the CRM"
    assert (
        decision["resume_hint"]
        == "podcast/escalation: Which guest manifest field is missing?"
    )
    assert (
        decision["packet_text"]
        == "Department: podcast\nKind: escalation\nmanifest_unknown"
    )
    assert decision["first_raised"] == "2026-08-01T09:00:00+00:00"
    assert decision["card_url"] == "https://example.test/ANK-777"


def test_fyi_actions_settle_each_row_by_its_own_first_raised(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [
        _ledger_row("old", "ANK-FYI", action_mode="fyi", fyi_only=True, first_raised="2026-08-08T12:00:00Z"),
        _ledger_row("future", "ANK-FYI", action_mode="fyi", fyi_only=True, first_raised="2026-08-08T14:00:00Z"),
    ])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-FYI": [{"body": "ACKNOWLEDGE", "createdAt": "2026-08-08T13:00:00Z"}]}))
    closer = _recorder(tmp_path, "closer")
    ack = _recorder(tmp_path, "ack")

    assert _run(_config(tmp_path, ledger, reader, closer, ack)).returncode == 0
    latest = {row["row_hash"]: row for row in _rows(ledger)}
    assert latest["old"]["status"] == "acknowledged"
    assert latest["future"]["status"] == "open"
    receipt = _rows(tmp_path / "decisions.jsonl")[0]
    assert (receipt["action_mode"], receipt["row_hash"]) == ("fyi", "old")
    assert _calls(tmp_path, "closer") == []
    assert _calls(tmp_path, "ack")[0][1].startswith("AGENT UPDATE:")


def test_fyi_rejects_decision_grammar_and_bad_timestamps(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [
        _ledger_row(name, f"ANK-{name}", action_mode="fyi", fyi_only=True, first_raised="2026-08-08T12:00:00Z")
        for name in ("APPROVE", "MISSING", "NAIVE")
    ])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({
        "ANK-APPROVE": [{"body": "APPROVE", "createdAt": "2026-08-08T13:00:00Z"}],
        "ANK-MISSING": [{"body": "ACKNOWLEDGE"}],
        "ANK-NAIVE": [{"body": "RETIRE", "createdAt": "2026-08-08T13:00:00"}],
    }))
    closer = _recorder(tmp_path, "closer")

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0
    assert len(_rows(ledger)) == 3
    assert not (tmp_path / "decisions.jsonl").exists()


def test_fyi_snooze_is_exact_utc_and_acknowledge_does_not_settle_legacy(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [
        _ledger_row("fyi", "ANK-SNOOZE", action_mode="fyi", fyi_only=True, first_raised="2026-08-08T12:00:00Z"),
        _ledger_row("legacy", "ANK-LEGACY", first_raised="2026-08-08T12:00:00Z"),
    ])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({
        "ANK-SNOOZE": [{"body": "SNOOZE 24H", "createdAt": "2026-08-08T09:30:00-04:00"}],
        "ANK-LEGACY": [{"body": "ACKNOWLEDGE", "createdAt": "2026-08-08T14:00:00Z"}],
    }))
    closer = _recorder(tmp_path, "closer")

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0
    latest = {row["row_hash"]: row for row in _rows(ledger)}
    assert latest["fyi"]["status"] == "snoozed"
    assert latest["fyi"]["snooze_until"] == "2026-08-09T13:30:00+00:00"
    assert latest["fyi"]["last_fyi_action_key"].startswith("sha256:")
    assert latest["fyi"]["last_fyi_action_at"] == "2026-08-08T13:30:00+00:00"
    assert latest["legacy"]["status"] == "open"
    assert _calls(tmp_path, "closer") == []


def test_fyi_cursor_blocks_replay_after_wake_but_allows_newer_action(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [
        _ledger_row("fyi", "ANK-FYI", action_mode="fyi", fyi_only=True, first_raised="2026-08-08T12:00:00Z")
    ])
    reader, data = _reader(tmp_path)
    snooze = {"id": "comment-1", "body": "SNOOZE 24H", "createdAt": "2026-08-08T13:00:00Z"}
    data.write_text(json.dumps({"ANK-FYI": [snooze]}))
    closer = _recorder(tmp_path, "closer")
    config = _config(tmp_path, ledger, reader, closer)

    assert _run(config).returncode == 0
    snoozed = _rows(ledger)[-1]
    _write_rows(ledger, _rows(ledger) + [{**snoozed, "ts": "2026-08-09T13:00:00Z", "status": "open"}])
    before = ledger.read_bytes()
    assert _run(config).returncode == 0
    assert ledger.read_bytes() == before
    assert _rows(ledger)[-1]["status"] == "open"

    data.write_text(json.dumps({"ANK-FYI": [snooze, {"id": "comment-2", "body": "RETIRE", "createdAt": "2026-08-08T14:00:00Z"}]}))
    assert _run(config).returncode == 0
    assert _rows(ledger)[-1]["status"] == "retired"
    assert _rows(ledger)[-1]["last_fyi_action_key"] == "comment:comment-2"


def test_fyi_cursor_allows_different_comment_id_at_same_timestamp(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row(
        "fyi", "ANK-FYI", action_mode="fyi", fyi_only=True,
        first_raised="2026-08-08T12:00:00Z", status="open",
        last_fyi_action_key="comment:comment-1",
        last_fyi_action_at="2026-08-08T13:00:00+00:00",
    )])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-FYI": [
        {"id": "comment-1", "body": "SNOOZE 24H", "createdAt": "2026-08-08T13:00:00Z"},
        {"id": "comment-2", "body": "ACKNOWLEDGE", "createdAt": "2026-08-08T13:00:00Z"},
    ]}))
    closer = _recorder(tmp_path, "closer")

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0
    assert _rows(ledger)[-1]["status"] == "acknowledged"
    assert _rows(ledger)[-1]["last_fyi_action_key"] == "comment:comment-2"


def test_fyi_invalid_stored_cursor_fails_closed(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row(
        "fyi", "ANK-FYI", action_mode="fyi", fyi_only=True,
        first_raised="2026-08-08T12:00:00Z", last_fyi_action_key="comment:old",
    )])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-FYI": [
        {"id": "comment-new", "body": "RETIRE", "createdAt": "2026-08-08T14:00:00Z"}
    ]}))
    closer = _recorder(tmp_path, "closer")

    result = _run(_config(tmp_path, ledger, reader, closer))
    assert result.returncode == 0
    assert len(_rows(ledger)) == 1
    assert "invalid replay cursor" in result.stderr


def test_grouped_decision_retry_reuses_receipt_after_partial_ledger_failure(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [
        _ledger_row("hash-a", "ANK-RETRY"),
        _ledger_row("hash-b", "ANK-RETRY"),
        _ledger_row("hash-c", "ANK-RETRY"),
    ])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-RETRY": [{
        "id": "comment-retry", "body": "APPROVE", "createdAt": "2026-08-08T13:00:00Z"
    }]}))
    closer = _recorder(tmp_path, "closer")
    config = outbox_listen.load_config(_config(tmp_path, ledger, reader, closer))
    original_append = outbox_listen._append_jsonl
    ledger_appends = 0

    def fail_middle(path, row):
        nonlocal ledger_appends
        if Path(path) == ledger:
            ledger_appends += 1
            if ledger_appends == 2:
                raise OSError("injected middle transition failure")
        original_append(path, row)

    monkeypatch.setattr(outbox_listen, "_append_jsonl", fail_middle)
    assert outbox_listen.tick(config) == 0
    monkeypatch.setattr(outbox_listen, "_append_jsonl", original_append)
    assert outbox_listen.tick(config) == 0

    receipts = _rows(tmp_path / "decisions.jsonl")
    assert len(receipts) == 1
    assert receipts[0]["source_action_key"] == "ANK-RETRY:comment:comment-retry"
    final_rows = [row for row in _rows(ledger) if row["status"] == "decided:approve"]
    assert sorted(row["row_hash"] for row in final_rows) == ["hash-a", "hash-b", "hash-c"]


def test_fyi_retry_reuses_receipt_and_completes_cursor_transition(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row(
        "fyi-retry", "ANK-FYI-RETRY", action_mode="fyi", fyi_only=True,
        first_raised="2026-08-08T12:00:00Z",
    )])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-FYI-RETRY": [{
        "id": "fyi-comment", "body": "ACKNOWLEDGE", "createdAt": "2026-08-08T13:00:00Z"
    }]}))
    closer = _recorder(tmp_path, "closer")
    config = outbox_listen.load_config(_config(tmp_path, ledger, reader, closer))
    original_append = outbox_listen._append_jsonl
    failed = False

    def fail_first_ledger(path, row):
        nonlocal failed
        if Path(path) == ledger and not failed:
            failed = True
            raise OSError("injected first transition failure")
        original_append(path, row)

    monkeypatch.setattr(outbox_listen, "_append_jsonl", fail_first_ledger)
    assert outbox_listen.tick(config) == 0
    monkeypatch.setattr(outbox_listen, "_append_jsonl", original_append)
    assert outbox_listen.tick(config) == 0

    assert len(_rows(tmp_path / "decisions.jsonl")) == 1
    final = _rows(ledger)[-1]
    assert final["status"] == "acknowledged"
    assert final["last_fyi_action_key"] == "comment:fyi-comment"


def test_malformed_receipt_line_blocks_listener_replay(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-bad-receipt", "ANK-BAD-RECEIPT")])
    reader, _ = _reader(tmp_path)
    closer = _recorder(tmp_path, "closer")
    config_path = _config(tmp_path, ledger, reader, closer)
    (tmp_path / "decisions.jsonl").write_text("{broken\n", encoding="utf-8")

    result = _run(config_path)
    assert result.returncode == 3
    assert len(_rows(ledger)) == 1
    assert "invalid JSON" in result.stderr


def test_invalid_keyed_receipts_block_listener_before_all_effects(tmp_path):
    invalid_receipts = [
        {"card_identifier": "ANK-INVALID-RECEIPT", "action_mode": "unknown-mode", "decision": "approve", "row_hash": "hash-a"},
        {"action_mode": "decision", "decision": "approve", "row_hash": "hash-a"},
        {"card_identifier": "ANK-INVALID-RECEIPT", "action_mode": "decision", "decision": "acknowledge", "row_hash": "hash-a"},
        {"card_identifier": "ANK-INVALID-RECEIPT", "action_mode": "fyi", "decision": "approve", "row_hash": "hash-a"},
        {"card_identifier": "ANK-INVALID-RECEIPT", "action_mode": "decision", "decision": "approve"},
        {"card_identifier": "ANK-INVALID-RECEIPT", "action_mode": "decision", "decision": "approve", "row_hashes": [""]},
        {"card_identifier": "  \t", "action_mode": "decision", "decision": "approve", "row_hash": "hash-a"},
        {"card_identifier": "ANK-INVALID-RECEIPT", "action_mode": "decision", "decision": "approve", "row_hash": "  \t"},
        {"card_identifier": "ANK-INVALID-RECEIPT", "action_mode": "decision", "decision": "approve", "row_hashes": ["hash-a", "  \t"]},
        {"card_identifier": "ANK-INVALID-RECEIPT", "action_mode": "decision", "decision": "approve", "row_hash": "hash-a", "row_hashes": ["hash-b"]},
    ]
    for index, receipt_fields in enumerate(invalid_receipts):
        case = tmp_path / str(index)
        case.mkdir()
        ledger = case / "ledger.jsonl"
        _write_rows(ledger, [_ledger_row("hash-a", "ANK-INVALID-RECEIPT")])
        reader, _ = _reader(case)
        closer = _recorder(case, "closer")
        ack = _recorder(case, "ack")
        config = _config(case, ledger, reader, closer, ack)
        _write_rows(case / "decisions.jsonl", [{
            "source_action_key": f"ANK-INVALID-RECEIPT:comment:{index}",
            **receipt_fields,
        }])
        before = ledger.read_bytes()

        result = _run(config)

        assert result.returncode == 3
        assert ledger.read_bytes() == before
        assert _calls(case, "reader") == []
        assert _calls(case, "closer") == []
        assert _calls(case, "ack") == []
        assert "invalid keyed receipt fields" in result.stderr


def test_valid_legacy_decision_receipt_replays_with_row_hash_only(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-legacy", "ANK-LEGACY-RECEIPT")])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-LEGACY-RECEIPT": [{
        "id": "comment-legacy", "body": "APPROVE", "createdAt": "2026-08-08T13:00:00Z"
    }]}))
    closer = _recorder(tmp_path, "closer")
    action_key = "ANK-LEGACY-RECEIPT:comment:comment-legacy"
    _write_rows(tmp_path / "decisions.jsonl", [{
        "source_action_key": action_key,
        "card_identifier": "ANK-LEGACY-RECEIPT",
        "decision": "approve",
        "row_hash": "hash-legacy",
    }])

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0

    assert _rows(ledger)[-1]["status"] == "decided:approve"
    assert _calls(tmp_path, "closer") == [["ANK-LEGACY-RECEIPT", "Agent Done"]]


def test_compatibility_receipt_uses_canonical_trimmed_hashes(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-compat", "ANK-COMPAT-RECEIPT")])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-COMPAT-RECEIPT": [{
        "id": "comment-compat", "body": "APPROVE", "createdAt": "2026-08-08T13:00:00Z"
    }]}))
    closer = _recorder(tmp_path, "closer")
    action_key = "ANK-COMPAT-RECEIPT:comment:comment-compat"
    _write_rows(tmp_path / "decisions.jsonl", [{
        "source_action_key": action_key,
        "card_identifier": "  ANK-COMPAT-RECEIPT  ",
        "action_mode": "decision",
        "decision": "approve",
        "row_hash": "  hash-compat  ",
        "row_hashes": ["  hash-compat  "],
    }])

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0

    indexed = outbox_listen._receipt_index(tmp_path / "decisions.jsonl")
    assert indexed == {
        action_key: [{
            "source_action_key": action_key,
            "card_identifier": "ANK-COMPAT-RECEIPT",
            "action_mode": "decision",
            "decision": "approve",
            "row_hash": "hash-compat",
            "row_hashes": ["hash-compat"],
        }]
    }
    assert _rows(ledger)[-1]["status"] == "decided:approve"


def test_valid_fyi_receipt_replays(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row(
        "hash-fyi", "ANK-FYI-RECEIPT", action_mode="fyi", fyi_only=True,
        first_raised="2026-08-08T12:00:00Z",
    )])
    reader, data = _reader(tmp_path)
    data.write_text(json.dumps({"ANK-FYI-RECEIPT": [{
        "id": "comment-fyi", "body": "ACKNOWLEDGE", "createdAt": "2026-08-08T13:00:00Z"
    }]}))
    closer = _recorder(tmp_path, "closer")
    _write_rows(tmp_path / "decisions.jsonl", [{
        "source_action_key": "ANK-FYI-RECEIPT:comment:comment-fyi",
        "card_identifier": "ANK-FYI-RECEIPT",
        "action_mode": "fyi",
        "decision": "acknowledge",
        "row_hash": "hash-fyi",
    }])

    assert _run(_config(tmp_path, ledger, reader, closer)).returncode == 0

    assert _rows(ledger)[-1]["status"] == "acknowledged"


def test_conflicting_duplicate_receipts_fail_before_listener_effects(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("new", "ANK-RECEIPT-CONFLICT")])
    reader, data = _reader(tmp_path)
    comment = {
        "id": "comment-conflict",
        "body": "APPROVE",
        "createdAt": "2026-08-08T13:00:00Z",
    }
    data.write_text(json.dumps({"ANK-RECEIPT-CONFLICT": [comment]}))
    closer = _recorder(tmp_path, "closer")
    ack = _recorder(tmp_path, "ack")
    config = _config(tmp_path, ledger, reader, closer, ack)
    action_key = "ANK-RECEIPT-CONFLICT:comment:comment-conflict"
    _write_rows(tmp_path / "decisions.jsonl", [
        {
            "source_action_key": action_key,
            "card_identifier": "ANK-RECEIPT-CONFLICT",
            "action_mode": "decision",
            "decision": "approve",
            "row_hash": "old",
            "row_hashes": ["old"],
        },
        {
            "source_action_key": action_key,
            "card_identifier": "ANK-RECEIPT-CONFLICT",
            "action_mode": "decision",
            "decision": "approve",
            "row_hash": "old",
            "row_hashes": ["old", "new"],
        },
    ])
    before = ledger.read_bytes()

    result = _run(config)

    assert result.returncode == 3
    assert ledger.read_bytes() == before
    assert _calls(tmp_path, "reader") == []
    assert _calls(tmp_path, "closer") == []
    assert _calls(tmp_path, "ack") == []
    assert "duplicate decision receipts" in result.stderr


def test_identical_duplicate_receipts_replay_safely(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_ledger_row("hash-a", "ANK-RECEIPT-DUPLICATE")])
    reader, data = _reader(tmp_path)
    comment = {
        "id": "comment-duplicate",
        "body": "APPROVE",
        "createdAt": "2026-08-08T13:00:00Z",
    }
    data.write_text(json.dumps({"ANK-RECEIPT-DUPLICATE": [comment]}))
    closer = _recorder(tmp_path, "closer")
    config = _config(tmp_path, ledger, reader, closer)
    action_key = "ANK-RECEIPT-DUPLICATE:comment:comment-duplicate"
    receipt = {
        "source_action_key": action_key,
        "card_identifier": "ANK-RECEIPT-DUPLICATE",
        "action_mode": "decision",
        "decision": "approve",
        "row_hash": "hash-a",
        "row_hashes": ["hash-a"],
    }
    _write_rows(tmp_path / "decisions.jsonl", [receipt, dict(receipt)])

    assert _run(config).returncode == 0

    assert len(_rows(tmp_path / "decisions.jsonl")) == 2
    assert _rows(ledger)[-1]["status"] == "decided:approve"
    assert _calls(tmp_path, "closer") == [["ANK-RECEIPT-DUPLICATE", "Agent Done"]]
