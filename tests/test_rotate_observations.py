from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from departments.podcast.runtime.rotate_observations import rotate


pytestmark = pytest.mark.usefixtures("factory_record_spool")


SCRIPT = Path(__file__).parents[1] / "departments" / "podcast" / "runtime" / "rotate_observations.py"


def _row(ts: str, subject: str = "episode", detail: object = "same") -> str:
    return json.dumps(
        {"ts": ts, "sensor": "timer", "subject": subject, "status": "ok", "detail": detail}
    ) + "\n"


def _run(state_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--state-dir", str(state_dir), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_dedup_keeps_newest_and_removes_exact_copies(tmp_path):
    path = tmp_path / "observations.jsonl"
    old = _row("2026-01-01T00:00:00+00:00")
    distinct = _row("2026-01-02T00:00:00+00:00", detail="changed")
    newest = _row("2026-01-03T00:00:00+00:00")
    path.write_text(old + distinct + newest, encoding="utf-8")

    receipt = rotate(tmp_path, 5000)

    assert path.read_text(encoding="utf-8") == distinct + newest
    assert receipt == {"kept": 2, "deduped": 1, "archived": 0, "dry_run": False, "malformed": 0}


def test_under_limit_file_with_no_dupes_is_byte_identical(tmp_path):
    path = tmp_path / "observations.jsonl"
    original = _row("2026-01-01T00:00:00+00:00", "one") + _row("2026-01-02T00:00:00+00:00", "two")
    path.write_bytes(original.encode())

    rotate(tmp_path, 10)

    assert path.read_bytes() == original.encode()


def test_overflow_archives_oldest_and_archive_appends_across_runs(tmp_path):
    path = tmp_path / "observations.jsonl"
    first = [_row(f"2026-01-0{number}T00:00:00+00:00", str(number)) for number in range(1, 5)]
    path.write_text("".join(first), encoding="utf-8")

    first_receipt = rotate(tmp_path, 2)
    archive = next(tmp_path.glob("observations-archive-*.jsonl"))
    assert archive.read_text(encoding="utf-8") == "".join(first[:2])
    assert first_receipt["archived"] == 2

    second = [_row("2026-01-05T00:00:00+00:00", "5"), _row("2026-01-06T00:00:00+00:00", "6")]
    path.write_text(path.read_text(encoding="utf-8") + "".join(second), encoding="utf-8")
    second_receipt = rotate(tmp_path, 2)

    assert archive.read_text(encoding="utf-8") == "".join(first[:4])
    assert path.read_text(encoding="utf-8") == "".join(second)
    assert second_receipt["archived"] == 2


def test_dry_run_leaves_file_byte_identical_and_reports_counts(tmp_path):
    path = tmp_path / "observations.jsonl"
    original = _row("2026-01-01T00:00:00+00:00") + _row("2026-01-02T00:00:00+00:00")
    path.write_bytes(original.encode())

    result = _run(tmp_path, "--max-lines", "0", "--dry-run")

    assert result.returncode == 0
    assert path.read_bytes() == original.encode()
    assert not list(tmp_path.glob("observations-archive-*.jsonl"))
    assert json.loads(result.stdout) == {"kept": 0, "deduped": 1, "archived": 1, "dry_run": True, "malformed": 0}


def test_malformed_lines_survive_at_their_position_and_are_counted(tmp_path):
    path = tmp_path / "observations.jsonl"
    malformed = "{not json}\n"
    newest = _row("2026-01-03T00:00:00+00:00")
    path.write_text(_row("2026-01-01T00:00:00+00:00") + malformed + newest, encoding="utf-8")

    receipt = rotate(tmp_path, 10)

    assert path.read_text(encoding="utf-8") == malformed + newest
    assert receipt["malformed"] == 1


def test_receipt_json_parses_with_all_keys(tmp_path):
    (tmp_path / "observations.jsonl").write_text(_row("2026-01-01T00:00:00+00:00"), encoding="utf-8")

    result = _run(tmp_path)
    receipt = json.loads(result.stdout)

    assert result.returncode == 0
    assert set(receipt) == {"kept", "deduped", "archived", "dry_run", "malformed"}
    assert result.stderr == ""


def test_unreadable_missing_input_exits_one_with_clear_error(tmp_path):
    result = _run(tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "unreadable input" in result.stderr
