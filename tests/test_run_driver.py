from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory import run_driver
from kernel import run_manifest


pytestmark = pytest.mark.usefixtures("factory_record_spool")


def _manifest(root, run_id="run-1"):
    dept = root / "departments" / "fake"
    dept.mkdir(parents=True, exist_ok=True)
    (dept / "charter.yaml").write_text(
        "department: fake\nowner: fixture\nautonomy_state: shadow\n"
        "immutable_safety_invariants:\n  heal_may_not_modify: [autonomy_state]\n"
        "capabilities: []\n",
        encoding="utf-8",
    )
    path = root / "state" / "run-manifests" / f"{run_id}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"entrypoint": {"path": "runtime/fake_daily.sh"}}))
    return path


def test_wrapper_binds_minted_run_id_and_only_executes_declared_entrypoint(tmp_path, monkeypatch):
    root = tmp_path
    dept = root / "departments" / "fake"
    runtime = dept / "runtime"
    runtime.mkdir(parents=True)
    entrypoint = runtime / "fake_daily.sh"
    entrypoint.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    manifest_path = _manifest(root)
    calls = []

    monkeypatch.setattr(
        run_manifest,
        "mint",
        lambda **_kwargs: {"run_id": "run-1", "manifest": str(manifest_path)},
    )
    monkeypatch.setattr(
        run_manifest,
        "verify",
        lambda **_kwargs: {"status": "green", "run_id": "run-1"},
    )
    monkeypatch.setattr(
        run_driver.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )

    result = run_driver.run(department="fake", root=root, trigger="daily")

    assert result["status"] == "green"
    assert len(calls) == 1
    assert calls[0][0] == ["/bin/bash", str(entrypoint)]
    assert calls[0][1]["env"]["LOOP_FACTORY_RUN_ID"] == "run-1"


def test_wrapper_child_cannot_observe_factory_signer(tmp_path, monkeypatch):
    root = tmp_path
    dept = root / "departments" / "fake"
    runtime = dept / "runtime"
    runtime.mkdir(parents=True)
    entrypoint = runtime / "fake_daily.sh"
    marker = root / "child-saw-signer"
    entrypoint.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ -n \"${OE_KERNEL_SIGNING_KEY+x}\" ]]; then touch \"%s\"; fi\n"
        "exit 0\n" % marker,
        encoding="utf-8",
    )
    manifest_path = _manifest(root, run_id="run-env")
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "test-signer")
    monkeypatch.setenv("UNRELATED_RUNTIME_VALUE", "must-not-leak")
    monkeypatch.setattr(
        run_manifest,
        "mint",
        lambda **_kwargs: {"run_id": "run-env", "manifest": str(manifest_path)},
    )
    monkeypatch.setattr(
        run_manifest,
        "verify",
        lambda **_kwargs: {"status": "green", "run_id": "run-env"},
    )

    result = run_driver.run(department="fake", root=root, trigger="daily")

    assert result["status"] == "green"
    assert not marker.exists()


def test_wrapper_fails_closed_when_mint_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(
        run_manifest,
        "mint",
        lambda **_kwargs: (_ for _ in ()).throw(run_manifest.ManifestRefused("binding_failed")),
    )
    monkeypatch.setattr(
        run_driver.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    result = run_driver.run(department="fake", root=tmp_path)

    assert result == {"status": "blocked", "reason": "binding_failed", "run_id": None}


def test_wrapper_blocks_post_mint_entrypoint_escape_without_execution(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"entrypoint": {"path": "../../outside.sh"}}))
    calls = []
    monkeypatch.setattr(
        run_manifest,
        "mint",
        lambda **_kwargs: {"run_id": "run-escape", "manifest": str(manifest_path)},
    )
    monkeypatch.setattr(
        run_driver.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = run_driver.run(department="fake", root=tmp_path)

    assert result == {"status": "blocked", "reason": "minted_entrypoint_unreadable", "run_id": None}
    assert calls == []


@pytest.mark.parametrize(
    ("entrypoint_exit_code", "expected_status"),
    ((0, "green"), (17, "red")),
)
def test_wrapper_runs_real_signed_fixture_and_honors_entrypoint_exit(
    tmp_path, monkeypatch, entrypoint_exit_code, expected_status
):
    root = tmp_path
    dept = root / "departments" / "fake"
    runtime = dept / "runtime"
    systemd = dept / "systemd"
    state = dept / "state"
    runtime.mkdir(parents=True)
    systemd.mkdir()
    state.mkdir()

    authority = {
        "schema": "authority-map/v1", "department": "fake",
        "actions": [
            {"action": "observe", "owner": "factory-manager", "actor": "factory_supervisor", "authority": "observe", "proof": "signed_observation", "external_effect": False, "approval_required": False},
            {"action": "plan", "owner": "fake-worker", "actor": "direct_worker", "authority": "draft", "proof": "release_bound_proposal", "external_effect": False, "approval_required": False},
            {"action": "approve", "owner": "human-owner", "actor": "human_gate", "authority": "approve", "proof": "signed_human_decision", "external_effect": False, "approval_required": False},
            {"action": "execute", "owner": "fake-executor", "actor": "dedicated_executor", "authority": "execute", "proof": "target_readback", "external_effect": True, "approval_required": True},
            {"action": "verify", "owner": "fake-verifier", "actor": "independent_verifier", "authority": "verify", "proof": "target_readback", "external_effect": False, "approval_required": False},
        ],
    }
    (dept / "authority-map.json").write_text(json.dumps(authority), encoding="utf-8")
    (dept / "charter.yaml").write_text(
        "department: fake\nowner: fixture\nautonomy_state: shadow\n"
        "immutable_safety_invariants:\n  heal_may_not_modify: [autonomy_state]\n"
        "capabilities: []\n",
        encoding="utf-8",
    )
    roster = {
        "schema": "run-roster", "rev": 2, "department": "fake",
        "entrypoint": {
            "timer": "fake-loop.timer", "service": "fake-loop.service",
            "timer_source": "systemd/fake-loop.timer", "service_source": "systemd/fake-loop.service",
            "path": "runtime/fake_daily.sh",
            "driver": {"node": "fake_driver", "path": "runtime/fake_driver.py"},
        },
        "nodes": [{"ordinal": 1, "node": "fake_driver", "required": True, "allowed_terminal_statuses": ["ok"]}],
    }
    (runtime / "run-roster.json").write_text(json.dumps(roster), encoding="utf-8")
    (runtime / "fake_driver.py").write_text("# release-bound driver marker\n", encoding="utf-8")
    receipt = state / "terminal-receipt.json"
    receipt.write_text('{"status":"complete"}\n', encoding="utf-8")
    repo_root = Path(__file__).parents[1]
    script = runtime / "fake_daily.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "python3 - \"$LOOP_FACTORY_RUN_ID\" <<'PY'\n"
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "from factory import runrecord\n"
        "root = Path.cwd()\ndept = root / 'departments' / 'fake'\nstate = dept / 'state'\n"
        "runrecord.emit_record(state, department='fake', node='fake_driver', status='ok', run_id=sys.argv[1], release=runrecord.read_release(dept), trigger={'kind':'time','id':'fake-loop.timer','dedupe_key':sys.argv[1]}, receipts=[{'kind':'terminal','path':'terminal-receipt.json'}])\n"
        f"PY\nexit {entrypoint_exit_code}\n",
        encoding="utf-8",
    )
    source_service = systemd / "fake-loop.service"
    source_service.write_text(
        f"[Service]\nExecStart=/usr/bin/python3 -m factory.run_driver --department fake --root {root}\n",
        encoding="utf-8",
    )
    (systemd / "fake-loop.timer").write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")

    artifacts = []
    for path in (dept / "authority-map.json", runtime / "run-roster.json", runtime / "fake_daily.sh", runtime / "fake_driver.py", source_service, systemd / "fake-loop.timer"):
        artifacts.append({"path": str(path.relative_to(dept)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    release = {"schema": "loop-factory-release", "rev": 1, "department": "fake", "source_ref": "fixture-ref", "artifacts": artifacts}
    release["hash"] = hashlib.sha256(json.dumps(release, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    release_dir = dept / "releases" / release["hash"]
    release_dir.mkdir(parents=True)
    (release_dir / "manifest.json").write_text(json.dumps(release), encoding="utf-8")
    (dept / "releases" / "current").write_text(release["hash"] + "\n", encoding="utf-8")

    installed_units = root / "installed-units"
    installed_units.mkdir()
    (installed_units / "fake-loop.service").write_text(source_service.read_text(encoding="utf-8"), encoding="utf-8")
    (installed_units / "fake-loop.timer").write_text((systemd / "fake-loop.timer").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("OE_KERNEL_SIGNING_KEY", "fixture-signing-key")
    monkeypatch.setenv("LOOP_FACTORY_SOURCE_REF", "fixture-ref")
    monkeypatch.setenv("LOOP_FACTORY_SYSTEMD_DIR", str(installed_units))
    monkeypatch.setenv("PYTHONPATH", str(repo_root))
    attacker_spool = root / "attacker-spool"
    attacker_spool.mkdir(mode=0o700)
    monkeypatch.setenv("LOOP_FACTORY_RUN_ID", "attacker-run")
    monkeypatch.setenv("OE_RECORD_SPOOL", str(attacker_spool))

    result = run_driver.run(department="fake", root=root)

    assert result["status"] == expected_status
    assert result["entrypoint_exit_code"] == entrypoint_exit_code
    verdict = json.loads((state / "run-manifests" / f"{result['run_id']}.verdict.json").read_text(encoding="utf-8"))
    assert verdict["status"] == expected_status
    assert verdict["entrypoint_exit_code"] == entrypoint_exit_code
    assert verdict["signature"]
    rows = [
        json.loads(line)
        for line in (state / "runs-v2.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows and {row["run_id"] for row in rows} == {result["run_id"]}
    assert not (attacker_spool / "runs-v2.jsonl").exists()


def _promotion_record(**overrides):
    record = {
        "schema": "run-record/v2", "rev": 2, "run_id": "caller-run",
        "department": "fake", "node": "fake_driver", "epoch": 0,
        "ts": "2026-08-09T00:00:00+00:00", "attempt": 1, "round": None,
        "release": {"hash": "release-hash", "source_ref": "source-ref"},
        "trigger": {"kind": "time", "id": "fake-loop.timer", "dedupe_key": "caller"},
        "engine": None, "model": None, "auth_class": None, "usage": None,
        "cost": {"lane": "flat_subscription", "model_calls": 0},
        "duration_ms": 0, "status": "ok", "errors": [], "artifacts": [],
        "receipts": [], "evaluator": None, "approval": None,
        "external_actions_taken": 0,
    }
    record.update(overrides)
    return record


def test_driver_rejects_forged_spool_identity_and_duplicate_rows(tmp_path):
    spool = tmp_path / "spool"
    state = tmp_path / "state"
    release = {"hash": "release-hash", "source_ref": "source-ref"}
    from factory import runrecord

    runrecord.write_spool_marker(
        spool, run_id="minted-run", department="fake", release=release,
        trigger="daily", state_dir=state,
    )
    forged = _promotion_record(department="attacker")
    (spool / "runs-v2.jsonl").write_text(
        json.dumps(forged) + "\n", encoding="utf-8"
    )
    with pytest.raises(run_driver.DriverRefusal, match="department_mismatch"):
        run_driver._promote_spool(
            spool, state_dir=state, run_id="minted-run", department="fake",
            release=release, trigger="daily",
        )
    assert not (state / "runs-v2.jsonl").exists()

    valid = runrecord.build_record(**_promotion_record())
    (spool / "runs-v2.jsonl").write_text(
        json.dumps(valid) + "\n" + json.dumps(valid) + "\n", encoding="utf-8"
    )
    with pytest.raises(run_driver.DriverRefusal, match="duplicate"):
        run_driver._promote_spool(
            spool, state_dir=state, run_id="minted-run", department="fake",
            release=release, trigger="daily",
        )
    assert not (state / "runs-v2.jsonl").exists()
