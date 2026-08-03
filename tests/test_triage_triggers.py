import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory import triage_agent


NOW = datetime(2026, 8, 2, 20, 0, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO / "deploy/triage-triggers/install_triggers.py"
SPEC = importlib.util.spec_from_file_location("install_triage_triggers", INSTALLER_PATH)
assert SPEC is not None and SPEC.loader is not None
install_triggers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_triggers)


def _sender(tmp_path):
    script = tmp_path / "sender.py"
    capture = tmp_path / "pings.jsonl"
    script.write_text(
        "import json, pathlib, sys\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "with p.open('a', encoding='utf-8') as h:\n"
        "    h.write(json.dumps({'text': sys.argv[2]}) + '\\n')\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script), str(capture), "{text}"]


def _setup(tmp_path, *, outbox_relative="state/outbox.jsonl"):
    root = tmp_path / "repo"
    root.mkdir()
    outbox = root / outbox_relative
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_text("", encoding="utf-8")
    config = {
        "outboxes": [str(outbox)],
        "ping": _sender(tmp_path),
        "max_pings_per_run": 1,
        "digest_cooldown_hours": 6,
    }
    return root, outbox, config


def _append(path, *rows):
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _row(department, issue, fingerprint):
    return {
        "kind": "escalation",
        "department": department,
        "issue": issue,
        "context": {"fingerprint": fingerprint},
    }


def _department(root, name):
    charter = root / "departments" / name / "charter.yaml"
    charter.parent.mkdir(parents=True, exist_ok=True)
    charter.write_text(
        f"""department: {name}
owner: owner
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [autonomy_state]
setpoints:
  objectives:
    completion:
      label: Completed loops
      minimum: 1
      target: 2
      unit: loops
thresholds:
  backlog_aging_min: 1
""",
        encoding="utf-8",
    )
    registry = root / "estate" / "registry.d" / f"{name}.yaml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(f"entries:\n  - id: {name}\n", encoding="utf-8")


def _initialize(root, config):
    triage_agent.run(root, config, execute=True, now=NOW)


def _jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_trigger_unit_records_trigger_in_receipt_and_audit(tmp_path):
    root, _, config = _setup(tmp_path)
    _department(root, "podcast")
    _initialize(root, config)

    receipt = triage_agent.run(
        root,
        config,
        execute=True,
        now=NOW,
        trigger_unit="podcast-loop.service",
    )

    trigger = {"kind": "unit_failure", "ref": "podcast-loop.service"}
    assert receipt["trigger"] == trigger
    assert receipt["by_class"] == {"infra_restartable": 1}
    assert _jsonl(root / "state/triage/audit.jsonl")[-1]["trigger"] == trigger


def test_unit_department_items_lead_digest_and_name_summoning_failure(tmp_path):
    root, outbox, config = _setup(tmp_path)
    _department(root, "podcast")
    _department(root, "social")
    _initialize(root, config)
    _append(
        outbox,
        _row("social", "OAuth expired for social", "social-auth"),
        _row("podcast", "OAuth expired for podcast", "podcast-auth"),
    )

    receipt = triage_agent.run(
        root,
        config,
        execute=False,
        now=NOW,
        trigger_unit="podcast-loop.service",
    )

    digest = receipt["digest"]["text"]
    assert digest.startswith("[triage] summoned by podcast-loop failure:")
    assert digest.index("OAuth expired for podcast") < digest.index("OAuth expired for social")
    assert receipt["decisions"][0]["fingerprint"] == "podcast-auth"


def test_trigger_path_resolves_department_and_prioritizes_its_rows(tmp_path):
    root, outbox, config = _setup(
        tmp_path, outbox_relative="departments/podcast/state/outbox.jsonl"
    )
    _department(root, "podcast")
    _initialize(root, config)
    _append(
        outbox,
        _row("social", "OAuth expired for social", "path-social"),
        _row("podcast", "OAuth expired for podcast", "path-podcast"),
    )

    receipt = triage_agent.run(
        root,
        config,
        execute=False,
        now=NOW,
        trigger_path=str(outbox),
    )

    assert receipt["trigger"] == {"kind": "outbox_append", "ref": str(outbox)}
    assert receipt["decisions"][0]["fingerprint"] == "path-podcast"
    assert receipt["digest"]["text"].startswith(
        "[triage] summoned by podcast outbox append:"
    )


def test_unmappable_unit_is_global_infra_candidate_with_trigger_recorded(tmp_path):
    root, _, config = _setup(tmp_path)
    _initialize(root, config)

    receipt = triage_agent.run(
        root,
        config,
        execute=True,
        now=NOW,
        trigger_unit="orphan-worker.service",
    )

    assert receipt["trigger"] == {
        "kind": "unit_failure",
        "ref": "orphan-worker.service",
    }
    assert receipt["by_class"] == {"infra_restartable": 1}
    assert receipt["decisions"] == [
        {
            "fingerprint": "unit-failure:orphan-worker.service",
            "class": "infra_restartable",
            "action": "proposed",
        }
    ]
    assert receipt["proposal_plan"][0]["selfheal_nodes"] == ["orphan-worker.service"]


def test_installer_dry_run_writes_nothing_and_lists_every_file(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    unit_dir = tmp_path / "units"

    receipt = install_triggers.install(
        root,
        unit_dir,
        units=["podcast-loop"],
        outboxes=["departments/podcast/state/outbox.jsonl"],
        dry_run=True,
    )

    assert len(receipt["files_planned"]) == 4
    assert receipt["files_would_write"] == receipt["files_planned"]
    assert receipt["files_written"] == []
    assert receipt["systemctl_invoked"] is False
    assert not unit_dir.exists()


def test_installer_writes_exact_dropin_and_second_run_is_noop(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    unit_dir = tmp_path / "units"

    first = install_triggers.install(root, unit_dir, units=["podcast-loop"])
    dropin = unit_dir / "podcast-loop.service.d/10-triage-onfailure.conf"
    second = install_triggers.install(root, unit_dir, units=["podcast-loop"])

    assert dropin.read_text(encoding="utf-8") == (
        "[Unit]\nOnFailure=loop-factory-triage@%n.service\n"
    )
    assert str(dropin) in first["files_written"]
    assert second["files_written"] == []
    assert set(second["files_unchanged"]) == set(second["files_planned"])


def test_installer_refuses_conflicting_dropin_without_force(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    unit_dir = tmp_path / "units"
    dropin = unit_dir / "podcast-loop.service.d/10-triage-onfailure.conf"
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Unit]\nOnFailure=someone-else.service\n", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        install_triggers.install(root, unit_dir, units=["podcast-loop"])

    assert dropin.read_text(encoding="utf-8") == (
        "[Unit]\nOnFailure=someone-else.service\n"
    )


def test_rendered_triage_instance_contains_substituted_repo_paths(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    unit_dir = tmp_path / "units"

    install_triggers.install(root, unit_dir)

    text = (unit_dir / "loop-factory-triage@.service").read_text(encoding="utf-8")
    assert "{{REPO}}" not in text
    assert f"WorkingDirectory={root}" in text
    assert (
        f"ExecStart=/usr/bin/python3 -m factory.triage_agent --repo-root {root} "
        "--config %h/.config/loop-factory/triage.yaml --execute --trigger-unit %i"
    ) in text
