"""Tests for charter-scoped department environment capabilities."""
from __future__ import annotations

import importlib.util
from pathlib import Path

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
