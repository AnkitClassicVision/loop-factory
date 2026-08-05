from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from factory import mailroom_registry


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "factory" / "mailroom_registry.py"


def _config(tmp_path: Path, watches=None) -> Path:
    value = {
        "cursor_file": str(tmp_path / "cursor.json"),
        "ledger_file": str(tmp_path / "ledger.jsonl"),
        "watches": watches or [],
        "senders": {
            "ping": ["ping-command", "{text}"],
            "card": ["card-command", "{body}"],
            "card_enabled": True,
        },
        "listener": {
            "reader": ["reader-command"],
            "close_enabled": False,
        },
    }
    path = tmp_path / "outbox_push.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def _run(config: Path, department: str, outbox: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(config),
            "--department",
            department,
            "--outbox",
            str(outbox),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_registers_one_escalation_watch_and_preserves_other_config(tmp_path):
    config = _config(tmp_path)
    before = yaml.safe_load(config.read_text(encoding="utf-8"))
    outbox = tmp_path / "departments" / "unit" / "state" / "decisions_outbox.jsonl"

    result = _run(config, "unit", outbox)

    assert result.returncode == 0, result.stderr
    after = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert after["watches"] == [
        {"path": str(outbox), "department": "unit", "kind": "escalation"}
    ]
    for key in ("cursor_file", "ledger_file", "senders", "listener"):
        assert after[key] == before[key]


def test_registering_same_department_and_outbox_twice_is_idempotent(tmp_path):
    config = _config(tmp_path)
    outbox = tmp_path / "outbox.jsonl"

    assert _run(config, "unit", outbox).returncode == 0
    second = _run(config, "unit", outbox)

    assert second.returncode == 0, second.stderr
    watches = yaml.safe_load(config.read_text(encoding="utf-8"))["watches"]
    assert watches == [{"path": str(outbox), "department": "unit", "kind": "escalation"}]


@pytest.mark.parametrize("contents", ["", "- one\n", "watches: nope\n"])
def test_missing_or_broken_config_fails_closed(tmp_path, contents):
    config = tmp_path / "broken.yaml"
    if contents:
        config.write_text(contents, encoding="utf-8")
    result = _run(config, "unit", tmp_path / "outbox.jsonl")

    assert result.returncode != 0
    assert result.stderr.strip()


def test_atomic_replace_failure_leaves_original_config(tmp_path, monkeypatch):
    config = _config(tmp_path)
    original = config.read_bytes()

    def fail_replace(source, destination):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(mailroom_registry.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated crash"):
        mailroom_registry.register_watch(config, "unit", tmp_path / "outbox.jsonl")

    assert config.read_bytes() == original
    assert not list(tmp_path.glob(f".{config.name}.*.tmp"))


def test_scaffold_wires_config_and_warns_when_config_is_absent(tmp_path, capsys):
    config = _config(tmp_path)
    from factory import scaffold

    out = scaffold.scaffold_department("unit", root=tmp_path, mailroom_config=config)
    assert out["mailroom"]["registered"] is True
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["watches"]

    absent = tmp_path / "missing.yaml"
    out = scaffold.scaffold_department("other", root=tmp_path, mailroom_config=absent)
    assert out["mailroom"]["registered"] is False
    assert "mailroom" in capsys.readouterr().err.lower()
