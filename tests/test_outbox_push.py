from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "factory" / "outbox_push.py"


def _sender(tmp_path: Path, name: str, exit_code: int = 0) -> Path:
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import json, pathlib, sys\n"
        f"p=pathlib.Path({str(tmp_path / (name + '.jsonl'))!r})\n"
        "with p.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    return script


def _card_sender_output(tmp_path: Path, name: str, output: str) -> Path:
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import pathlib, sys\n"
        f"p=pathlib.Path({str(tmp_path / (name + '.jsonl'))!r})\n"
        "with p.open('a') as f: f.write('called\\n')\n"
        f"sys.stdout.write({output!r})\n",
        encoding="utf-8",
    )
    return script


def _selective_ping_sender(tmp_path: Path) -> Path:
    script = tmp_path / "selective_ping.py"
    script.write_text(
        "import json, pathlib, sys\n"
        f"p=pathlib.Path({str(tmp_path / 'selective_ping.jsonl')!r})\n"
        "with p.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "raise SystemExit(1 if 'retry me' in sys.argv[1] else 0)\n",
        encoding="utf-8",
    )
    return script


def _config(tmp_path: Path, watch: Path, ping: Path, card: Path, **changes) -> Path:
    value = {
        "cursor_file": str(tmp_path / "cursor.json"),
        "watches": [{"path": str(watch), "department": "label", "kind": "approval"}],
        "senders": {
            "ping": [sys.executable, str(ping), "{text}", "{department}", "{kind}"],
            "card": [sys.executable, str(card), "{title}", "{body}"],
            "card_enabled": True,
        },
    }
    value.update(changes)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return path


def _run(config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config), "--once", *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _calls(path: Path) -> list[list[str]]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_new_rows_pushed_once_and_second_tick_uses_offset(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"question": "Approve this?", "ts": "now"}) + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    config = _config(tmp_path, watch, ping, card)
    assert _run(config).returncode == 0
    assert _run(config).returncode == 0
    assert len(_calls(tmp_path / "ping.jsonl")) == 1
    assert len(_calls(tmp_path / "card.jsonl")) == 1
    assert (
        "Reply with first line: APPROVE (confirm/apply), SKIP (dismiss), or FIX: "
        "<what to change> (add notes on the lines below)."
        in _calls(tmp_path / "card.jsonl")[0][1]
    )


def test_escalation_card_uses_approve_and_respond_proposal_grammar(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"question": "Keep this handling?"}) + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    config = _config(tmp_path, watch, ping, card)
    data = yaml.safe_load(config.read_text())
    data["watches"][0]["kind"] = "escalation"
    config.write_text(yaml.safe_dump(data))

    assert _run(config).returncode == 0
    body = _calls(tmp_path / "card.jsonl")[0][1]
    assert "APPROVE (keep or accept this handling as-is)" in body
    assert "FIX: <change> (request a change or retirement" in body
    assert "external_send" not in body
    assert "stale evidence" not in body


def test_duplicate_row_content_is_skipped_by_hash(tmp_path):
    row = json.dumps({"eli5": "same"})
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(row + "\n" + row + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    assert _run(_config(tmp_path, watch, ping, card)).returncode == 0
    assert len(_calls(tmp_path / "ping.jsonl")) == 1
    assert json.loads((tmp_path / "cursor.json").read_text())[str(watch)]["offset_lines"] == 2


def test_fyi_rows_share_stable_department_dedupe_key(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(
        json.dumps({"eli5": "first FYI", "card": {"fyi_only": True}}) + "\n"
        + json.dumps({"eli5": "second FYI", "card": {"fyi_only": True}}) + "\n"
    )
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    config = _config(tmp_path, watch, ping, card)
    data = yaml.safe_load(config.read_text())
    data["senders"]["card"].append("{dedupe_key}")
    config.write_text(yaml.safe_dump(data))

    assert _run(config).returncode == 0
    assert [call[-1] for call in _calls(tmp_path / "card.jsonl")] == [
        "loop-factory-fyi:label",
        "loop-factory-fyi:label",
    ]


def test_real_decision_uses_unique_row_digest_as_dedupe_key(tmp_path):
    raw = json.dumps({"question": "Approve this decision?"})
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(raw + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    config = _config(tmp_path, watch, ping, card)
    data = yaml.safe_load(config.read_text())
    data["senders"]["card"].append("{dedupe_key}")
    config.write_text(yaml.safe_dump(data))

    assert _run(config).returncode == 0
    expected = hashlib.sha256(raw.encode()).hexdigest()
    assert _calls(tmp_path / "card.jsonl")[0][-1] == expected


def test_ping_failure_leaves_failed_row_and_retries_without_earlier_duplicate(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "first"}) + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    config = _config(tmp_path, watch, ping, card)
    assert _run(config).returncode == 0
    watch.write_text(watch.read_text() + json.dumps({"eli5": "second"}) + "\n")
    failing = _sender(tmp_path, "failing", 1)
    data = yaml.safe_load(config.read_text())
    data["senders"]["ping"][1] = str(failing)
    config.write_text(yaml.safe_dump(data))
    assert _run(config).returncode == 3
    assert json.loads((tmp_path / "cursor.json").read_text())[str(watch)]["offset_lines"] == 1
    data["senders"]["ping"][1] = str(ping)
    config.write_text(yaml.safe_dump(data))
    assert _run(config).returncode == 0
    texts = [call[0] for call in _calls(tmp_path / "ping.jsonl")]
    assert sum("first" in text for text in texts) == 1
    assert sum("second" in text for text in texts) == 1


def test_mixed_watch_ping_failure_forces_exit_three_and_preserves_failed_row(tmp_path):
    failed_watch = tmp_path / "failed.jsonl"
    successful_watch = tmp_path / "successful.jsonl"
    failed_watch.write_text(json.dumps({"eli5": "retry me"}) + "\n")
    successful_watch.write_text(json.dumps({"eli5": "consume me"}) + "\n")
    ping = _selective_ping_sender(tmp_path)
    card = _sender(tmp_path, "card")
    config = _config(tmp_path, failed_watch, ping, card)
    data = yaml.safe_load(config.read_text())
    data["watches"].append(
        {"path": str(successful_watch), "department": "label", "kind": "approval"}
    )
    config.write_text(yaml.safe_dump(data))

    assert _run(config).returncode == 3

    cursor = json.loads((tmp_path / "cursor.json").read_text())
    assert cursor[str(failed_watch)]["offset_lines"] == 0
    assert cursor[str(successful_watch)]["offset_lines"] == 1
    assert len(_calls(tmp_path / "selective_ping.jsonl")) == 2


def test_card_failure_leaves_row_unconsumed(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "hello"}) + "\n")
    ledger = tmp_path / "ledger.jsonl"
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "bad_card", 1)
    cursor = tmp_path / "cursor.json"
    cursor.write_text(
        json.dumps({str(watch): {"offset_lines": 0, "last_hashes": []}}) + "\n"
    )
    config = _config(tmp_path, watch, ping, card, ledger_file=str(ledger))

    assert _run(config).returncode == 3
    assert json.loads(cursor.read_text())[str(watch)] == {
        "offset_lines": 0,
        "last_hashes": [],
    }
    assert not ledger.exists()


def test_approve_mapped_to_pause_is_rejected_without_consuming_row(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "Approve = pause the launch"}) + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")

    result = _run(_config(tmp_path, watch, ping, card))

    assert result.returncode == 3
    assert not (tmp_path / "ping.jsonl").exists()
    assert not (tmp_path / "card.jsonl").exists()
    assert not (tmp_path / "cursor.json").exists()
    assert "Approve must never mean 'pause'" in result.stderr
    assert "owner rule 2026-08-05" in result.stderr


def test_real_mixed_case_approve_pause_incident_is_rejected(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(
        json.dumps({"eli5": "Approve = PAUSE the 10am publish"}) + "\n"
    )
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")

    result = _run(_config(tmp_path, watch, ping, card))

    assert result.returncode == 3
    assert not (tmp_path / "ping.jsonl").exists()
    assert not (tmp_path / "card.jsonl").exists()


def test_stop_verb_not_mapped_to_approve_is_allowed(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(
        json.dumps(
            {
                "eli5": (
                    "WHAT IT NEEDS: approve the catch-up actions; ops will pause "
                    "the old timer"
                )
            }
        )
        + "\n"
    )
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")

    result = _run(_config(tmp_path, watch, ping, card))

    assert result.returncode == 0
    assert len(_calls(tmp_path / "ping.jsonl")) == 1
    assert len(_calls(tmp_path / "card.jsonl")) == 1


def test_approve_to_cancel_is_rejected(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "Approve to cancel the schedule"}) + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")

    result = _run(_config(tmp_path, watch, ping, card))

    assert result.returncode == 3
    assert not (tmp_path / "ping.jsonl").exists()
    assert not (tmp_path / "card.jsonl").exists()
    assert "Approve must never mean 'cancel'" in result.stderr


def test_card_failure_retries_successfully_on_next_tick(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "hello"}) + "\n")
    ledger = tmp_path / "ledger.jsonl"
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "bad_card", 1)
    config = _config(tmp_path, watch, ping, card, ledger_file=str(ledger))

    assert _run(config).returncode == 3

    working_card = _card_sender_output(
        tmp_path,
        "working_card",
        '{"identifier":"ANK-456","url":"https://example.test/ANK-456"}\n',
    )
    data = yaml.safe_load(config.read_text())
    data["senders"]["card"][1] = str(working_card)
    config.write_text(yaml.safe_dump(data))

    assert _run(config).returncode == 0
    state = json.loads((tmp_path / "cursor.json").read_text())[str(watch)]
    assert state["offset_lines"] == 1
    assert len(state["last_hashes"]) == 1
    assert json.loads(ledger.read_text())["card_identifier"] == "ANK-456"
    assert len(_calls(tmp_path / "ping.jsonl")) == 2


def test_buzz_failure_after_card_and_ledger_consumes_row(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "buzz later"}) + "\n")
    ledger = tmp_path / "ledger.jsonl"
    ping = _sender(tmp_path, "ping")
    card = _card_sender_output(
        tmp_path,
        "card_for_buzz",
        '{"identifier":"ANK-789","url":"https://example.test/ANK-789"}\n',
    )
    buzz = _sender(tmp_path, "bad_buzz", 1)
    config = _config(tmp_path, watch, ping, card, ledger_file=str(ledger))
    data = yaml.safe_load(config.read_text())
    data["senders"]["buzz"] = [sys.executable, str(buzz), "{card}"]
    config.write_text(yaml.safe_dump(data))

    result = _run(config)

    assert result.returncode == 0
    state = json.loads((tmp_path / "cursor.json").read_text())[str(watch)]
    assert state["offset_lines"] == 1
    assert len(state["last_hashes"]) == 1
    assert json.loads(ledger.read_text())["card_identifier"] == "ANK-789"
    assert "buzz sender failed" in result.stderr


def test_sensitive_fields_excluded_and_text_truncated(tmp_path):
    secret = "NEVER_INCLUDE_SECRET"
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(
        json.dumps({"eli5": "x" * 900, "context": {"secret": secret}, "body": secret}) + "\n"
    )
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    assert _run(_config(tmp_path, watch, ping, card)).returncode == 0
    ping_text = _calls(tmp_path / "ping.jsonl")[0][0]
    card_args = _calls(tmp_path / "card.jsonl")[0]
    assert len(ping_text) == 800
    assert secret not in ping_text
    assert all(secret not in value for value in card_args)


def test_single_missing_watch_file_is_a_stall(tmp_path):
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    missing = tmp_path / "missing.jsonl"
    result = _run(_config(tmp_path, missing, ping, card))
    assert result.returncode == 4
    assert str(missing) in result.stderr
    assert not (tmp_path / "cursor.json").exists()


def test_missing_watch_file_is_tolerated_when_another_watch_is_present(tmp_path):
    missing = tmp_path / "missing.jsonl"
    present = tmp_path / "present.jsonl"
    present.write_text(json.dumps({"eli5": "existing watch processed"}) + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    config = _config(tmp_path, missing, ping, card)
    data = yaml.safe_load(config.read_text())
    data["watches"].append(
        {"path": str(present), "department": "label", "kind": "approval"}
    )
    config.write_text(yaml.safe_dump(data))

    result = _run(config)

    assert result.returncode == 0
    assert len(_calls(tmp_path / "ping.jsonl")) == 1
    assert "existing watch processed" in _calls(tmp_path / "ping.jsonl")[0][0]
    assert json.loads((tmp_path / "cursor.json").read_text())[str(present)][
        "offset_lines"
    ] == 1


def test_invalid_config_exits_two(tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text("watches: nope\n")
    result = _run(config)
    assert result.returncode == 2
    assert "invalid config" in result.stderr


def test_dry_run_sends_nothing_and_advances_nothing(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "preview"}) + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    result = _run(_config(tmp_path, watch, ping, card), "--dry-run")
    assert result.returncode == 0
    assert "dry-run ping argv" in result.stderr
    assert not (tmp_path / "ping.jsonl").exists()
    assert not (tmp_path / "card.jsonl").exists()
    assert not (tmp_path / "cursor.json").exists()


def test_successful_card_send_appends_tracked_ledger_row(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"question": "Approve ledger entry?"}) + "\n")
    ledger = tmp_path / "ledger.jsonl"
    ping = _sender(tmp_path, "ping")
    card = _card_sender_output(
        tmp_path,
        "card_json",
        'setup log\n{"identifier":"ANK-123","url":"https://example.test/ANK-123"}\n',
    )
    config = _config(
        tmp_path,
        watch,
        ping,
        card,
        ledger_file=str(ledger),
    )

    assert _run(config).returncode == 0

    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["card_identifier"] == "ANK-123"
    assert rows[0]["card_url"] == "https://example.test/ANK-123"
    assert rows[0]["department"] == "label"
    assert rows[0]["kind"] == "approval"
    assert rows[0]["summary"] == "Approve ledger entry?"
    assert rows[0]["status"] == "open"
    assert rows[0]["row_hash"]
    assert rows[0]["ts"]


def test_junk_card_stdout_appends_untracked_ledger_without_failing_push(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "Still delivered"}) + "\n")
    ledger = tmp_path / "ledger.jsonl"
    ping = _sender(tmp_path, "ping")
    card = _card_sender_output(tmp_path, "card_junk", "not json at all\n")
    config = _config(
        tmp_path,
        watch,
        ping,
        card,
        ledger_file=str(ledger),
    )

    result = _run(config)

    assert result.returncode == 0
    row = json.loads(ledger.read_text())
    assert row["card_identifier"] is None
    assert row["card_url"] is None
    assert row["status"] == "untracked"
    assert "untracked" in result.stderr
    assert json.loads((tmp_path / "cursor.json").read_text())[str(watch)][
        "offset_lines"
    ] == 1
