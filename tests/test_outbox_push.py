from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from factory import outbox_push


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


def test_duplicate_row_content_is_skipped_by_hash(tmp_path):
    row = json.dumps({"eli5": "same"})
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(row + "\n" + row + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    assert _run(_config(tmp_path, watch, ping, card)).returncode == 0
    assert len(_calls(tmp_path / "ping.jsonl")) == 1
    assert json.loads((tmp_path / "cursor.json").read_text())[str(watch)]["offset_lines"] == 2


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


def test_card_failure_does_not_block_cursor(tmp_path):
    watch = tmp_path / "outbox.jsonl"
    watch.write_text(json.dumps({"eli5": "hello"}) + "\n")
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "bad_card", 1)
    config = _config(tmp_path, watch, ping, card)
    assert _run(config).returncode == 0
    assert json.loads((tmp_path / "cursor.json").read_text())[str(watch)]["offset_lines"] == 1
    assert _run(config).returncode == 0
    assert len(_calls(tmp_path / "ping.jsonl")) == 1


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


def test_missing_watch_file_is_tolerated(tmp_path):
    ping, card = _sender(tmp_path, "ping"), _sender(tmp_path, "card")
    result = _run(_config(tmp_path, tmp_path / "missing.jsonl", ping, card))
    assert result.returncode == 0
    assert not (tmp_path / "cursor.json").exists()


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
