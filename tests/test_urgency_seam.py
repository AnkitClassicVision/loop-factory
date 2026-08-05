from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUTBOX_PUSH = ROOT / "factory" / "outbox_push.py"
REESCALATE = ROOT / "factory" / "reescalate.py"


def _sender(tmp_path: Path, name: str, *, card_identifier: str | None = None) -> Path:
    script = tmp_path / f"{name}.py"
    output = (
        json.dumps({"identifier": card_identifier}) + "\n"
        if card_identifier is not None
        else ""
    )
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({output!r})\n",
        encoding="utf-8",
    )
    return script


def _push_packet(tmp_path: Path, packet: dict) -> tuple[Path, dict]:
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps(packet) + "\n", encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    ping = _sender(tmp_path, "ping")
    card = _sender(tmp_path, "card", card_identifier="ANK-SEAM")
    config = tmp_path / "outbox.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "cursor_file": str(tmp_path / "cursor.json"),
                "ledger_file": str(ledger),
                "watches": [
                    {
                        "path": str(watch),
                        "department": "test-department",
                        "kind": "approval",
                    }
                ],
                "senders": {
                    "ping": [sys.executable, str(ping), "{text}"],
                    "card": [sys.executable, str(card), "{title}", "{body}"],
                    "card_enabled": True,
                },
            }
        ),
        encoding="utf-8",
    )

    pushed = subprocess.run(
        [sys.executable, str(OUTBOX_PUSH), "--config", str(config), "--once"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert pushed.returncode == 0, pushed.stderr
    row = json.loads(ledger.read_text(encoding="utf-8"))
    return ledger, row


def _plan(ledger: Path, now: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(REESCALATE),
            "--ledger",
            str(ledger),
            "--now",
            now,
            "--plan-only",
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_outbox_to_reescalation_defaults_absent_urgency_to_normal(tmp_path):
    ledger, row = _push_packet(tmp_path, {"question": "Approve this?"})
    now = datetime.fromisoformat(row["first_raised"]) + timedelta(hours=48)

    result = _plan(ledger, now.isoformat())

    assert row["urgency"] == "normal"
    assert "due" not in row
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["due"][0]["card_identifier"] == "ANK-SEAM"


def test_outbox_to_reescalation_preserves_urgent_due(tmp_path):
    due = "2099-08-06T00:00:00+00:00"
    ledger, row = _push_packet(
        tmp_path,
        {"question": "Approve urgently?", "urgency": "urgent", "due": due},
    )

    result = _plan(ledger, row["first_raised"])

    assert row["urgency"] == "urgent"
    assert row["due"] == due
    assert result.returncode == 0, result.stderr


def test_outbox_to_reescalation_still_refuses_invalid_urgency(tmp_path):
    ledger, row = _push_packet(
        tmp_path,
        {"question": "Corrupt urgency", "urgency": "banana"},
    )

    result = _plan(ledger, row["first_raised"])

    assert row["urgency"] == "banana"
    assert result.returncode == 2
    assert "urgency must be 'normal' or 'urgent'" in result.stderr
