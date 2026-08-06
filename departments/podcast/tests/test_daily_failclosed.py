import json
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "runtime" / "podcast_daily.sh"


LAUNCH_STUB = '''
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
separator = args.index("--")
command = args[separator + 1:]
target = Path(command[1]).stem
fingerprint = command[command.index("--fingerprint") + 1]
behavior = json.loads(Path(os.environ["STUB_BEHAVIOR"]).read_text())
action = behavior.get(fingerprint, {}).get(target, "ok")
with Path(os.environ["STUB_CALLS"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"fingerprint": fingerprint, "stage": target}) + "\\n")
if action == "crash":
    raise SystemExit(17)
if action == "malformed":
    print("not-json")
elif action == "refuse" and target == "heal_select":
    state_dir = Path(command[command.index("--state-dir") + 1])
    receipt = {
        "fingerprint": fingerprint, "playbook": "", "mode": "proposed",
        "commands": [], "result": "refused", "detail": "fixture refusal",
    }
    with (state_dir / "heals.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt) + "\\n")
elif target == "heal_select":
    print(json.dumps({"id": "fixture-playbook"}))
else:
    print(json.dumps({"fingerprint": fingerprint, "result": "proposed"}))
'''


def _run(tmp_path, incidents, behavior):
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    (repo / "factory").mkdir(parents=True)
    state.mkdir()
    (repo / "factory" / "launch.py").write_text(LAUNCH_STUB, encoding="utf-8")
    (state / "incidents.json").write_text(json.dumps(incidents), encoding="utf-8")
    behavior_path = tmp_path / "behavior.json"
    calls_path = tmp_path / "calls.jsonl"
    behavior_path.write_text(json.dumps(behavior), encoding="utf-8")
    env = {
        **os.environ,
        "PODCAST_REPO_ROOT": str(repo),
        "PODCAST_STATE_DIR": str(state),
        "STUB_BEHAVIOR": str(behavior_path),
        "STUB_CALLS": str(calls_path),
    }
    completed = subprocess.run(
        ["bash", str(SCRIPT), "--heal-phase-only"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    calls = []
    if calls_path.exists():
        calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    failures = []
    failure_path = state / "heal_failures.jsonl"
    if failure_path.exists():
        failures = [json.loads(line) for line in failure_path.read_text().splitlines()]
    return completed, state, calls, failures


def _open(*fingerprints):
    return {fingerprint: {"state": "open"} for fingerprint in fingerprints}


def test_select_crash_records_once_and_skips_apply_and_verify(tmp_path):
    completed, _, calls, failures = _run(
        tmp_path, _open("fp-a"), {"fp-a": {"heal_select": "crash"}}
    )
    assert completed.returncode == 0
    assert calls == [{"fingerprint": "fp-a", "stage": "heal_select"}]
    assert [(row["fingerprint"], row["stage"]) for row in failures] == [
        ("fp-a", "heal_select")
    ]


def test_apply_crash_records_once_and_skips_verify(tmp_path):
    completed, _, calls, failures = _run(
        tmp_path, _open("fp-a"), {"fp-a": {"heal_apply": "crash"}}
    )
    assert completed.returncode == 0
    assert [row["stage"] for row in calls] == ["heal_select", "heal_apply"]
    assert len(failures) == 1 and failures[0]["stage"] == "heal_apply"


def test_other_incident_continues_after_one_fails(tmp_path):
    completed, _, calls, failures = _run(
        tmp_path,
        _open("fp-a", "fp-b"),
        {"fp-a": {"heal_select": "crash"}},
    )
    assert completed.returncode == 0
    assert [row["stage"] for row in calls if row["fingerprint"] == "fp-b"] == [
        "heal_select", "heal_apply", "heal_verify"
    ]
    assert [row["fingerprint"] for row in failures] == ["fp-a"]


def test_incident_load_failure_aborts_nonzero(tmp_path):
    completed, state, calls, failures = _run(tmp_path, {}, {})
    (state / "incidents.json").write_text("not-json", encoding="utf-8")
    env = {
        **os.environ,
        "PODCAST_REPO_ROOT": str(tmp_path / "repo"),
        "PODCAST_STATE_DIR": str(state),
        "STUB_BEHAVIOR": str(tmp_path / "behavior.json"),
        "STUB_CALLS": str(tmp_path / "calls.jsonl"),
    }
    completed = subprocess.run(
        ["bash", str(SCRIPT), "--heal-phase-only"], env=env, capture_output=True
    )
    assert completed.returncode != 0
    assert "incident-list load failed" in completed.stderr.decode()
    assert calls == [] and failures == []


def test_success_and_recorded_refusal_keep_happy_path_without_failure_receipt(tmp_path):
    completed, state, calls, failures = _run(
        tmp_path,
        _open("fp-refused", "fp-success"),
        {"fp-refused": {"heal_select": "refuse"}},
    )
    assert completed.returncode == 0
    assert [row["stage"] for row in calls if row["fingerprint"] == "fp-refused"] == [
        "heal_select"
    ]
    assert [row["stage"] for row in calls if row["fingerprint"] == "fp-success"] == [
        "heal_select", "heal_apply", "heal_verify"
    ]
    refusal = json.loads((state / "heals.jsonl").read_text().strip())
    assert refusal["result"] == "refused"
    assert failures == []


def test_expectation_line_has_no_silent_bypass():
    text = SCRIPT.read_text(encoding="utf-8")
    expectation_lines = [l for l in text.splitlines() if "expectation_reconcile.py" in l]
    assert expectation_lines, "expectation_reconcile invocation missing from daily chain"
    assert not any("|| true" in l for l in expectation_lines), (
        "expectation_reconcile must not be silenced with || true; "
        "exit 2 is a findings verdict handled like dag_supervisor's alarm")
    assert "exp_rc" in text, "expected the rc-capture alarm-verdict pattern"
