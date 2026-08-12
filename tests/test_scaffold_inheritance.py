"""A future department inherits the Loop Factory v2 contracts at F0."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.record_fixture import promote_factory_records


pytestmark = pytest.mark.usefixtures("factory_record_spool")


ROOT = Path(__file__).resolve().parents[1]


def _load_scaffold():
    spec = importlib.util.spec_from_file_location("scaffold_inheritance", ROOT / "factory/scaffold.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SC = _load_scaffold()


def test_scaffold_inherits_eval_registry(tmp_path):
    SC.scaffold_department("future", root=tmp_path)
    registry = tmp_path / "departments/future/runtime/eval_registry.yaml"
    assert registry.is_file()
    text = registry.read_text(encoding="utf-8")
    assert "tier1:" in text
    assert "# tier2:" in text
    assert "advisory-only" in text


def test_scaffolded_eval_registry_loads_through_the_real_loader(tmp_path):
    """Seam regression: the template must satisfy evalregistry.load_registry."""
    SC.scaffold_department("future", root=tmp_path)
    from factory.evalregistry import load_registry

    registry = load_registry(tmp_path / "departments/future/runtime/eval_registry.yaml")
    assert registry["default"]["tier1"] == [
        "schema_valid",
        "required_fields_present",
        "duplicate_free",
        "dates_valid",
        "permissions_valid",
        "external_actions_taken_valid",
    ]
    assert "tier2" not in registry["default"]


def test_scaffold_charter_carries_commented_objectives(tmp_path):
    SC.scaffold_department("future", root=tmp_path)
    charter = (tmp_path / "departments/future/charter.yaml").read_text(encoding="utf-8")
    assert "# objectives:" in charter
    for field in ("label", "setpoint", "minimum", "target", "unit"):
        assert f"#     {field}:" in charter
    assert "TODO_F1" in charter


def test_scaffold_daily_script_regenerates_estate_and_department_boards(tmp_path):
    SC.scaffold_department("future", root=tmp_path)
    script = (tmp_path / "departments/future/runtime/future_daily.sh").read_text(encoding="utf-8")
    assert 'DEPARTMENT="future"' in script
    assert f'REPO="{tmp_path.resolve()}"' in script
    assert 'python3 -m factory.boardfeed --repo-root "${REPO}"' in script
    assert 'python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" --out "${REPO}/estate/state/board.html"' in script
    assert '--department "${DEPARTMENT}"' in script


def test_scaffold_runtime_node_uses_fail_closed_runrecord_pattern(tmp_path):
    SC.scaffold_department("future", root=tmp_path)
    node = (tmp_path / "departments/future/runtime/runtime_node.py").read_text(encoding="utf-8")
    assert "from factory import runrecord" in node
    assert "runrecord.timed_emit(" in node
    assert "runrecord.emit_record" in node
    assert '"dedupe_key": f"{utc_date}-{NODE}"' in node
    assert "external_actions_taken=0" in node
    assert "fails closed" in node


def test_scaffold_runtime_node_emits_v2_record(tmp_path):
    SC.scaffold_department("future", root=tmp_path)
    node = tmp_path / "departments/future/runtime/runtime_node.py"
    completed = subprocess.run(
        [sys.executable, str(node)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    promote_factory_records(tmp_path / "departments/future/state")
    assert (tmp_path / "departments/future/state/runs-v2.jsonl").is_file()


def test_scaffold_instantiates_subscription_engine_shape(tmp_path):
    SC.scaffold_department("future", root=tmp_path)
    engines = (tmp_path / "departments/future/runtime/engines.example.yaml").read_text(encoding="utf-8")
    assert "command:" in engines
    assert "auth_class: oauth_cli" in engines
    assert "auth_probe:" in engines


def test_scaffold_refuses_existing_department_charter(tmp_path):
    SC.scaffold_department("future", root=tmp_path)
    with pytest.raises(FileExistsError):
        SC.scaffold_department("future", root=tmp_path)


def test_scaffolded_empty_tree_builds_board_feed(tmp_path):
    SC.scaffold_department("future", root=tmp_path)
    completed = subprocess.run(
        [sys.executable, "-m", "factory.boardfeed", "--repo-root", str(tmp_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "estate/state/board-feed.ndjson").is_file()
    assert '"departments":0' in completed.stdout
    assert '"projection_status":"incomplete"' in completed.stdout
