from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from departments.pulse.runtime.clarify_ask import create_asks
from departments.pulse.runtime.digest_build import build_digest
from departments.pulse.runtime.intake_scan import scan
from departments.pulse.runtime.objectives_sensor import observe
from factory.runrecord import validate_record


REPO = Path(__file__).resolve().parents[3]
ORCHESTRATOR = REPO / "departments" / "pulse" / "runtime" / "pulse_daily.sh"


def fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "pulse"
    for name in ("inbox", "replies", "state"):
        (root / name).mkdir(parents=True)
    return root


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_intake_classifies_nonblank_utf8_as_readable(tmp_path):
    root = fixture_root(tmp_path)
    (root / "inbox" / "good.txt").write_text("status green\n", encoding="utf-8")
    scan(root)
    assert read_json(root / "state" / "intake.json") == {"readable": ["good.txt"], "unreadable": []}


def test_intake_classifies_empty_file_as_unreadable(tmp_path):
    root = fixture_root(tmp_path)
    (root / "inbox" / "empty.txt").write_bytes(b"")
    scan(root)
    assert read_json(root / "state" / "intake.json")["unreadable"] == ["empty.txt"]


def test_intake_classifies_blank_lines_as_unreadable(tmp_path):
    root = fixture_root(tmp_path)
    (root / "inbox" / "blank.txt").write_text(" \n\t\n", encoding="utf-8")
    scan(root)
    assert read_json(root / "state" / "intake.json")["unreadable"] == ["blank.txt"]


def test_intake_classifies_non_utf8_as_unreadable(tmp_path):
    root = fixture_root(tmp_path)
    (root / "inbox" / "binary.txt").write_bytes(b"\xff\xfe")
    scan(root)
    assert read_json(root / "state" / "intake.json")["unreadable"] == ["binary.txt"]


def test_ask_created_once_and_draft_has_no_note_name(tmp_path):
    root = fixture_root(tmp_path)
    (root / "state" / "intake.json").write_text(json.dumps({"readable": [], "unreadable": ["person-name.txt"]}), encoding="utf-8")
    create_asks(root)
    create_asks(root)
    rows = (root / "state" / "asks.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    ask = json.loads(rows[0])
    draft = (root / "state" / "outbox" / f"ask-{ask['ask_id']}.md").read_text(encoding="utf-8")
    assert "person-name" not in draft


def test_digest_folds_readable_note_exactly_once_across_runs(tmp_path):
    root = fixture_root(tmp_path)
    (root / "inbox" / "one.txt").write_text("UNIQUE BODY", encoding="utf-8")
    scan(root)
    digest = build_digest(root)
    build_digest(root)
    assert digest.read_text(encoding="utf-8").count("UNIQUE BODY") == 1
    assert list(read_json(root / "state" / "digest_ledger.json")["notes"]) == ["one.txt"]


def test_reply_harvest_marks_ask_and_folds_clarification(tmp_path):
    root = fixture_root(tmp_path)
    (root / "state" / "intake.json").write_text(json.dumps({"readable": [], "unreadable": ["bad.txt"]}), encoding="utf-8")
    create_asks(root)
    ask = json.loads((root / "state" / "asks.jsonl").read_text(encoding="utf-8"))
    (root / "replies" / f"{ask['ask_id']}.txt").write_text("clarified fixture", encoding="utf-8")
    digest = build_digest(root)
    harvested = json.loads((root / "state" / "asks.jsonl").read_text(encoding="utf-8"))
    assert harvested["status"] == "harvested"
    assert "harvested_ts" in harvested
    assert digest.read_text(encoding="utf-8").count("clarified fixture") == 1


def test_sensor_omits_coverage_when_no_readable_notes(tmp_path):
    root = fixture_root(tmp_path)
    (root / "state" / "intake.json").write_text(json.dumps({"readable": [], "unreadable": []}), encoding="utf-8")
    receipt = observe(root)
    assert "digest_coverage" not in read_json(receipt)["values"]


def test_sensor_reports_full_coverage(tmp_path):
    root = fixture_root(tmp_path)
    (root / "inbox" / "one.txt").write_text("ready", encoding="utf-8")
    scan(root)
    build_digest(root)
    values = read_json(observe(root))["values"]
    assert values["digest_coverage"] == 100


def test_sensor_counts_open_ask_older_than_48_hours(tmp_path):
    root = fixture_root(tmp_path)
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    old = now - timedelta(hours=49)
    row = {"ask_id": "a1", "note": "bad.txt", "ts": old.isoformat(), "status": "open"}
    (root / "state" / "asks.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    values = read_json(observe(root, now=now))["values"]
    assert values["ask_return_integrity"] == 1


def run_cycle(root: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        ["bash", str(ORCHESTRATOR), "--root", str(root)],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def load_records(root: Path) -> list[dict]:
    path = root / "state" / "runs-v2.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_orchestrator_appends_four_valid_run_records(tmp_path):
    root = fixture_root(tmp_path)
    (root / "inbox" / "good.txt").write_text("all green", encoding="utf-8")
    result = run_cycle(root)
    assert result.returncode == 0, result.stderr
    records = load_records(root)
    assert [record["node"] for record in records] == ["N1", "N3", "N2", "N4"]
    assert all(validate_record(record)["status"] == "ok" for record in records)
    assert all(record["external_actions_taken"] == 0 for record in records)


def test_orchestrator_stops_after_failing_node(tmp_path):
    root = fixture_root(tmp_path)
    (root / "state" / "intake.json").mkdir()
    result = run_cycle(root)
    assert result.returncode != 0
    records = load_records(root)
    assert [(record["node"], record["status"]) for record in records] == [("N1", "error")]
    validate_record(records[0])


def test_orchestrator_full_fixture_cycle_outputs_receipts(tmp_path):
    root = fixture_root(tmp_path)
    (root / "inbox" / "good.txt").write_text("fixture status", encoding="utf-8")
    (root / "inbox" / "empty.txt").write_text("", encoding="utf-8")
    result = run_cycle(root)
    assert result.returncode == 0, result.stderr
    assert (root / "state" / "intake.json").is_file()
    assert (root / "state" / "asks.jsonl").is_file()
    assert list((root / "state").glob("digest-*.md"))
    assert read_json(root / "state" / "objectives_observed.json")["values"]["digest_coverage"] == 100
