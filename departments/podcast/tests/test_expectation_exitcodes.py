"""Exit-code contract for expectation_reconcile: 0 ok / 2 findings / 1 crash.

The daily shell (podcast_daily.sh) treats 2 as a valid alarm verdict whose
observations the compare/dedup chain must process, exactly like
dag_supervisor. Any other nonzero is a node failure that stops the chain.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "runtime" / "expectation_reconcile.py"
REPO = Path(__file__).parents[3]


def _invoke(state_dir, sources, manifests):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--shadow",
         "--state-dir", str(state_dir),
         "--sources", str(sources),
         "--manifests", str(manifests)],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
    )


def test_no_manifests_is_findings_verdict_exit_2(tmp_path):
    state = tmp_path / "state"; state.mkdir()
    sources = tmp_path / "sources"; sources.mkdir()
    manifests = tmp_path / "manifests"; manifests.mkdir()  # empty: fail closed
    result = _invoke(state, sources, manifests)
    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload["status"] == "fail"
    rows = [json.loads(line) for line in
            (state / "observations.jsonl").read_text().splitlines()]
    assert rows[-1]["sensor"] == "expectation"
    assert rows[-1]["status"] == "unknown"


def test_crash_is_node_failure_exit_1(tmp_path):
    # state-dir path occupied by a FILE: _append raises OSError -> crash lane.
    state = tmp_path / "state"; state.write_text("not a directory")
    sources = tmp_path / "sources"; sources.mkdir()
    manifests = tmp_path / "manifests"; manifests.mkdir()
    result = _invoke(state, sources, manifests)
    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload["errors"]
