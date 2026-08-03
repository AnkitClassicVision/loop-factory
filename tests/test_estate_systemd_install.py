from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from deploy import install_estate_watchdog


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "deploy" / "systemd"


def test_units_are_uniquely_named_and_invoke_real_entrypoints():
    estate = (SYSTEMD / "loop-factory-estate.service").read_text(encoding="utf-8")
    deadman = (SYSTEMD / "loop-factory-estate-deadman.service").read_text(encoding="utf-8")
    assert "loopfactory.py estate" in estate
    assert "factory/estate_deadman.py" in deadman
    assert "estate-manager.service" not in estate
    assert "estate-manager.service" not in deadman
    assert "--outbox /mnt/d_drive/repos/loop-factory/state/decisions_outbox.jsonl" in estate
    assert "--outbox /mnt/d_drive/repos/loop-factory/state/decisions_outbox.jsonl" in deadman
    assert "--alarm-state /mnt/d_drive/repos/loop-factory/state/estate-deadman/alarm_state.json" in deadman
    assert "--cooldown-seconds 21600" in deadman


def test_units_are_fail_closed_and_network_denied():
    for name in (
        "loop-factory-estate.service",
        "loop-factory-estate-deadman.service",
    ):
        text = (SYSTEMD / name).read_text(encoding="utf-8")
        assert "ProtectSystem=strict" in text
        assert "NoNewPrivileges=yes" in text
        assert "IPAddressDeny=any" in text
        assert "SuccessExitStatus=" not in text
    deadman = (SYSTEMD / "loop-factory-estate-deadman.service").read_text(encoding="utf-8")
    assert "ReadWritePaths=/mnt/d_drive/repos/loop-factory/state" in deadman
    assert "estate/state" not in deadman.split("ReadWritePaths=", 1)[1]
    assert "ConditionPathIsDirectory=" not in deadman


def test_timers_explicitly_target_unique_services():
    estate_timer = (SYSTEMD / "loop-factory-estate.timer").read_text(encoding="utf-8")
    deadman_timer = (SYSTEMD / "loop-factory-estate-deadman.timer").read_text(encoding="utf-8")
    assert "Unit=loop-factory-estate.service" in estate_timer
    assert "Unit=loop-factory-estate-deadman.service" in deadman_timer
    assert "Persistent=true" in estate_timer
    assert "Persistent=true" in deadman_timer


def test_installer_command_plan_is_idempotent_and_unique(tmp_path):
    commands = install_estate_watchdog.install_commands(ROOT, tmp_path)
    copies = [command for command in commands if command[:3] == ["install", "-m", "0644"]]
    assert len(copies) == 4
    assert {Path(command[-1]).name for command in copies} == set(install_estate_watchdog.UNIT_NAMES)
    assert commands[0][-1] == "--self-test-poisoned-registry"
    assert commands[1][:2] == ["systemd-analyze", "verify"]
    assert ["install", "-d", "-m", "0755", str(ROOT / "state" / "estate-deadman")] in commands
    systemctl = [command for command in commands if command and command[0] == "systemctl"]
    assert systemctl[0] == ["systemctl", "--user", "daemon-reload"]
    assert systemctl[1][:4] == ["systemctl", "--user", "enable", "--now"]
    assert systemctl[2] == ["systemctl", "--user", "start", "loop-factory-estate.service"]
    assert systemctl[3] == ["systemctl", "--user", "start", "loop-factory-estate-deadman.service"]
    assert any(command[:2] == ["systemd-analyze", "verify"] for command in commands)
    assert "estate-manager.service" not in " ".join(" ".join(command) for command in commands)


def test_systemd_analyze_verifies_temporary_unit_copies(tmp_path):
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze is unavailable")
    copies = []
    for name in install_estate_watchdog.UNIT_NAMES:
        target = tmp_path / name
        shutil.copy2(SYSTEMD / name, target)
        copies.append(str(target))
    result = subprocess.run(
        ["systemd-analyze", "verify", *copies],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0 and result.stderr and all(
        "Operation not permitted" in line
        for line in result.stderr.splitlines()
        if line.strip()
    ):
        pytest.skip(
            "systemd-analyze cannot create credential sockets in this sandbox"
        )
    assert result.returncode == 0, result.stderr


def test_default_installer_is_display_only():
    result = subprocess.run(
        ["python3", str(ROOT / "deploy" / "install_estate_watchdog.py")],
        check=True,
        text=True,
        capture_output=True,
    )
    assert "INSTALL DRY RUN (no commands executed)" in result.stdout
    assert "systemctl --user daemon-reload" in result.stdout
    assert "systemctl --user enable --now loop-factory-estate.timer loop-factory-estate-deadman.timer" in result.stdout
    assert "Re-run with --apply to execute exactly the commands above." in result.stdout


def test_no_real_subprocess_call_in_this_suite_contains_apply_flag():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        function = call.func
        is_subprocess_run = (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "subprocess"
            and function.attr == "run"
        )
        if not is_subprocess_run or not call.args:
            continue
        constants = {
            node.value
            for node in ast.walk(call.args[0])
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert "--apply" not in constants


def test_apply_refuses_noncanonical_root_without_executing_commands(monkeypatch, capsys):
    sentinel_root = ROOT.parent / "not-the-current-checkout"

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("refusal path must not execute subprocesses")

    monkeypatch.setattr(install_estate_watchdog, "CANONICAL_ROOT", sentinel_root)
    monkeypatch.setattr(install_estate_watchdog.subprocess, "run", fail_if_called)
    monkeypatch.setattr(sys, "argv", ["install_estate_watchdog.py", "--apply"])

    assert install_estate_watchdog.main() == 2
    assert "REFUSING: installer must run from" in capsys.readouterr().out


def test_apply_executes_exact_plan_only_when_explicit(monkeypatch):
    calls = []

    def fake_run(command, check):
        assert check is True
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(install_estate_watchdog, "CANONICAL_ROOT", ROOT)
    monkeypatch.setattr(install_estate_watchdog.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["install_estate_watchdog.py", "--apply"])

    assert install_estate_watchdog.main() == 0
    assert calls == install_estate_watchdog.install_commands(ROOT, Path.home())


def test_apply_failure_stops_and_prints_tested_rollback(monkeypatch, capsys):
    calls = []
    plan = install_estate_watchdog.install_commands(ROOT, Path.home())

    def fake_run(command, check):
        calls.append(command)
        if len(calls) == 3:
            raise subprocess.CalledProcessError(9, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(install_estate_watchdog, "CANONICAL_ROOT", ROOT)
    monkeypatch.setattr(install_estate_watchdog.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["install_estate_watchdog.py", "--apply"])

    assert install_estate_watchdog.main() == 9
    assert calls == plan[:3]
    assert "--apply --rollback" in capsys.readouterr().out


def test_rollback_plan_targets_only_unique_units(tmp_path):
    commands = install_estate_watchdog.rollback_commands(tmp_path)
    rendered = "\n".join(" ".join(command) for command in commands)
    assert "loop-factory-estate.timer" in rendered
    assert "loop-factory-estate-deadman.timer" in rendered
    assert "estate-manager.service" not in rendered
    assert all(str(tmp_path / ".config" / "systemd" / "user" / name) in rendered
               for name in install_estate_watchdog.UNIT_NAMES)


def test_applied_rollback_continues_when_units_are_already_absent(monkeypatch):
    calls = []
    plan = install_estate_watchdog.rollback_commands(Path.home())

    def fake_run(command, check):
        assert check is False
        calls.append(command)
        returncode = 5 if command[0] == "systemctl" and "daemon-reload" not in command else 0
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(install_estate_watchdog, "CANONICAL_ROOT", ROOT)
    monkeypatch.setattr(install_estate_watchdog.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["install_estate_watchdog.py", "--apply", "--rollback"])

    assert install_estate_watchdog.main() == 0
    assert calls == plan


def test_applied_rollback_reports_critical_failure_after_completing_plan(monkeypatch):
    calls = []
    plan = install_estate_watchdog.rollback_commands(Path.home())

    def fake_run(command, check):
        calls.append(command)
        returncode = 7 if command[0] == "rm" else 0
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(install_estate_watchdog, "CANONICAL_ROOT", ROOT)
    monkeypatch.setattr(install_estate_watchdog.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["install_estate_watchdog.py", "--apply", "--rollback"])

    assert install_estate_watchdog.main() == 7
    assert calls == plan
