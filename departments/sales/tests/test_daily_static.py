import json
import re
import subprocess
from pathlib import Path

from factory.authority import load as load_authority_map


DEPARTMENT = Path(__file__).parents[1]
RUNTIME = DEPARTMENT / "runtime"
SCRIPT = RUNTIME / "sales_daily.sh"
ROSTER = RUNTIME / "run-roster.json"
EXPECTED = [
    "intake_sensor", "qualify_scorer", "booked_sensor", "held_confirm_card",
    "held_sensor", "sense_gates", "floor_compiler_run", "conductor_tick",
]


def _text():
    return SCRIPT.read_text(encoding="utf-8")


def _roster():
    return json.loads(ROSTER.read_text(encoding="utf-8"))


def _invoked_nodes():
    text = _text()
    chain = text[text.index("runtime/intake_sensor.py"):]
    return re.findall(r"runtime/([a-z_]+)\.py", chain)


def test_daily_shell_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_chain_places_the_declared_driver_before_semantic_verification():
    text = _text()
    markers = [
        *[f"runtime/{node}.py" for node in EXPECTED],
        "factory/manager.py",
        "factory/human_in_the_loop.py\" push",
    ]
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert "run_manifest mint" not in text
    assert "run_manifest verify" not in text


def test_roster_is_a_strict_release_bound_execution_contract():
    roster = _roster()
    entries = roster["nodes"]
    assert roster["schema"] == "run-roster"
    assert roster["rev"] == 3
    assert roster["department"] == "sales"
    assert roster["entrypoint"] == {
        "timer": "sales-loop.timer",
        "service": "sales-loop.service",
        "timer_source": "systemd/sales-loop.timer",
        "service_source": "systemd/sales-loop.service",
        "path": "runtime/sales_daily.sh",
        "driver": {
            "node": "sense_gates", "subgraph": "SG-SENSE", "node_id": "N1",
            "impl": "runtime/sense_gates.py", "path": "runtime/sense_gates.py",
        },
    }
    assert [entry["ordinal"] for entry in entries] == list(range(1, len(entries) + 1))
    assert [entry["node"] for entry in entries] == EXPECTED
    assert _invoked_nodes() == EXPECTED
    assert all(entry["required"] is True for entry in entries)
    assert all(entry["allowed_terminal_statuses"] == ["ok", "blocked", "unknown"] for entry in entries)
    assert roster["contract"]["schema"] == "node-contract/v1"


def test_daily_entrypoint_does_not_self_mint_or_self_verify():
    assert "run_manifest mint" not in _text()
    assert "run_manifest verify" not in _text()


def test_source_controlled_canonical_unit_uses_the_trusted_driver_once_daily():
    service = (DEPARTMENT / "systemd" / "sales-loop.service").read_text(encoding="utf-8")
    timer = (DEPARTMENT / "systemd" / "sales-loop.timer").read_text(encoding="utf-8")
    assert "factory.run_driver --department sales --root /mnt/d_drive/repos/loop-factory --trigger daily" in service
    assert "runtime/sales_daily.sh" not in service
    assert "OnCalendar=daily" in timer


def test_sales_authority_map_keeps_execute_human_gated_and_readback_bound():
    authority = load_authority_map(DEPARTMENT / "authority-map.json", department="sales")
    execute = next(entry for entry in authority["actions"] if entry["action"] == "execute")
    approve = next(entry for entry in authority["actions"] if entry["action"] == "approve")
    assert execute["external_effect"] is True
    assert execute["approval_required"] is True
    assert execute["proof"] == "target_readback"
    assert approve["owner"] == "ankit"
