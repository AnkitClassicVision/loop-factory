from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from factory import cadence


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "cadence"


def valid_contract() -> dict:
    return yaml.safe_load((FIXTURES / "valid.yaml").read_text(encoding="utf-8"))


def write_contract(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "cadence.yaml"
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return path


def test_valid_contract_renders_green(tmp_path):
    rendered = cadence.check_contract(FIXTURES / "valid.yaml", tmp_path / "units")
    assert [path.name for path in rendered] == ["intake-loop.timer", "intake-loop.service"]
    assert "OnCalendar=*:05/30" in rendered[0].read_text(encoding="utf-8")
    assert "Persistent=false" in rendered[0].read_text(encoding="utf-8")


def test_enabled_by_default_true_fails(tmp_path):
    value = valid_contract()
    value["activation"]["enabled_by_default"] = True
    with pytest.raises(cadence.CadenceError, match=r"WHY:.*must be false"):
        cadence.check_contract(write_contract(tmp_path, value), tmp_path / "units")


def test_unknown_trigger_kind_fails(tmp_path):
    value = valid_contract()
    value["triggers"][1]["kind"] = "webhook"
    with pytest.raises(cadence.CadenceError, match=r"WHY:.*unknown"):
        cadence.check_contract(write_contract(tmp_path, value), tmp_path / "units")


def test_goal_trigger_missing_cursor_policy_fails(tmp_path):
    value = valid_contract()
    del value["triggers"][1]["spec"]["cursor_policy"]
    with pytest.raises(cadence.CadenceError, match=r"WHY:.*cursor_policy"):
        cadence.check_contract(write_contract(tmp_path, value), tmp_path / "units")


def test_digest_cap_missing_fails(tmp_path):
    value = valid_contract()
    del value["alerting"]["digest"]["cap_per_day"]
    with pytest.raises(cadence.CadenceError, match=r"WHY:.*cap_per_day"):
        cadence.check_contract(write_contract(tmp_path, value), tmp_path / "units")


def test_rendered_execstart_containing_systemctl_fails(tmp_path):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    for name in ("dept-loop.timer.tmpl", "dept-loop.service.tmpl"):
        (template_dir / name).write_text(
            (cadence.TEMPLATE_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    service = template_dir / "dept-loop.service.tmpl"
    service.write_text(
        service.read_text(encoding="utf-8").replace(
            "ExecStart=/usr/bin/env bash", "ExecStart=/usr/bin/systemctl --user start x #"
        ),
        encoding="utf-8",
    )
    with pytest.raises(cadence.CadenceError, match=r"WHY:.*systemctl"):
        cadence.check_contract(
            FIXTURES / "valid.yaml", tmp_path / "units", template_dir=template_dir
        )


def test_rendering_is_byte_deterministic(tmp_path):
    first = cadence.check_contract(FIXTURES / "valid.yaml", tmp_path / "first")
    second = cadence.check_contract(FIXTURES / "valid.yaml", tmp_path / "second")
    assert [path.read_bytes() for path in first] == [path.read_bytes() for path in second]


def test_malformed_yaml_cli_exits_one_with_clean_why(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "factory.cadence",
            "check",
            "--contract",
            str(FIXTURES / "malformed.yaml"),
            "--render-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout.startswith("WHY: malformed YAML")
    assert "Traceback" not in result.stdout + result.stderr


def test_catch_up_and_persistent_cannot_contradict(tmp_path):
    value = copy.deepcopy(valid_contract())
    value["triggers"][0]["spec"]["persistent"] = True
    with pytest.raises(cadence.CadenceError, match=r"WHY:.*requires persistent=false"):
        cadence.check_contract(write_contract(tmp_path, value), tmp_path / "units")
