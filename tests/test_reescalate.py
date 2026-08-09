from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from factory import reescalate


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "factory" / "reescalate.py"
NOW = "2026-08-05T12:00:00+00:00"


def _row(row_hash: str, issue: str, **changes) -> dict:
    row = {
        "ts": "2026-08-01T12:00:00+00:00",
        "row_hash": row_hash,
        "card_identifier": issue,
        "status": "open",
        "first_raised": "2026-08-01T12:00:00+00:00",
        "urgency": "normal",
    }
    row.update(changes)
    return row


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _run(ledger: Path, *args: str, now: str = NOW) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--ledger", str(ledger), "--now", now, *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _due(result: subprocess.CompletedProcess[str]) -> list[dict]:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["due"]


def _ledger_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _sender_config(tmp_path: Path) -> tuple[Path, Path]:
    calls = tmp_path / "calls.jsonl"
    sender = tmp_path / "sender.py"
    sender.write_text(
        "import json, pathlib, sys\n"
        f"path=pathlib.Path({str(calls)!r})\n"
        "with path.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "reescalation": {
                    "sender": [sys.executable, str(sender), "{card_identifier}"]
                }
            }
        ),
        encoding="utf-8",
    )
    return config, calls


def test_plan_only_is_pure_and_first_normal_ping_is_due_at_48_hours(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_row("hash-1", "ANK-1")])
    before = ledger.read_bytes()

    result = _run(ledger, "--plan-only", now="2026-08-03T12:00:00+00:00")

    assert _due(result) == [
        {
            "card_identifier": "ANK-1",
            "reescalation_count": 0,
            "reason": "normal cadence: 48h elapsed",
        }
    ]
    assert ledger.read_bytes() == before


def test_normal_card_is_not_due_before_48_hours(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_row("hash-1", "ANK-1")])

    result = _run(ledger, "--plan-only", now="2026-08-03T11:59:59+00:00")

    assert _due(result) == []


def test_normal_intervals_double_after_three_and_cap_without_stopping(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rows = [
        _row(
            f"hash-{count}",
            f"ANK-{count}",
            first_raised="2026-01-01T00:00:00+00:00",
            last_ping_at="2026-07-22T12:00:00+00:00",
            reescalation_count=count,
        )
        for count in (2, 3, 4, 5, 12)
    ]
    _write_rows(ledger, rows)

    result = _run(ledger, "--plan-only")

    assert [item["card_identifier"] for item in _due(result)] == [
        "ANK-2",
        "ANK-3",
        "ANK-4",
        "ANK-5",
        "ANK-12",
    ]
    assert [item["reason"] for item in _due(result)] == [
        "normal cadence: 48h elapsed",
        "normal cadence: 96h elapsed",
        "normal cadence: 192h elapsed",
        "normal cadence: 336h elapsed",
        "normal cadence: 336h elapsed",
    ]


def test_only_open_and_fix_requested_latest_statuses_are_eligible(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(
        ledger,
        [
            _row("open", "ANK-OPEN"),
            _row("fix", "ANK-FIX", status="fix_requested"),
            _row("retired", "ANK-RETIRED", status="retired"),
            _row("approve", "ANK-APPROVE", status="decided:approve"),
            _row("skip", "ANK-SKIP", status="decided:skip"),
        ],
    )

    result = _run(ledger, "--plan-only")

    assert [item["card_identifier"] for item in _due(result)] == [
        "ANK-OPEN",
        "ANK-FIX",
    ]


def test_last_row_per_hash_wins(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(
        ledger,
        [
            _row("hash-1", "ANK-1"),
            _row("hash-1", "ANK-1", status="retired"),
        ],
    )

    result = _run(ledger, "--plan-only")

    assert _due(result) == []


def test_shared_card_identifier_produces_exactly_one_ping(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(
        ledger,
        [
            _row("hash-a", "ANK-13"),
            _row("hash-b", "ANK-13", status="retired"),
            _row("hash-c", "ANK-13"),
        ],
    )

    result = _run(ledger, "--plan-only")

    assert len(_due(result)) == 1
    assert _due(result)[0]["card_identifier"] == "ANK-13"


def test_send_mode_renders_argv_and_appends_incremented_card_row(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_row("hash-1", "ANK-1")])
    calls = tmp_path / "calls.jsonl"
    sender = tmp_path / "sender.py"
    sender.write_text(
        "import json, pathlib, sys\n"
        f"path=pathlib.Path({str(calls)!r})\n"
        "with path.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "reescalation": {
                    "sender": [
                        sys.executable,
                        str(sender),
                        "{card_identifier}",
                        "{reescalation_count}",
                        "{reason}",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = _run(ledger, "--config", str(config))

    assert result.returncode == 0, result.stderr
    assert _ledger_rows(calls) == [
        ["ANK-1", "1", "normal cadence: 48h elapsed"]
    ]
    appended = _ledger_rows(ledger)[-1]
    assert appended["last_ping_at"] == NOW
    assert appended["reescalation_count"] == 1
    assert appended["status"] == "open"


def test_missing_or_failed_sender_fails_closed_with_compensating_state(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_row("hash-1", "ANK-1")])
    original = ledger.read_bytes()

    missing = _run(ledger)

    assert missing.returncode != 0
    assert "requires --config" in missing.stderr
    assert ledger.read_bytes() == original

    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "reescalation": {
                    "sender": [sys.executable, "-c", "raise SystemExit(7)", "{issue}"]
                }
            }
        ),
        encoding="utf-8",
    )
    failed = _run(ledger, "--config", str(config))

    assert failed.returncode != 0
    assert "sender failed" in failed.stderr
    rows = _ledger_rows(ledger)
    assert [row["status"] for row in rows[-2:]] == ["delivery_pending", "open"]


def test_malformed_row_does_not_block_due_card_and_exits_nonzero(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    malformed = _row("unused", "ANK-BAD")
    del malformed["row_hash"]
    _write_rows(ledger, [malformed, _row("hash-good", "ANK-GOOD")])
    config, calls = _sender_config(tmp_path)

    result = _run(ledger, "--config", str(config))

    assert result.returncode == 2
    assert _ledger_rows(calls) == [["ANK-GOOD"]]
    assert "quarantined ANK-BAD: ledger line 1 lacks row_hash" in result.stdout
    assert '"quarantined": 1' in result.stdout


def test_quarantined_eligible_row_without_cadence_clock_is_not_pinged(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    poisoned = _row("hash-bad", "ANK-BAD")
    del poisoned["first_raised"]
    _write_rows(ledger, [poisoned, _row("hash-good", "ANK-GOOD")])
    config, calls = _sender_config(tmp_path)

    result = _run(ledger, "--config", str(config))

    assert result.returncode == 2
    assert _ledger_rows(calls) == [["ANK-GOOD"]]
    assert "quarantined ANK-BAD: first_raised must be a non-empty ISO 8601 string" in result.stdout
    assert all(row.get("card_identifier") != "ANK-BAD" for row in _ledger_rows(ledger)[2:])


def test_unreadable_ledger_still_aborts_without_pings(tmp_path):
    ledger = tmp_path / "missing-ledger.jsonl"
    config, calls = _sender_config(tmp_path)

    result = _run(ledger, "--config", str(config))

    assert result.returncode == 2
    assert "ledger could not be read" in result.stderr
    assert not calls.exists()


def test_snoozed_fyi_wakes_exactly_and_successfully_reopens(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    wake = "2026-08-09T13:00:00+00:00"
    _write_rows(ledger, [_row(
        "fyi", "ANK-FYI", status="snoozed", snooze_until=wake,
        action_mode="fyi", fyi_only=True,
    )])
    assert _due(_run(ledger, "--plan-only", now="2026-08-09T12:59:59+00:00")) == []
    assert len(_due(_run(ledger, "--plan-only", now=wake))) == 1
    config, calls = _sender_config(tmp_path)
    data = yaml.safe_load(config.read_text())
    data["reescalation"]["sender"].append("{action_mode}")
    config.write_text(yaml.safe_dump(data))

    result = _run(ledger, "--config", str(config), now=wake)

    assert result.returncode == 0
    assert _ledger_rows(calls) == [["ANK-FYI", "fyi"]]
    reopened = _ledger_rows(ledger)[-1]
    assert reopened["status"] == "open"
    assert reopened["action_mode"] == "fyi"
    assert reopened["fyi_only"] is True
    assert reopened["last_ping_at"] == wake
    assert reopened["reescalation_count"] == 1


def test_failed_snooze_reminder_restores_prior_eligible_state(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    original = _row(
        "fyi", "ANK-FYI", status="snoozed",
        snooze_until="2026-08-09T13:00:00+00:00", action_mode="fyi", fyi_only=True,
    )
    _write_rows(ledger, [original])
    before = ledger.read_bytes()
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"reescalation": {"sender": [
        sys.executable, "-c", "raise SystemExit(7)", "{issue}", "{action_mode}"
    ]}}))

    result = _run(ledger, "--config", str(config), now="2026-08-09T13:00:00+00:00")

    assert result.returncode == 3
    rows = _ledger_rows(ledger)
    assert [row["status"] for row in rows[-2:]] == ["delivery_pending", "snoozed"]
    assert rows[-1]["snooze_until"] == original["snooze_until"]


def test_intent_append_failure_never_starts_sender(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_row("hash-1", "ANK-1")])
    config, calls = _sender_config(tmp_path)

    def fail_intent(*args, **kwargs):
        raise reescalate.ReescalationError("injected intent failure")

    monkeypatch.setattr(reescalate, "_append_delivery_row", fail_intent)
    cards = reescalate.due_cards(ledger, reescalate._datetime(NOW, "now"))
    sender = reescalate._sender_from_config(config)

    assert reescalate.send_due(ledger, cards, sender, NOW) == 3
    assert not calls.exists()


def test_confirmation_failure_is_quarantined_and_not_resent(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    _write_rows(ledger, [_row("hash-1", "ANK-1")])
    config, calls = _sender_config(tmp_path)
    cards = reescalate.due_cards(ledger, reescalate._datetime(NOW, "now"))
    sender = reescalate._sender_from_config(config)

    def fail_confirmation(*args, **kwargs):
        raise reescalate.ReescalationError("injected confirmation failure")

    monkeypatch.setattr(reescalate, "_append_ping", fail_confirmation)
    assert reescalate.send_due(ledger, cards, sender, NOW) == 3
    assert _ledger_rows(calls) == [["ANK-1"]]
    assert _ledger_rows(ledger)[-1]["status"] == "delivery_pending"

    second = _run(ledger, "--config", str(config))
    assert second.returncode == 2
    assert _ledger_rows(calls) == [["ANK-1"]]
    assert "pending manual reconciliation" in second.stdout
    assert '"quarantined": 1' in second.stdout
