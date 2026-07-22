"""Tests for charter-scoped department environment capabilities."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPABILITIES = _load("launch_test_capabilities", "kernel/capabilities.py")
LAUNCH = _load("launch_test_launch", "factory/launch.py")


def _write_charter(root, department, capabilities_marker=None):
    department_dir = root / "departments" / department
    department_dir.mkdir(parents=True)
    capability_yaml = ""
    if capabilities_marker is not None:
        capability_yaml = "capabilities:\n  - systemd_user_probe\n"
    (department_dir / "charter.yaml").write_text(
        "department: " + department + "\n"
        "owner: test-owner\n"
        "autonomy_state: shadow\n"
        "immutable_safety_invariants:\n"
        "  heal_may_not_modify: [x]\n"
        + capability_yaml,
        encoding="utf-8",
    )


def test_department_env_drops_xdg_runtime_dir_without_capability():
    env = CAPABILITIES.department_env({"XDG_RUNTIME_DIR": "/run/user/1000"})

    assert "XDG_RUNTIME_DIR" not in env


def test_department_env_grants_xdg_runtime_dir_with_capability():
    env = CAPABILITIES.department_env(
        {"XDG_RUNTIME_DIR": "/run/user/1000"},
        capabilities=["systemd_user_probe"],
    )

    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"


def test_assertion_rejects_capability_env_without_declaration():
    with pytest.raises(CAPABILITIES.AmbientCredentialError):
        CAPABILITIES.assert_no_ambient_credentials(
            {"XDG_RUNTIME_DIR": "/run/user/1000"})


def test_assertion_accepts_capability_env_with_declaration():
    CAPABILITIES.assert_no_ambient_credentials(
        {"XDG_RUNTIME_DIR": "/run/user/1000"},
        capabilities=["systemd_user_probe"],
    )


def test_unknown_capability_fails_closed():
    with pytest.raises(ValueError, match="unknown department capability"):
        CAPABILITIES.department_env({}, capabilities=["systemd_user_typo"])


def test_missing_charter_fails_launch_env_build(tmp_path):
    with pytest.raises(RuntimeError, match="charter not found"):
        LAUNCH.build_env({}, department="missing", root=tmp_path)


@pytest.mark.parametrize("declared", [False, True])
def test_launch_build_env_honors_charter_capabilities(tmp_path, declared):
    department = "with-capability" if declared else "without-capability"
    _write_charter(
        tmp_path,
        department,
        capabilities_marker="declared" if declared else None,
    )

    env = LAUNCH.build_env(
        {"PATH": "/usr/bin", "XDG_RUNTIME_DIR": "/run/user/1000"},
        department=department,
        root=tmp_path,
    )

    assert ("XDG_RUNTIME_DIR" in env) is declared


def test_capability_launch_allows_department_runtime_python_and_grants_env(
    tmp_path,
):
    department = "confined"
    _write_charter(tmp_path, department, capabilities_marker="declared")
    runtime = tmp_path / "departments" / department / "runtime"
    runtime.mkdir()
    script = runtime / "sensor.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    calls = []

    result = LAUNCH.launch_command(
        department,
        [sys.executable, str(script)],
        base={"PATH": "/usr/bin", "XDG_RUNTIME_DIR": "/run/user/1000"},
        root=tmp_path,
        runner=lambda command, env: (
            calls.append((command, env)) or SimpleNamespace(returncode=0)
        ),
    )

    assert result == 0
    assert calls[0][0] == [sys.executable, str(script)]
    assert calls[0][1]["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert calls[0][1]["OE_DEPARTMENT"] == department


@pytest.mark.parametrize("command_kind", ["out-of-tree", "non-python"])
def test_capability_launch_refuses_untrusted_command(tmp_path, command_kind):
    department = "confined"
    _write_charter(tmp_path, department, capabilities_marker="declared")
    runtime = tmp_path / "departments" / department / "runtime"
    runtime.mkdir()
    runtime_script = runtime / "sensor.py"
    runtime_script.write_text("print('ok')\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    command = (
        [sys.executable, str(outside)]
        if command_kind == "out-of-tree"
        else ["/bin/sh", str(runtime_script)]
    )

    with pytest.raises(LAUNCH.LaunchRefused, match="capability-bearing"):
        LAUNCH.launch_command(
            department,
            command,
            base={"XDG_RUNTIME_DIR": "/run/user/1000"},
            root=tmp_path,
            runner=lambda *args, **kwargs: pytest.fail(
                "a refused command must not execute"
            ),
        )


def test_no_capability_launch_keeps_arbitrary_command_behavior(tmp_path):
    department = "unconfined"
    _write_charter(tmp_path, department)
    calls = []

    result = LAUNCH.launch_command(
        department,
        ["/bin/echo", "still-allowed"],
        base={"XDG_RUNTIME_DIR": "/run/user/1000"},
        root=tmp_path,
        runner=lambda command, env: (
            calls.append((command, env)) or SimpleNamespace(returncode=0)
        ),
    )

    assert result == 0
    assert calls[0][0] == ["/bin/echo", "still-allowed"]
    assert "XDG_RUNTIME_DIR" not in calls[0][1]
