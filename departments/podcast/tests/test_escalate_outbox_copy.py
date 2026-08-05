import json

from departments.podcast.runtime import escalate_outbox
from departments.podcast.runtime import record


def test_hopper_blind_escalation_leads_with_plain_language(tmp_path):
    technical_question = (
        "Hopper/publish-reliability evidence is unavailable — what broke the "
        "publish schedule source or the publish-day verifier?"
    )
    incidents_path = tmp_path / "incidents.json"
    outbox_path = tmp_path / "decisions_outbox.jsonl"
    record.atomic_write_json(
        incidents_path,
        {
            "123456789abc": {
                "fingerprint": "123456789abc",
                "failure_class": "hopper_blind",
                "state": "open",
                "evidence": ["fixture://hopper"],
                "one_question": technical_question,
                "escalated": False,
                "escalated_at": None,
            }
        },
    )

    escalate_outbox.escalate_new_incidents(incidents_path, outbox_path)

    row = json.loads(outbox_path.read_text(encoding="utf-8"))
    assert row["eli5"].startswith("[podcast]")
    assert "WHAT THIS MEANS:" in row["eli5"]
    assert "WHAT IT NEEDS:" in row["eli5"]
    assert not row["eli5"].startswith("hopper_blind")
    assert row["context"]["one_question"] == technical_question
    assert technical_question not in row["eli5"]
