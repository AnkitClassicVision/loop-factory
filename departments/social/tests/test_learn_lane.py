"""SG-LEARN grounding, governance, and outbox tests."""
from __future__ import annotations

import json
import logging

import pytest

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
            "source": "fake-zernio-sensor",
            "ts": "2026-07-28T12:00:00+00:00",
            "surface": "linkedin_mybcat",
            "lane": "republish_reengagement",
            "item_type": "podcast",
        },
        {
            "row_id": "sense-engagement-1",
            "metric": "engagement_rate",
            "value": 0.25,
            "source": "fake-zernio-sensor",
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
            "source": "fake-cross-model-qa",
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
        "rows": [
            {
                "row_id": "sense-engagement-1",
                "metric": "engagement_rate",
                "value": 0.25,
                "source": "fake-zernio-sensor",
                "ts": "2026-07-28T12:05:00+00:00",
            }
        ],
        "aggregates": [],
    }


def test_aggregates_retain_row_provenance(tmp_path):
    observations = tmp_path / "observations.jsonl"
    _write_observations(observations)

    pack = read_metrics_records.build_evidence_pack(
        observations, assembled_at="2026-07-28T13:00:00+00:00"
    )

    assert pack["source"] == "SG-SENSE"
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
        [card], tmp_path, now="2026-07-28T14:00:00+00:00"
    )
    second = proposal_card_to_outbox.append_cards(
        [card], tmp_path, now="2026-07-28T15:00:00+00:00"
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
        proposal_card_to_outbox.append_cards([crafted], tmp_path)

    assert not (tmp_path / "approval_queue.jsonl").exists()
