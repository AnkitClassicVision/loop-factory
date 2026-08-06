import json
import re
from pathlib import Path


RUNTIME = Path(__file__).parents[1] / "runtime"
SCRIPT = RUNTIME / "podcast_daily.sh"
ROSTER = RUNTIME / "run-roster.json"


def _roster_nodes():
    return json.loads(ROSTER.read_text(encoding="utf-8"))["nodes"]


def _daily_chain_nodes():
    # Scan from mint to end-of-file: the conductor tick legitimately runs
    # AFTER the manager/boards (observer position). Nodes after the
    # run-manifest verify step must be required:false in the roster — the
    # verifier would otherwise mark them missing every day.
    text = SCRIPT.read_text(encoding="utf-8")
    chain = text[text.index("run_manifest mint"):]
    return re.findall(r"runtime/([a-z_]+)\.py", chain)


def test_nodes_invoked_after_verify_are_not_required():
    text = SCRIPT.read_text(encoding="utf-8")
    tail = text[text.index("run_manifest verify"):]
    tail_nodes = set(re.findall(r"runtime/([a-z_]+)\.py", tail))
    for entry in _roster_nodes():
        if entry["node"] in tail_nodes:
            assert entry["required"] is False, (
                f"{entry['node']!r} runs after the verify step; required:true "
                "would make every verdict red")


def test_every_required_roster_node_is_invoked_by_the_daily_chain():
    invoked = set(_daily_chain_nodes())
    for entry in _roster_nodes():
        node = entry["node"]
        assert node in invoked, f"roster node {node!r} is absent from the daily chain"
        if entry["required"]:
            source = (RUNTIME / f"{node}.py").read_text(encoding="utf-8")
            assert "emit_record" in source or "timed_emit" in source, (
                f"required roster node {node!r} has no completion-record call"
            )


def test_every_daily_chain_runtime_node_appears_in_the_roster():
    roster_nodes = {entry["node"] for entry in _roster_nodes()}
    assert set(_daily_chain_nodes()) <= roster_nodes
