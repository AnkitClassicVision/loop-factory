from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from factory.authority import load as load_authority
from kernel.run_manifest import _validate_roster


ROOT = Path(__file__).parents[1]
DEPARTMENTS = ("sales", "podcast", "outreach", "social")


def test_every_current_department_has_a_release_bound_wrapper_contract():
    for department in DEPARTMENTS:
        root = ROOT / "departments" / department
        roster = json.loads((root / "runtime" / "run-roster.json").read_text(encoding="utf-8"))
        nodes, entrypoint = _validate_roster(roster, department)
        assert nodes
        assert (root / entrypoint["path"]).is_file()
        assert (root / entrypoint["driver"]["path"]).is_file()
        service = root / entrypoint["service_source"]
        timer = root / entrypoint["timer_source"]
        assert service.is_file()
        assert timer.is_file()
        body = service.read_text(encoding="utf-8")
        assert f"factory.run_driver --department {department}" in body
        assert entrypoint["path"] not in body
        assert "OnCalendar=" in timer.read_text(encoding="utf-8")


def test_registry_cadence_names_only_the_canonical_source_timer_and_no_live_claim():
    for department in DEPARTMENTS:
        roster = json.loads(
            (ROOT / "departments" / department / "runtime" / "run-roster.json").read_text(encoding="utf-8")
        )
        registry = (ROOT / "estate" / "registry.d" / f"{department}.yaml").read_text(encoding="utf-8")
        assert roster["entrypoint"]["timer"] in registry
        assert "daily via source-controlled" in registry
        assert "deployment state unverified" in registry


def test_legacy_worker_trigger_sources_are_inert_and_not_enableable():
    legacy = ROOT / "departments" / "podcast" / "runtime" / "systemd"
    service = (legacy / "podcast-daily-department.service").read_text(encoding="utf-8")
    timer = (legacy / "podcast-daily-department.timer").read_text(encoding="utf-8")
    assert "ConditionPathExists=/run/loop-factory/legacy-podcast-trigger-never-enable" in service
    assert "ExecStart=/usr/bin/false" in service
    assert "podcast_daily.sh" not in service
    assert "ConditionPathExists=/run/loop-factory/legacy-podcast-trigger-never-enable" in timer
    assert "[Install]" not in timer


def test_every_current_department_has_one_valid_authority_map():
    for department in DEPARTMENTS:
        authority = load_authority(
            ROOT / "departments" / department / "authority-map.json",
            department=department,
        )
        execute = next(entry for entry in authority["actions"] if entry["action"] == "execute")
        assert execute["approval_required"] is True
        assert execute["proof"] == "target_readback"


@pytest.mark.parametrize(
    ("department", "script", "root_var"),
    (
        ("sales", "sales_daily.sh", "SALES_REPO_ROOT"),
        ("podcast", "podcast_daily.sh", "PODCAST_REPO_ROOT"),
        ("outreach", "outreach_daily.sh", "OUTREACH_REPO_ROOT"),
        ("social", "social_factory_daily.sh", None),
    ),
)
def test_daily_entrypoints_refuse_without_factory_context(tmp_path, department, script, root_var):
    env = {"PATH": os.environ["PATH"]}
    if root_var:
        env[root_var] = str(tmp_path)
    result = subprocess.run(
        ["bash", str(ROOT / "departments" / department / "runtime" / script)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Factory run identity and record spool are required" in result.stderr
