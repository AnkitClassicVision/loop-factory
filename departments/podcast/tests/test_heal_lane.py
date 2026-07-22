"""Podcast SG-HEAL allowlist, execution, and verification tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from departments.podcast.runtime import heal_apply, heal_select, heal_verify


RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
PLAYBOOKS = RUNTIME / "playbooks.json"
NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _incident(fingerprint="fp-1", failure_class="timer_failed", **changes):
    row = {
        "fingerprint": fingerprint,
        "failure_class": failure_class,
        "first_seen": NOW.isoformat(),
        "last_seen": NOW.isoformat(),
        "state": "open",
        "severity": "error",
        "setpoint": "active",
        "observed": "failed",
        "evidence": {"probe": "systemctl_is_active", "unit": "podcast.timer"},
        "one_question": "Can the known playbook clear this?",
        "count": 1,
    }
    row.update(changes)
    return row


def _write_incidents(state_dir, *incidents):
    state_dir.mkdir(parents=True, exist_ok=True)
    data = {incident["fingerprint"]: incident for incident in incidents}
    (state_dir / "incidents.json").write_text(json.dumps(data), encoding="utf-8")


def _receipts(state_dir):
    return [
        json.loads(line)
        for line in (state_dir / "heals.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _custom_playbooks(tmp_path, *, target="runtime_unit", cap=1):
    source = json.loads(PLAYBOOKS.read_text(encoding="utf-8"))
    source["playbooks"][0]["heal_target"] = target
    source["playbooks"][0]["max_attempts_per_day"] = cap
    path = tmp_path / "playbooks.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    return path


def test_unknown_failure_class_is_refused_without_execution(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    _write_incidents(state_dir, _incident(failure_class="new_weird_failure"))

    def forbidden(*args, **kwargs):
        raise AssertionError("unknown incidents must never execute")

    monkeypatch.setattr(heal_apply.subprocess, "run", forbidden)
    assert heal_select.select_heal(state_dir, "fp-1", now=NOW) is None
    assert _receipts(state_dir)[-1]["result"] == "refused"
    assert _receipts(state_dir)[-1]["detail"] == "unknown pattern — escalate"


def test_immutable_heal_target_is_refused(tmp_path):
    state_dir = tmp_path / "state"
    _write_incidents(state_dir, _incident())
    playbooks = _custom_playbooks(tmp_path, target="autonomy_state")

    selected = heal_select.select_heal(
        state_dir, "fp-1", playbooks_path=playbooks, now=NOW
    )

    assert selected is None
    receipt = _receipts(state_dir)[-1]
    assert receipt["result"] == "refused"
    assert "heal may not modify immutable invariant: autonomy_state" == receipt["detail"]


def test_shadow_apply_executes_nothing_and_writes_proposal(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"

    def forbidden(*args, **kwargs):
        raise AssertionError("shadow apply must never call subprocess")

    monkeypatch.setattr(heal_apply.subprocess, "run", forbidden)
    receipt = heal_apply.apply_heal(
        state_dir,
        "fp-1",
        "restart_user_timer",
        {"unit": "podcast.timer"},
        shadow=True,
        now=NOW,
    )

    assert receipt["mode"] == "proposed"
    assert receipt["result"] == "proposed"
    assert receipt["commands"] == ["systemctl --user restart podcast.timer"]
    assert not (state_dir / "heal_attempts.json").exists()


def test_attempts_over_daily_cap_are_refused(tmp_path):
    state_dir = tmp_path / "state"
    playbooks = _custom_playbooks(tmp_path, cap=1)
    calls = []

    def succeeded(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    first = heal_apply.apply_heal(
        state_dir,
        "fp-1",
        "restart_user_timer",
        {"unit": "podcast.timer"},
        shadow=False,
        playbooks_path=playbooks,
        executor=succeeded,
        now=NOW,
    )
    second = heal_apply.apply_heal(
        state_dir,
        "fp-1",
        "restart_user_timer",
        {"unit": "podcast.timer"},
        shadow=False,
        playbooks_path=playbooks,
        executor=succeeded,
        now=NOW,
    )

    assert first["result"] == "proposed"
    assert second["result"] == "refused"
    assert "max attempts per day reached" in second["detail"]
    assert calls == [["systemctl", "--user", "restart", "podcast.timer"]]


def test_verify_marks_failed_when_condition_persists(tmp_path):
    state_dir = tmp_path / "state"
    _write_incidents(state_dir, _incident())

    receipt = heal_verify.verify_heal(
        state_dir,
        "fp-1",
        "restart_user_timer",
        prober=lambda incident: (True, "timer remains failed"),
        shadow=False,
        now=NOW,
    )

    assert receipt["mode"] == "applied"
    assert receipt["result"] == "failed"
    assert receipt["detail"] == "timer remains failed"


def test_verify_marks_verified_only_after_clearance(tmp_path):
    state_dir = tmp_path / "state"
    _write_incidents(state_dir, _incident())

    receipt = heal_verify.verify_heal(
        state_dir,
        "fp-1",
        "restart_user_timer",
        prober=lambda incident: False,
        shadow=False,
        now=NOW,
    )

    assert receipt["result"] == "verified"


def test_render_rejects_parameter_that_could_add_argv_tokens():
    playbook = heal_select.load_playbooks()[0]
    try:
        heal_apply.render_commands(playbook, {"unit": "podcast.timer --now"})
    except ValueError as exc:
        assert "unsafe value" in str(exc)
    else:
        raise AssertionError("unsafe parameter was accepted")
