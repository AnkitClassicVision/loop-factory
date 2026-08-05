from __future__ import annotations

import json
from pathlib import Path

from factory import outbox_listen, outbox_push


def _push_config(tmp_path: Path, outbox: Path, ledger: Path) -> dict:
    return {
        "cursor_file": str(tmp_path / "push_cursor.json"),
        "watches": [
            {
                "path": str(outbox),
                "department": "packet-flow",
                "kind": "approval",
            }
        ],
        "ping": ["fake-ping", "{text}"],
        "card": ["fake-card", "{title}", "{body}"],
        "buzz": [],
        "card_enabled": True,
        "ledger_file": str(ledger),
    }


def _listen_config(tmp_path: Path, ledger: Path) -> dict:
    return {
        "ledger_file": str(ledger),
        "reader": ["fake-reader", "{issue}"],
        "closer": [],
        "close_enabled": False,
        "ack": [],
        "decisions_file": str(tmp_path / "decisions.jsonl"),
    }


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_packet_id_round_trips_from_outbox_packet_to_approve_decision(
    tmp_path, monkeypatch
):
    packet_id = "packet-remote-agent-42"
    outbox = tmp_path / "outbox.jsonl"
    outbox.write_text(
        json.dumps({"question": "Approve this packet?", "packet_id": packet_id})
        + "\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(outbox_push, "_send", lambda argv: True)
    monkeypatch.setattr(
        outbox_push,
        "_send_captured",
        lambda argv: (
            True,
            json.dumps(
                {
                    "identifier": "ANK-4242",
                    "url": "https://example.test/ANK-4242",
                }
            ),
        ),
    )

    assert outbox_push.tick(_push_config(tmp_path, outbox, ledger)) == 0
    assert _rows(ledger)[0]["packet_id"] == packet_id

    monkeypatch.setattr(
        outbox_listen,
        "_run_reader",
        lambda argv: [{"body": "APPROVE\nProceed with the packet."}],
    )
    assert outbox_listen.tick(_listen_config(tmp_path, ledger)) == 0

    decision = _rows(tmp_path / "decisions.jsonl")[0]
    assert decision["decision"] == "approve"
    assert decision["packet_id"] == packet_id


def test_packet_without_packet_id_pushes_cleanly(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox.jsonl"
    outbox.write_text(
        json.dumps({"question": "Approve legacy packet?"}) + "\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(outbox_push, "_send", lambda argv: True)
    monkeypatch.setattr(
        outbox_push,
        "_send_captured",
        lambda argv: (True, json.dumps({"identifier": "ANK-4343"})),
    )

    assert outbox_push.tick(_push_config(tmp_path, outbox, ledger)) == 0
    assert "packet_id" not in _rows(ledger)[0]
