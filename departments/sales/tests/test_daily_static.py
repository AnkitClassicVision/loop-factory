import json
import re
import subprocess
from pathlib import Path


RUNTIME = Path(__file__).parents[1] / "runtime"
SCRIPT = RUNTIME / "sales_daily.sh"
ROSTER = RUNTIME / "run-roster.json"
EXPECTED = [
    "intake_sensor", "qualify_scorer", "booked_sensor", "held_confirm_card",
    "held_sensor", "sense_gates", "floor_compiler_run",
]
POST_VERIFY = ["conductor_tick"]  # observer position; required:false (P4 lesson)


def _text():
    return SCRIPT.read_text(encoding="utf-8")


def _roster():
    return json.loads(ROSTER.read_text(encoding="utf-8"))


def _invoked_nodes():
    text = _text()
    chain = text[text.index("run_manifest mint"):]
    return re.findall(r"runtime/([a-z_]+)\.py", chain)


def test_daily_shell_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_chain_order_and_manifest_boundaries_are_pinned():
    text = _text()
    markers = ["run_manifest mint", *[f"runtime/{node}.py" for node in EXPECTED], "run_manifest verify", "factory/manager.py", "factory/human_in_the_loop.py\" push"]
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_roster_matches_whole_daily_runtime_chain():
    roster = _roster()
    entries = roster["nodes"]
    assert roster["schema"] == "run-roster"
    assert roster["rev"] == 1
    assert roster["department"] == "sales"
    assert [entry["ordinal"] for entry in entries] == list(range(1, len(entries) + 1))
    assert [entry["node"] for entry in entries] == EXPECTED + POST_VERIFY
    assert _invoked_nodes() == EXPECTED + POST_VERIFY
    required = {entry["node"]: entry["required"] for entry in entries}
    assert all(required[node] is True for node in EXPECTED)
    assert all(required[node] is False for node in POST_VERIFY)


def test_no_required_roster_node_runs_after_verify():
    tail = _text()[_text().index("run_manifest verify"):]
    tail_nodes = set(re.findall(r"runtime/([a-z_]+)\.py", tail))
    assert tail_nodes.isdisjoint(EXPECTED)
    assert set(POST_VERIFY) <= tail_nodes
