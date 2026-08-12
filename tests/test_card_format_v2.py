import json
import sys

from factory import estate_deadman, human_in_the_loop, manager
from departments.podcast.runtime import escalate_outbox


def _row(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_escalate_without_owner_deadline_and_next_action_stops_as_blocked(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    result = human_in_the_loop.escalate("sales", "old issue", outbox)

    assert result["escalated"] is False
    assert result["blocked"] is True
    assert not outbox.exists()


def test_full_v2_escalate_writes_exact_card_shape(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    actions = [{
        "action": "Pause spend",
        "effect": "new spend stays paused",
        "reply": "approve pause-spend",
    }]
    human_in_the_loop.escalate(
        "sales", "budget near", outbox,
        meaning="Spend is near its ceiling",
        needs="Choose whether to pause",
        actions=actions,
        owner="human-owner",
        deadline="2026-08-10T12:00:00Z",
        next_action="Choose whether to pause spend",
    )

    row = _row(outbox)
    assert row["schema"] == "human-outbox-escalation/v1"
    assert row["escalation"]["owner"] == "human-owner"
    assert row["escalation"]["status"] == "open"
    assert row["card"] == {
        "what_it_means": "Spend is near its ceiling",
        "what_it_needs": "Choose whether to pause",
        "approvable_actions": actions,
    }


def test_fyi_only_escalate_has_no_approvable_actions(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    human_in_the_loop.escalate(
        "sales", "review", outbox,
        meaning="Something needs review", needs="Ops review", fyi_only=True,
        owner="human-owner",
        deadline="2026-08-10T12:00:00Z",
        next_action="Review the update",
    )

    card = _row(outbox)["card"]
    assert card["fyi_only"] is True
    assert "approvable_actions" not in card


def test_manager_main_budget_telemetry_missing_card(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox.jsonl"
    state = tmp_path / "state"
    budget = tmp_path / "missing-budget.json"

    monkeypatch.setattr(manager, "_load_charter_config", lambda *_: {
        "thresholds": {},
        "autonomy_state": "shadow",
        "escalation_owner": "human-owner",
        "escalation_sla_hours": 24,
    })
    def fake_cycle(*args, **kwargs):
        kwargs["escalate_fn"](
            "[sales] budget_telemetry_missing: detail",
            context={"finding": "budget_telemetry_missing"},
        )
        return {"epoch": 1, "findings": [], "escalations": 1, "brief_path": None}

    monkeypatch.setattr(manager, "run_manager_cycle", fake_cycle)
    monkeypatch.setattr(sys, "argv", [
        "manager.py", "--department", "sales", "--state-dir", str(state),
        "--outbox", str(outbox), "--budget", str(budget),
    ])

    manager.main()

    assert _row(outbox)["card"] == {
        "what_it_means": "Budget ceilings are set but no usage data exists, so spend cannot be verified",
        "what_it_needs": "Wire the telemetry producer or confirm the path",
        "approvable_actions": [{
            "action": "Acknowledge until the P-next producer lands",
            "effect": "card stays parked, re-raised weekly",
            "reply": "approve ack-budget-telemetry",
        }],
    }


def test_deadman_alarm_has_inspect_restart_card(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    report = {
        "findings": [{"code": "estate_heartbeat_stale", "detail": "stale"}],
        "observed_at": "2026-08-06T12:00:00+00:00",
        "max_age_seconds": 600,
    }
    estate_deadman.raise_alarm(report, outbox)

    card = _row(outbox)["card"]
    assert "watchdog" in card["what_it_means"]
    assert card["approvable_actions"] == [{
        "action": "Inspect and restart",
        "effect": "inspect the stale heartbeat and restart the affected watchdog or conductor",
        "reply": "approve inspect-restart",
    }]


def test_podcast_schema_accepts_old_and_new_card_but_rejects_malformed(tmp_path, capsys):
    old = {
        "kind": "escalation", "department": "podcast",
        "issue": "hopper_blind: Decide?", "ts": "2026-08-06T12:00:00+00:00",
        "eli5": escalate_outbox._eli5("hopper_blind"),
        "context": {"fingerprint": "123456789abc", "escalation_marker": "open",
                    "incident_state": "open", "one_question": "Decide?", "evidence": []},
    }
    path = tmp_path / "outbox.jsonl"
    path.write_text(json.dumps(old) + "\n", encoding="utf-8")
    assert escalate_outbox._load_outbox_markers(path) == {
        ("123456789abc", "open"): old["ts"]
    }

    new = {**old, "card": {"what_it_means": "Meaning", "what_it_needs": "Needs", "fyi_only": True}}
    path.write_text(json.dumps(new) + "\n", encoding="utf-8")
    assert escalate_outbox._load_outbox_markers(path)

    malformed = {**old, "card": "bad"}
    path.write_text(json.dumps(malformed) + "\n", encoding="utf-8")
    assert escalate_outbox._load_outbox_markers(path) == {}
    assert "does not match podcast escalation schema" in capsys.readouterr().err
