"""SG-LEARN grounding, governance, and outbox tests."""
from __future__ import annotations

import json
import logging
import multiprocessing
from pathlib import Path
import sys

import pytest
import yaml

from departments.social.runtime import (
    proposal_card_to_outbox,
    propose_insights,
    read_metrics_records,
)


def _write_observations(path):
    rows = [
        {
            "row_id": "sense-published-1",
            "metric": "posts_published",
            "value": 2,
            "source": "zernio",
            "ts": "2026-07-28T12:00:00+00:00",
            "surface": "linkedin_mybcat",
            "lane": "republish_reengagement",
            "item_type": "podcast",
        },
        {
            "row_id": "sense-engagement-1",
            "metric": "engagement_rate",
            "value": 0.25,
            "source": "zernio",
            "ts": "2026-07-28T12:05:00+00:00",
            "surface": "linkedin_mybcat",
            "lane": "republish_reengagement",
            "item_type": "podcast",
        },
        {
            "row_id": "sense-defect-1",
            "metric": "qa_defect.unsourced_claim",
            "value": 1,
            "code": "unsourced_claim",
            "source": "compare_charter",
            "ts": "2026-07-28T12:10:00+00:00",
            "surface": "linkedin_mybcat",
            "lane": "republish_reengagement",
            "item_type": "podcast",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _pack():
    return {
        "source": "SG-SENSE",
        "sanitized": True,
        "rows": [
            {
                "row_id": "sense-engagement-1",
                "metric": "engagement_rate",
                "value": 0.25,
                "source": "zernio",
                "ts": "2026-07-28T12:05:00+00:00",
            }
        ],
        "aggregates": [],
    }


def _charter_copy(tmp_path, *, engines=None, ttl=None):
    source = Path(proposal_card_to_outbox.DEFAULT_CHARTER)
    charter = yaml.safe_load(source.read_text(encoding="utf-8"))
    if engines is not None:
        charter["budget"]["engine_allowlist"] = engines
    if ttl is not None:
        charter["escalation"]["no_reply_ttl_hours"] = ttl
    target = tmp_path / "social-charter-copy.yaml"
    target.write_text(yaml.safe_dump(charter), encoding="utf-8")
    return target


def _append_worker(state_dir, evidence_pack, charter_path, card):
    proposal_card_to_outbox.append_cards(
        [card],
        state_dir,
        evidence_pack=evidence_pack,
        charter_path=charter_path,
    )


def test_aggregates_retain_row_provenance(tmp_path):
    observations = tmp_path / "observations.jsonl"
    _write_observations(observations)

    pack = read_metrics_records.build_evidence_pack(
        observations, assembled_at="2026-07-28T13:00:00+00:00"
    )

    assert pack["source"] == "SG-SENSE"
    assert pack["sanitized"] is True
    aggregate = pack["aggregates"][0]
    assert aggregate["surface"] == "linkedin_mybcat"
    assert aggregate["published"]["value_sum"] == 2
    assert aggregate["engagement"]["value_mean"] == 0.25
    assert aggregate["qa_defect"]["frequency_by_code"] == {"unsourced_claim": 1}
    assert aggregate["published"]["source_row_ids"] == ["sense-published-1"]
    assert aggregate["published"]["provenance"] == [
        {
            "row_id": "sense-published-1",
            "ts": "2026-07-28T12:00:00+00:00",
        }
    ]


def test_self_reported_sensitive_and_extra_fields_never_enter_pack(tmp_path):
    observations = tmp_path / "observations.jsonl"
    rows = [
        {
            "row_id": "self-report",
            "metric": "engagement_rate",
            "value": 1,
            "source": "department_self_report",
            "ts": "2026-07-28T12:00:00+00:00",
        },
        {
            "row_id": "sensitive",
            "metric": "engagement_rate",
            "value": 1,
            "source": "zernio",
            "ts": "2026-07-28T12:00:00+00:00",
            "email": "sensitive-shaped-value",
        },
        {
            "row_id": "safe",
            "metric": "engagement_rate",
            "value": 0.2,
            "source": "zernio",
            "ts": "2026-07-28T12:00:00+00:00",
            "surface": "linkedin",
            "untrusted_claim": "drop me",
        },
    ]
    observations.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    pack = read_metrics_records.build_evidence_pack(observations)

    assert [row["row_id"] for row in pack["rows"]] == ["safe"]
    assert set(pack["rows"][0]) <= {
        "metric", "value", "status", "source", "ts", "post_ref", "surface", "row_id"
    }
    assert pack["aggregates"][0]["engagement"]["provenance"] == [
        {"row_id": "safe", "ts": "2026-07-28T12:00:00+00:00"}
    ]


def test_ungrounded_proposal_is_dropped_and_logged(caplog):
    raw = [
        {
            "question": "Approve changing the cadence?",
            "kind": "approve",
            "class": "process_change",
            "evidence": ["not-in-pack"],
        }
    ]

    with caplog.at_level(logging.WARNING):
        cards = propose_insights.validate_proposals(raw, _pack())

    assert cards == []
    assert "ungrounded_proposal" in caplog.text


def test_prompt_update_class_is_assigned_from_explicit_change_type():
    raw = [
        {
            "question": "Approve updating the drafting prompt?",
            "kind": "approve",
            "change_type": "prompt_update",
            "evidence": ["sense-engagement-1"],
        }
    ]

    cards = propose_insights.validate_proposals(raw, _pack())

    assert cards[0]["class"] == "prompt_update"


def test_fake_engine_adapter_accepts_only_grounded_cards():
    def fake_runner(command, prompt):
        assert command == ["fake-subscription-engine"]
        assert "sense-engagement-1" in prompt
        return json.dumps(
            [
                {
                    "question": "Fix the QA prompt using this evidence?",
                    "kind": "fix",
                    "class": "prompt_update",
                    "evidence": ["sense-engagement-1"],
                }
            ]
        )

    cards = propose_insights.propose(
        _pack(),
        engine="codex_oauth",
        command=["fake-subscription-engine"],
        allowed_engines=frozenset({"codex_oauth"}),
        runner=fake_runner,
    )

    assert cards == [
        {
            "question": "Fix the QA prompt using this evidence?",
            "kind": "fix",
            "class": "prompt_update",
            "evidence": ["sense-engagement-1"],
        }
    ]


def test_outbox_is_idempotent_and_card_has_one_question_and_ttl(tmp_path):
    card = {
        "question": "Approve updating the drafting prompt?",
        "kind": "approve",
        "class": "prompt_update",
        "evidence": ["sense-engagement-1"],
    }

    first = proposal_card_to_outbox.append_cards(
        [card], tmp_path, evidence_pack=_pack(), now="2026-07-28T14:00:00+00:00"
    )
    second = proposal_card_to_outbox.append_cards(
        [card], tmp_path, evidence_pack=_pack(), now="2026-07-28T15:00:00+00:00"
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "approval_queue.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert first["queued"] == 1
    assert second["queued"] == 0
    assert len(rows) == 1
    assert rows[0]["question"].count("?") == 1
    assert rows[0]["ttl_hours"] == 24
    assert rows[0]["status"] == "pending"


def test_missing_class_self_modify_style_card_is_rejected(tmp_path):
    crafted = {
        "question": "Approve letting the department rewrite its own runtime?",
        "kind": "approve",
        "evidence": ["sense-engagement-1"],
        "self_modify": True,
    }

    with pytest.raises(ValueError, match="class is missing"):
        proposal_card_to_outbox.append_cards([crafted], tmp_path, evidence_pack=_pack())

    assert not (tmp_path / "approval_queue.jsonl").exists()


def test_direct_outbox_evidence_bypass_is_rejected(tmp_path, monkeypatch):
    card = {
        "question": "Approve changing the cadence?",
        "kind": "approve",
        "class": "process_change",
        "evidence": ["invented-row"],
    }
    cards_path = tmp_path / "cards.json"
    pack_path = tmp_path / "pack.json"
    out_path = tmp_path / "receipt.json"
    cards_path.write_text(json.dumps([card]), encoding="utf-8")
    pack_path.write_text(json.dumps(_pack()), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "proposal_card_to_outbox.py",
            "--state-dir", str(tmp_path),
            "--cards", str(cards_path),
            "--evidence-pack", str(pack_path),
            "--out", str(out_path),
        ],
    )

    assert proposal_card_to_outbox.main() == 2
    assert json.loads(out_path.read_text(encoding="utf-8"))["status"] == "blocked"
    assert not (tmp_path / "approval_queue.jsonl").exists()


def test_out_receipt_must_stay_inside_state_dir(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    cards = tmp_path / "cards.json"
    pack = tmp_path / "pack.json"
    cards.write_text("[]", encoding="utf-8")
    pack.write_text(json.dumps(_pack()), encoding="utf-8")
    escaped = tmp_path / "escaped-receipt.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "proposal_card_to_outbox.py",
            "--state-dir", str(state_dir),
            "--cards", str(cards),
            "--evidence-pack", str(pack),
            "--out", str(escaped),
        ],
    )

    assert proposal_card_to_outbox.main() == 2
    assert not escaped.exists()
    with pytest.raises(ValueError, match="governance"):
        proposal_card_to_outbox._safe_out_path(state_dir, state_dir / "runbook-receipt.json")


@pytest.mark.parametrize("protected_name", ["approval_queue.jsonl", ".approval_queue.lock"])
def test_out_receipt_refuses_queue_and_lock_without_modifying_them(
    tmp_path, monkeypatch, protected_name
):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    queue = state_dir / "approval_queue.jsonl"
    lock = state_dir / ".approval_queue.lock"
    queue.write_text('{"existing": "queue row"}\n', encoding="utf-8")
    lock.write_text("existing lock content\n", encoding="utf-8")
    cards = tmp_path / "cards.json"
    pack = tmp_path / "pack.json"
    cards.write_text("[]", encoding="utf-8")
    pack.write_text(json.dumps(_pack()), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "proposal_card_to_outbox.py",
            "--state-dir", str(state_dir),
            "--cards", str(cards),
            "--evidence-pack", str(pack),
            "--out", str(state_dir / protected_name),
        ],
    )

    assert proposal_card_to_outbox.main() == 2
    assert queue.read_text(encoding="utf-8") == '{"existing": "queue row"}\n'
    assert lock.read_text(encoding="utf-8") == "existing lock content\n"


def test_corrupt_charter_blocks_with_receipt_and_does_not_append(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    queue = state_dir / "approval_queue.jsonl"
    original_queue = '{"existing": "queue row"}\n'
    queue.write_text(original_queue, encoding="utf-8")
    cards = tmp_path / "cards.json"
    pack = tmp_path / "pack.json"
    charter = tmp_path / "corrupt-charter.yaml"
    out = state_dir / "receipt.json"
    cards.write_text(
        json.dumps(
            [
                {
                    "question": "Approve updating the drafting prompt?",
                    "kind": "approve",
                    "class": "prompt_update",
                    "evidence": ["sense-engagement-1"],
                }
            ]
        ),
        encoding="utf-8",
    )
    pack.write_text(json.dumps(_pack()), encoding="utf-8")
    charter.write_text("department: [social\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "proposal_card_to_outbox.py",
            "--state-dir", str(state_dir),
            "--cards", str(cards),
            "--evidence-pack", str(pack),
            "--charter", str(charter),
            "--out", str(out),
        ],
    )

    assert proposal_card_to_outbox.main() == 2
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["status"] == "blocked"
    assert queue.read_text(encoding="utf-8") == original_queue


def test_concurrent_dedup_queues_exactly_once(tmp_path):
    card = {
        "question": "Approve updating the drafting prompt?",
        "kind": "approve",
        "class": "prompt_update",
        "evidence": ["sense-engagement-1"],
    }
    charter = _charter_copy(tmp_path)
    context = multiprocessing.get_context("fork")
    processes = [
        context.Process(
            target=_append_worker,
            args=(tmp_path, _pack(), charter, card),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    rows = (tmp_path / "approval_queue.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1


def test_engine_allowlist_is_charter_driven_and_empty_fails_closed(tmp_path):
    changed = _charter_copy(tmp_path, engines=["test_subscription"])
    allowed = propose_insights._engine_allowlist(changed)
    assert allowed == frozenset({"test_subscription"})
    assert propose_insights.propose(
        _pack(),
        engine="test_subscription",
        command=["fake"],
        allowed_engines=allowed,
        runner=lambda command, prompt: "[]",
    ) == []
    with pytest.raises(ValueError, match="not subscription/OAuth allowlisted"):
        propose_insights.propose(
            _pack(),
            engine="codex_oauth",
            command=["fake"],
            allowed_engines=allowed,
            runner=lambda command, prompt: "[]",
        )

    empty = _charter_copy(tmp_path, engines=[])
    with pytest.raises(ValueError, match="missing or empty"):
        propose_insights._engine_allowlist(empty)


def test_outbox_ttl_is_charter_driven(tmp_path):
    charter = _charter_copy(tmp_path, ttl=7)
    card = {
        "question": "Approve updating the drafting prompt?",
        "kind": "approve",
        "class": "prompt_update",
        "evidence": ["sense-engagement-1"],
    }

    proposal_card_to_outbox.append_cards(
        [card],
        tmp_path,
        evidence_pack=_pack(),
        charter_path=charter,
    )

    row = json.loads(
        (tmp_path / "approval_queue.jsonl").read_text(encoding="utf-8").strip()
    )
    assert row["ttl_hours"] == 7
