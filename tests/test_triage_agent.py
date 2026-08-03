import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from factory import triage_agent


NOW = datetime(2026, 8, 2, 20, 0, tzinfo=timezone.utc)


def _row(issue, *, department="demo", fingerprint=None, eli5=None, **context):
    body = {
        "kind": "escalation",
        "department": department,
        "issue": issue,
        "context": context,
        "ts": NOW.isoformat(),
    }
    if fingerprint:
        body["context"]["fingerprint"] = fingerprint
    if eli5:
        body["eli5"] = eli5
    return body


def _append(path, *rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _sender(tmp_path):
    script = tmp_path / "fake_sender.py"
    capture = tmp_path / "pings.jsonl"
    script.write_text(
        "import json, pathlib, sys\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "with p.open('a', encoding='utf-8') as h:\n"
        "    h.write(json.dumps({'text': sys.argv[2]}) + '\\n')\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script), str(capture), "{text}"], capture


def _setup(tmp_path, *, max_pings=1, cooldown=6):
    root = tmp_path / "repo"
    root.mkdir()
    outbox = root / "state" / "decisions_outbox.jsonl"
    outbox.parent.mkdir(parents=True)
    outbox.write_text("", encoding="utf-8")
    ping, capture = _sender(tmp_path)
    config = {
        "outboxes": [str(outbox)],
        "ping": ping,
        "max_pings_per_run": max_pings,
        "digest_cooldown_hours": cooldown,
    }
    return root, outbox, config, capture


def _initialize(root, config, now=NOW):
    return triage_agent.run(root, config, execute=True, now=now)


def _write_charter(root, department="demo"):
    charter = root / "departments" / department / "charter.yaml"
    charter.parent.mkdir(parents=True, exist_ok=True)
    charter.write_text(
        f"""department: {department}
owner: owner
autonomy_state: shadow
immutable_safety_invariants:
  heal_may_not_modify: [autonomy_state]
setpoints:
  objectives:
    hopper_depth:
      label: Recordings in the hopper
      minimum: 2
      target: 6
      unit: recordings
thresholds:
  backlog_aging_min: 1
""",
        encoding="utf-8",
    )
    return charter


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_first_run_cursors_to_current_eof_and_processes_zero_rows(tmp_path):
    root, outbox, config, _ = _setup(tmp_path)
    _append(outbox, _row("AWS SSO expired", fingerprint="old-auth"))

    receipt = _initialize(root, config)

    cursor = json.loads((root / "state/triage/cursor.json").read_text())
    assert receipt["rows"] == 0
    assert receipt["by_class"] == {}
    assert cursor[str(outbox)] == outbox.stat().st_size
    assert not (root / "state/triage/audit.jsonl").exists()


def test_appended_row_after_first_run_is_processed(tmp_path):
    root, outbox, config, _ = _setup(tmp_path)
    _initialize(root, config)
    _append(outbox, _row("worker.service failed", fingerprint="new-unit"))

    receipt = triage_agent.run(root, config, execute=True, now=NOW)

    assert receipt["rows"] == 1
    assert receipt["by_class"] == {"infra_restartable": 1}
    assert receipt["proposals_written"] == 2


def test_classifies_infra_restartable():
    assert triage_agent.classify(_row("receipt_stale for worker.timer")) == "infra_restartable"


def test_classifies_release_drift():
    assert triage_agent.classify(_row("release drift: pinned tree mismatch")) == "release_drift"


def test_classifies_auth_needed():
    assert triage_agent.classify(_row("AWS SSO login expired with 401")) == "auth_needed"


def test_classifies_objective_breach():
    row = _row("hopper below minimum", finding_code="OBJECTIVE_BELOW_MIN")
    assert triage_agent.classify(row) == "objective_breach"


def test_classifies_approval_pending():
    assert triage_agent.classify(_row("pending approval for release")) == "approval_pending"


def test_classifies_unknown():
    assert triage_agent.classify(_row("unexpected purple result")) == "unknown"


def test_objective_breach_is_enriched_from_fixture_charter(tmp_path):
    root, outbox, config, _ = _setup(tmp_path)
    _write_charter(root)
    _initialize(root, config)
    _append(
        outbox,
        _row(
            "OBJECTIVE_BELOW_MIN: hopper_depth",
            fingerprint="goal-1",
            objective="hopper_depth",
            observed=0,
        ),
    )

    receipt = triage_agent.run(root, config, execute=False, now=NOW)

    objective = receipt["decisions"][0]["objective"]
    assert objective == {
        "key": "hopper_depth",
        "label": "Recordings in the hopper",
        "minimum": 2,
        "target": 6,
        "unit": "recordings",
        "observed": 0,
    }
    assert "Recordings in the hopper 0 < min 2" in receipt["digest"]["text"]


def test_dedupe_suppresses_repeat_fingerprint_across_runs(tmp_path):
    root, outbox, config, _ = _setup(tmp_path)
    _initialize(root, config)
    row = _row("worker.service failed", fingerprint="same-unit")
    _append(outbox, row)
    triage_agent.run(root, config, execute=True, now=NOW)
    _append(outbox, row)

    receipt = triage_agent.run(root, config, execute=True, now="2026-08-02T21:00:00Z")

    assert receipt["by_class"] == {}
    assert receipt["decisions"] == [
        {
            "fingerprint": "same-unit",
            "class": "infra_restartable",
            "action": "suppressed_duplicate",
        }
    ]


def test_resolved_fingerprint_reopens_and_is_processed(tmp_path):
    root, outbox, config, _ = _setup(tmp_path)
    _initialize(root, config)
    row = _row("worker.service failed", fingerprint="reopen-unit")
    _append(outbox, row)
    triage_agent.run(root, config, execute=True, now=NOW)
    _append(
        outbox,
        {"kind": "resolution", "fingerprint": "reopen-unit", "marker": "resolved"},
        row,
    )

    receipt = triage_agent.run(root, config, execute=True, now="2026-08-03T01:00:00Z")

    assert receipt["rows"] == 2
    assert receipt["by_class"] == {"infra_restartable": 1}
    assert any(item["action"] == "proposed" for item in receipt["decisions"])


def test_ringer_manifests_are_valid_self_contained_and_exclude_human_classes(tmp_path):
    root, outbox, config, _ = _setup(tmp_path)
    _initialize(root, config)
    _append(
        outbox,
        _row("worker.service failed", fingerprint="infra-1", eli5="worker service stopped"),
        _row("release drift mismatch", department="alpha", fingerprint="drift-1"),
        _row("OAuth token expired", fingerprint="auth-1"),
        _row("strange purple state", fingerprint="unknown-1"),
    )

    receipt = triage_agent.run(root, config, execute=True, now=NOW)

    manifests = sorted((root / "ringer/triage-proposals").glob("*.json"))
    assert len(manifests) == 2
    assert receipt["by_class"] == {
        "infra_restartable": 1,
        "release_drift": 1,
        "auth_needed": 1,
        "unknown": 1,
    }
    payloads = [json.loads(path.read_text()) for path in manifests]
    assert {payload["run_name"] for payload in payloads} == {
        "triage-infra_restartable",
        "triage-release_drift",
    }
    for payload in payloads:
        assert payload["tasks"]
        assert all(task["engine"] == "codex" for task in payload["tasks"])
        assert all("Department:" in task["spec"] and task["check"] for task in payload["tasks"])
        assert "OAuth" not in json.dumps(payload)
        assert "purple" not in json.dumps(payload)


def test_selfheal_proposal_card_is_created_with_auto_apply_false(tmp_path):
    root, outbox, config, _ = _setup(tmp_path)
    _initialize(root, config)
    _append(outbox, _row("alpha.timer failed", fingerprint="heal-1"))

    triage_agent.run(root, config, execute=True, now=NOW)

    cards = list((root / "state/triage/heal_proposals").glob("*.json"))
    assert len(cards) == 1
    card = json.loads(cards[0].read_text())
    assert card["auto_apply"] is False
    assert card["fix_class"] == "runtime_config"
    assert card["node"] == "alpha.timer"
    assert card["rung"] == "L2"


def test_digest_sends_one_ping_max_and_uses_owner_goal_language(tmp_path):
    root, outbox, config, capture = _setup(tmp_path, max_pings=1)
    _write_charter(root)
    _initialize(root, config)
    _append(
        outbox,
        _row(
            "OBJECTIVE_BELOW_MIN hopper_depth",
            fingerprint="goal-one",
            objective="hopper_depth",
            observed=0,
        ),
        _row("AWS SSO expired", fingerprint="auth-two", eli5="AWS SSO expired (sweeper blocked)"),
    )

    receipt = triage_agent.run(root, config, execute=True, now=NOW)

    pings = _read_jsonl(capture)
    assert receipt["pinged"] is True
    assert len(pings) == 1
    assert "2 need you" in pings[0]["text"]
    assert "Recordings in the hopper 0 < min 2" in pings[0]["text"]


def test_digest_cooldown_suppresses_identical_reopened_digest(tmp_path):
    root, outbox, config, capture = _setup(tmp_path, cooldown=6)
    _initialize(root, config)
    row = _row("AWS SSO expired", fingerprint="cool-auth")
    _append(outbox, row)
    triage_agent.run(root, config, execute=True, now=NOW)
    _append(
        outbox,
        {"kind": "resolution", "fingerprint": "cool-auth", "marker": "resolved"},
        row,
    )

    receipt = triage_agent.run(root, config, execute=True, now="2026-08-02T21:00:00Z")

    assert len(_read_jsonl(capture)) == 1
    assert receipt["pinged"] is False
    assert receipt["digest"]["suppressed_by_cooldown"] is True
    assert receipt["decisions"][-1]["action"] == "skipped"


def test_different_digest_sends_during_cooldown_window(tmp_path):
    root, outbox, config, capture = _setup(tmp_path, cooldown=6)
    _initialize(root, config)
    _append(outbox, _row("AWS SSO expired", fingerprint="auth-a"))
    triage_agent.run(root, config, execute=True, now=NOW)
    _append(outbox, _row("GitHub OAuth login expired", fingerprint="auth-b"))

    receipt = triage_agent.run(root, config, execute=True, now="2026-08-02T21:00:00Z")

    assert receipt["pinged"] is True
    assert len(_read_jsonl(capture)) == 2


def test_dry_run_writes_nothing_and_pings_nothing(tmp_path):
    root, outbox, config, capture = _setup(tmp_path)
    _initialize(root, config)
    state_dir = root / "state/triage"
    before = {path.name: path.read_bytes() for path in state_dir.iterdir()}
    _append(outbox, _row("worker.service failed", fingerprint="dry-infra"))

    receipt = triage_agent.run(root, config, execute=False, now=NOW)

    after = {path.name: path.read_bytes() for path in state_dir.iterdir()}
    assert before == after
    assert receipt["dry_run"] is True
    assert receipt["proposals_written"] == 0
    assert receipt["proposal_plan"][0]["tasks"] == 1
    assert not (root / "ringer/triage-proposals").exists()
    assert not capture.exists()


def test_audit_has_one_fsynced_decision_row_per_input_decision(tmp_path):
    root, outbox, config, _ = _setup(tmp_path)
    _initialize(root, config)
    repeated = _row("alpha.timer failed", fingerprint="audit-auto")
    _append(
        outbox,
        repeated,
        _row("OAuth expired", fingerprint="audit-human"),
        _row("approval_pending", fingerprint="audit-approval"),
    )
    triage_agent.run(root, config, execute=True, now=NOW)
    _append(outbox, repeated)
    triage_agent.run(root, config, execute=True, now="2026-08-02T21:00:00Z")

    audit = _read_jsonl(root / "state/triage/audit.jsonl")
    assert len(audit) == 4
    assert {(row["row_fingerprint"], row["action"]) for row in audit} == {
        ("audit-auto", "proposed"),
        ("audit-human", "digested"),
        ("audit-approval", "skipped"),
        ("audit-auto", "suppressed_duplicate"),
    }


def test_cli_dry_run_prints_one_json_plan_without_writes(tmp_path, capsys):
    root, outbox, config, _ = _setup(tmp_path)
    config_path = tmp_path / "triage.yaml"
    config_path.write_text(yaml_dump(config), encoding="utf-8")
    _append(outbox, _row("old backlog", fingerprint="old"))

    assert triage_agent.main(
        ["--repo-root", str(root), "--config", str(config_path), "--now", NOW.isoformat()]
    ) == 0

    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] is True
    assert plan["rows"] == 0
    assert not (root / "state/triage").exists()


def yaml_dump(value):
    # Kept local so the production parser, not hand-authored YAML, is under test.
    import yaml

    return yaml.safe_dump(value, sort_keys=False)


def test_systemd_templates_are_disabled_by_convention_and_offset():
    root = Path(__file__).resolve().parents[1]
    service = (root / "templates/systemd/loop-factory-triage.service").read_text()
    timer = (root / "templates/systemd/loop-factory-triage.timer").read_text()
    assert "Installed DISABLED by convention" in service
    assert "--execute" in service
    assert "OnCalendar=*:5/30" in timer
    assert "Persistent=false" in timer
