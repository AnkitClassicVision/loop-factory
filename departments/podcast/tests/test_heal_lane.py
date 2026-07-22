"""Podcast SG-HEAL allowlist, execution, and verification tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        "evidence": ["systemd://podcast.timer"],
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


def test_charter_only_immutable_heal_target_is_refused(tmp_path):
    state_dir = tmp_path / "state"
    _write_incidents(state_dir, _incident())
    playbooks = _custom_playbooks(tmp_path, target="escalation_dedup_policy")

    selected = heal_select.select_heal(
        state_dir, "fp-1", playbooks_path=playbooks, now=NOW
    )

    assert selected is None
    receipt = _receipts(state_dir)[-1]
    assert receipt["result"] == "refused"
    assert (
        "heal may not modify immutable invariant: escalation_dedup_policy"
        == receipt["detail"]
    )


def test_shadow_apply_executes_nothing_and_writes_proposal(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    _write_incidents(state_dir, _incident())

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


def test_attempts_over_daily_cap_are_refused(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    _write_incidents(state_dir, _incident())
    calls = []

    def succeeded(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        heal_apply.kernel_bridge, "require_shadow", lambda live=False: None
    )
    receipts = [
        heal_apply.apply_heal(
            state_dir,
            "fp-1",
            "restart_user_timer",
            {"unit": "podcast.timer"},
            shadow=False,
            executor=succeeded,
            now=NOW,
        )
        for _ in range(3)
    ]

    assert [receipt["result"] for receipt in receipts] == [
        "proposed", "proposed", "refused",
    ]
    assert "max attempts per day reached" in receipts[-1]["detail"]
    assert calls == [
        ["systemctl", "--user", "restart", "podcast.timer"],
        ["systemctl", "--user", "restart", "podcast.timer"],
    ]


def test_live_playbooks_override_is_refused_without_execution(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    _write_incidents(state_dir, _incident())
    playbooks = _custom_playbooks(tmp_path)

    monkeypatch.setattr(
        heal_apply.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("a live playbook override must never execute")
        ),
    )
    receipt = heal_apply.apply_heal(
        state_dir,
        "fp-1",
        "restart_user_timer",
        {"unit": "podcast.timer"},
        shadow=False,
        playbooks_path=playbooks,
        now=NOW,
    )

    assert receipt["result"] == "refused"
    assert "canonical podcast playbook allowlist" in receipt["detail"]
    assert not (state_dir / "heal_attempts.json").exists()


def test_live_missing_incident_fingerprint_is_refused_without_execution(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "state"
    _write_incidents(state_dir, _incident(fingerprint="different-fingerprint"))

    monkeypatch.setattr(
        heal_apply.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("an unbound live heal must never execute")
        ),
    )
    receipt = heal_apply.apply_heal(
        state_dir,
        "absent-fingerprint",
        "restart_user_timer",
        {"unit": "podcast.timer"},
        shadow=False,
        now=NOW,
    )

    assert receipt["result"] == "refused"
    assert receipt["detail"] == "fingerprint does not name an existing incident"
    assert not (state_dir / "heal_attempts.json").exists()


@pytest.mark.parametrize(
    ("incident_changes", "expected_detail"),
    [
        ({"state": "resolved"}, "incident is not open or a department defect"),
        (
            {"failure_class": "receipt_stale"},
            "incident failure class does not match the selected playbook",
        ),
    ],
)
def test_live_heal_requires_healable_state_and_matching_failure_class(
    tmp_path, monkeypatch, incident_changes, expected_detail
):
    state_dir = tmp_path / "state"
    _write_incidents(state_dir, _incident(**incident_changes))

    monkeypatch.setattr(
        heal_apply.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("an incident mismatch must not execute"),
    )
    receipt = heal_apply.apply_heal(
        state_dir,
        "fp-1",
        "restart_user_timer",
        {"unit": "podcast.timer"},
        shadow=False,
        now=NOW,
    )

    assert receipt["result"] == "refused"
    assert receipt["detail"] == expected_detail
    assert not (state_dir / "heal_attempts.json").exists()


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


def test_default_verify_derives_systemd_unit_from_real_incident_evidence(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "state"
    _write_incidents(
        state_dir,
        _incident(evidence=["systemd://podcast-production.timer"]),
    )
    probed = []

    def cleared(unit):
        probed.append(unit)
        return False, f"systemctl is-active {unit}: active"

    monkeypatch.setattr(heal_verify, "_systemctl_inactive", cleared)

    receipt = heal_verify.verify_heal(
        state_dir,
        "fp-1",
        "restart_user_timer",
        shadow=False,
        now=NOW,
    )

    assert receipt["result"] == "verified"
    assert probed == ["podcast-production.timer"]


def test_default_verify_tolerates_legacy_dict_evidence(monkeypatch):
    probed = []
    monkeypatch.setattr(
        heal_verify,
        "_systemctl_inactive",
        lambda unit: (probed.append(unit) or (False, "active")),
    )

    persists, detail = heal_verify.default_condition_persists(
        _incident(evidence={"probe": "systemctl_is_active", "unit": "legacy.timer"})
    )

    assert persists is False
    assert detail == "active"
    assert probed == ["legacy.timer"]


def test_render_rejects_parameter_that_could_add_argv_tokens():
    playbook = heal_select.load_playbooks()[0]
    try:
        heal_apply.render_commands(playbook, {"unit": "podcast.timer --now"})
    except ValueError as exc:
        assert "unsafe value" in str(exc)
    else:
        raise AssertionError("unsafe parameter was accepted")
