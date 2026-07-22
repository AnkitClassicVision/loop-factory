import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "capabilities", ROOT / "kernel/capabilities.py"
)
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)


def test_department_env_strips_all_credentials():
    dirty = {
        "PATH": "/usr/bin",
        "HUBSPOT_PRIVATE_APP_TOKEN": "x",
        "GMAIL_ACCESS_TOKEN": "y",
        "AWS_SECRET_ACCESS_KEY": "z",
        "ANTHROPIC_API_KEY": "k",
        "HOME": "/home/dep",
    }

    clean = C.department_env(dirty)

    assert clean["PATH"] == "/usr/bin"
    assert clean["HOME"] == "/home/dep"
    for name in (
        "HUBSPOT_PRIVATE_APP_TOKEN",
        "GMAIL_ACCESS_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "ANTHROPIC_API_KEY",
    ):
        assert name not in clean
    assert clean["OE_KERNEL_ONLY"] == "1"
    assert clean["PLACEHOLDER_MODE"] == "1"


def test_assert_no_ambient_credentials_raises_on_leak():
    with pytest.raises(C.AmbientCredentialError):
        C.assert_no_ambient_credentials({"HUBSPOT_PRIVATE_APP_TOKEN": "x"})


def test_assert_no_ambient_credentials_passes_clean():
    C.assert_no_ambient_credentials({"PATH": "/usr/bin", "OE_KERNEL_ONLY": "1"})


def test_case_insensitive_and_substring():
    dirty = {
        "PATH": "/usr/bin",
        "my_api_key": "x",
        "Service_Token": "y",
        "DB_PASSWORD": "z",
        "aws_anything": "a",
        "hubspot_anything": "b",
        "gmail_anything": "c",
    }

    clean = C.department_env(dirty)

    assert clean["PATH"] == "/usr/bin"
    for name in dirty.keys() - {"PATH"}:
        assert name not in clean
