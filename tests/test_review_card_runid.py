from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from departments.social.runtime import create_review_card


def _run(tmp_path: Path, monkeypatch, run_id: str | None = None):
    draft = tmp_path / "draft.json"
    candidate = tmp_path / "candidate.json"
    ledger = tmp_path / "ledger.jsonl"
    receipt = tmp_path / "receipt.json"
    draft.write_text(json.dumps({"body": "Draft body", "surface": "linkedin"}))
    candidate.write_text(json.dumps({"item": {"item_id": "episode-123", "title": "Episode"}}))

    descriptions: list[str] = []

    def fake_gql(_key, query, variables=None):
        if "issueCreate" in query:
            descriptions.append(variables["i"]["description"])
            return {
                "issueCreate": {
                    "success": True,
                    "issue": {
                        "id": "issue-id",
                        "identifier": "ANK-123",
                        "url": "https://example.test/ANK-123",
                    },
                }
            }
        return {
            "teams": {"nodes": [{"id": "team-id", "key": "ANK"}]},
            "workflowStates": {"nodes": []},
            "users": {"nodes": []},
        }

    argv = [
        "create_review_card.py",
        "--draft", str(draft),
        "--candidate", str(candidate),
        "--ledger", str(ledger),
        "--out", str(receipt),
    ]
    if run_id is not None:
        argv.extend(["--run-id", run_id])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(create_review_card, "_get_key", lambda: "fake-key")
    monkeypatch.setattr(create_review_card, "_gql", fake_gql)

    assert create_review_card.main() == 0
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    return rows, json.loads(receipt.read_text()), descriptions


def test_run_id_is_stored_in_ledger_and_receipt(tmp_path, monkeypatch):
    rows, receipt, descriptions = _run(tmp_path, monkeypatch, "run-456")

    assert rows[0]["run_id"] == "run-456"
    assert receipt["run_id"] == "run-456"
    assert descriptions == ["APPROVE to queue | SKIP to discard | FIX to request changes\n\n---\n\nDraft body\n\nrun: run-456\n"]


def test_missing_run_id_is_stored_as_unknown(tmp_path, monkeypatch):
    rows, receipt, descriptions = _run(tmp_path, monkeypatch)

    assert rows[0]["run_id"] == "unknown"
    assert receipt["run_id"] == "unknown"
    assert descriptions[0].endswith("run: unknown\n")


def test_row_hash_matches_pre_change_algorithm(tmp_path, monkeypatch):
    rows, receipt, _ = _run(tmp_path, monkeypatch, "fixed-run")
    expected = hashlib.sha256(b"social-episode-123-fixed-run").hexdigest()

    assert rows[0]["row_hash"] == expected
    assert receipt["row_hash"] == expected


def test_ledger_appends_exactly_once_per_invocation(tmp_path, monkeypatch):
    rows, _, _ = _run(tmp_path, monkeypatch, "run-once")
    rows, _, _ = _run(tmp_path, monkeypatch, "run-twice")

    assert len(rows) == 2
    assert [row["run_id"] for row in rows] == ["run-once", "run-twice"]
