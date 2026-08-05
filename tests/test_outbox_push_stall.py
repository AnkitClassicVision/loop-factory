from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "factory" / "outbox_push.py"


def _config(tmp_path: Path, watches: list[Path]) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "cursor_file": str(tmp_path / "cursor.json"),
                "watches": [
                    {
                        "path": str(watch),
                        "department": "mailroom",
                        "kind": "approval",
                    }
                    for watch in watches
                ],
                "senders": {
                    "ping": [sys.executable, "-c", "raise SystemExit(0)"],
                    "card": [sys.executable, "-c", "raise SystemExit(0)"],
                    "card_enabled": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return config


def _run(config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config), "--once", *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_all_watch_paths_missing_exit_four_and_name_missing_path(tmp_path):
    first = tmp_path / "missing-one.jsonl"
    second = tmp_path / "missing-two.jsonl"

    result = _run(_config(tmp_path, [first, second]))

    assert result.returncode == 4
    assert str(first) in result.stderr
    assert str(second) in result.stderr


def test_one_present_empty_watch_is_healthy(tmp_path):
    missing = tmp_path / "missing.jsonl"
    present = tmp_path / "present.jsonl"
    present.write_text("", encoding="utf-8")

    result = _run(_config(tmp_path, [missing, present]))

    assert result.returncode == 0
    assert not (tmp_path / "cursor.json").exists()


def test_dry_run_with_all_watch_paths_missing_is_unaffected(tmp_path):
    missing = tmp_path / "missing.jsonl"

    result = _run(_config(tmp_path, [missing]), "--dry-run")

    assert result.returncode == 0
    assert not (tmp_path / "cursor.json").exists()
