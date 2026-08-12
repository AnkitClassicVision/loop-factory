"""Factory-owned execution wrapper for one signed, release-bound department run.

Systemd must invoke this wrapper rather than a mutable department shell script.
The wrapper binds the unit's declared department/root to a signed run manifest,
executes exactly the release-declared entrypoint, then issues the final semantic
verdict. It never enables units, sends externally, or promotes a release.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from kernel import capabilities
from kernel import run_manifest
from factory import launch
from factory import node_contract
from factory import runrecord


class DriverRefusal(RuntimeError):
    """The wrapper cannot safely establish one release-bound execution."""


def _child_environment(*, root: Path, run_id: str) -> dict[str, str]:
    """Build the documented runtime-only child environment.

    ``kernel.capabilities.department_env`` is an explicit allowlist, not a
    credential-name denylist. The wrapper adds only the minted run identity
    and repository import path needed by the declared shell entrypoint.
    Factory signing authority, including ``OE_KERNEL_SIGNING_KEY``, is never
    copied into the child environment.
    """
    env = capabilities.department_env(os.environ)
    capabilities.assert_no_ambient_credentials(env)
    env["LOOP_FACTORY_RUN_ID"] = run_id
    env["PYTHONPATH"] = str(root)
    return env


def _load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DriverRefusal("minted_manifest_unreadable") from exc
    if not isinstance(value, dict):
        raise DriverRefusal("minted_manifest_invalid")
    return value


def _expected_trigger_kind(trigger: str) -> str:
    if trigger == "daily":
        return "time"
    if trigger in {"time", "event", "goal", "manual", "escalation"}:
        return trigger
    raise DriverRefusal("trigger_invalid")


def _read_promotable_records(
    spool: Path,
    *,
    run_id: str,
    department: str,
    release: dict[str, Any] | None,
    trigger: str,
    state_dir: Path,
) -> list[dict[str, Any]]:
    """Validate the complete private spool before one canonical append."""
    marker = runrecord.verify_factory_spool(
        spool,
        run_id=run_id,
        department=department,
        state_dir=state_dir,
        trigger=trigger,
    )
    if marker["release"] != release:
        raise DriverRefusal("spool_release_binding_mismatch")
    try:
        entries = {path.name for path in spool.iterdir()}
    except OSError as exc:
        raise DriverRefusal("factory_record_spool_unreadable") from exc
    allowed = {runrecord.SPOOL_MARKER, "runs-v2.jsonl"}
    unknown = entries - allowed
    if unknown:
        raise DriverRefusal("factory_record_spool_contains_unknown_entries")

    stream = spool / "runs-v2.jsonl"
    if not stream.exists():
        return []
    try:
        info = stream.lstat()
        if not info or not stat.S_ISREG(info.st_mode):
            raise DriverRefusal("factory_record_spool_stream_invalid")
        lines = stream.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DriverRefusal("factory_record_spool_stream_unreadable") from exc

    expected_kind = _expected_trigger_kind(trigger)
    rows: list[dict[str, Any]] = []
    seen_bytes: set[bytes] = set()
    seen_identity: set[tuple[Any, ...]] = set()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("record is not an object")
            record = runrecord.validate_record(raw)
            contract_root = Path(state_dir).parent
            if (contract_root / "node-contract.json").is_file():
                node_contract.validate_bound_record(record, contract_root)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DriverRefusal(f"spool_record_malformed:{number}") from exc
        if "promotion" in record:
            raise DriverRefusal(f"spool_record_reserved_field:{number}")
        if record["department"] != department:
            raise DriverRefusal(f"spool_record_department_mismatch:{number}")
        if record["release"] != release:
            raise DriverRefusal(f"spool_record_release_mismatch:{number}")
        record_trigger = record["trigger"]
        if record_trigger is not None and record_trigger["kind"] != expected_kind:
            raise DriverRefusal(f"spool_record_trigger_mismatch:{number}")
        canonical = json.dumps(
            record, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        identity = (record["node"], record["attempt"], record["round"])
        if canonical in seen_bytes or identity in seen_identity:
            raise DriverRefusal(f"spool_record_duplicate:{number}")
        seen_bytes.add(canonical)
        seen_identity.add(identity)
        promoted = dict(record)
        promoted["run_id"] = run_id
        rows.append(promoted)
    return rows


def _promote_spool(
    spool: Path,
    *,
    state_dir: Path,
    run_id: str,
    department: str,
    release: dict[str, Any] | None,
    trigger: str,
) -> int:
    rows = _read_promotable_records(
        spool,
        run_id=run_id,
        department=department,
        release=release,
        trigger=trigger,
        state_dir=state_dir,
    )
    if rows:
        runrecord._append_canonical_records(state_dir, rows)
    return len(rows)


def run(*, department: str, root: str | Path, state_dir: str | Path | None = None,
        trigger: str = "daily") -> dict[str, Any]:
    root_path = Path(root).resolve()
    dept_dir = root_path / "departments" / department
    state_path = Path(state_dir) if state_dir else dept_dir / "state"
    try:
        minted = run_manifest.mint(
            department=department,
            dept_dir=dept_dir,
            state_dir=state_path,
            trigger=trigger,
        )
        manifest = _load_manifest(minted["manifest"])
        entrypoint = manifest.get("entrypoint")
        if not isinstance(entrypoint, dict) or not isinstance(entrypoint.get("path"), str):
            raise DriverRefusal("minted_entrypoint_invalid")
        entrypoint_path = (dept_dir / entrypoint["path"]).resolve()
        if not entrypoint_path.is_file() or dept_dir not in entrypoint_path.parents:
            raise DriverRefusal("minted_entrypoint_unreadable")
    except (run_manifest.ManifestRefused, DriverRefusal) as exc:
        return {"status": "blocked", "reason": str(exc), "run_id": None}

    run_id = str(minted["run_id"])
    manifest_release = manifest.get("release")
    if manifest_release is None:
        manifest_release = runrecord.read_release(dept_dir)
    manifest_trigger = manifest.get("trigger", trigger)
    if not isinstance(manifest_trigger, str):
        return {"status": "blocked", "reason": "minted_trigger_invalid", "run_id": run_id}
    try:
        runrecord.validate_record(
            {
                "schema": runrecord.SCHEMA,
                "rev": 2,
                "run_id": run_id,
                "department": department,
                "node": "factory-driver",
                "epoch": 0,
                "ts": "1970-01-01T00:00:00+00:00",
                "attempt": 1,
                "round": None,
                "release": manifest_release,
                "trigger": None,
                "engine": None,
                "model": None,
                "auth_class": None,
                "usage": None,
                "cost": {"lane": "flat_subscription", "model_calls": 0},
                "duration_ms": 0,
                "status": "ok",
                "errors": [],
                "artifacts": [],
                "receipts": [],
                "evaluator": None,
                "approval": None,
                "external_actions_taken": 0,
            }
        )
        _expected_trigger_kind(manifest_trigger)
    except (TypeError, ValueError, DriverRefusal) as exc:
        return {"status": "blocked", "reason": "minted_binding_invalid", "run_id": run_id}

    state_path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".factory-spool-{run_id}-", dir=state_path
    ) as spool_name:
        spool = Path(spool_name)
        # TemporaryDirectory defaults to a private directory. The marker is
        # written only after mint and binds the child context to this run.
        try:
            runrecord.write_spool_marker(
                spool,
                run_id=run_id,
                department=department,
                release=manifest_release,
                trigger=manifest_trigger,
                state_dir=state_path,
            )
        except runrecord.RecordCustodyRefused as exc:
            return {"status": "blocked", "reason": str(exc), "run_id": run_id}
        env = _child_environment(root=root_path, run_id=run_id)
        captured: dict[str, Any] = {}

        def _run_child(command, *, env):
            completed = subprocess.run(
                command,
                cwd=root_path,
                env=env,
                check=False,
                text=True,
            )
            captured["completed"] = completed
            return completed

        try:
            launch.launch_command(
                department,
                ["/bin/bash", str(entrypoint_path)],
                base=env,
                root=root_path,
                runner=_run_child,
                record_spool=spool,
            )
        except (launch.LaunchRefused, runrecord.RecordCustodyRefused, OSError) as exc:
            return {"status": "blocked", "reason": str(exc), "run_id": run_id}
        completed = captured.get("completed")
        if completed is None:
            return {"status": "blocked", "reason": "child_not_executed", "run_id": run_id}
        try:
            promoted_count = _promote_spool(
                spool,
                state_dir=state_path,
                run_id=run_id,
                department=department,
                release=manifest_release,
                trigger=manifest_trigger,
            )
        except (DriverRefusal, runrecord.RecordCustodyRefused, OSError) as exc:
            return {
                "status": "blocked",
                "reason": str(exc),
                "run_id": run_id,
                "entrypoint_exit_code": completed.returncode,
            }
    verdict = run_manifest.verify(
        dept_dir=dept_dir,
        state_dir=state_path,
        run_id=run_id,
        entrypoint_exit_code=completed.returncode,
    )
    return {
        "status": verdict["status"],
        "run_id": run_id,
        "entrypoint_exit_code": completed.returncode,
        "promoted_records": promoted_count,
        "verdict": verdict,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--department", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--trigger", default="daily")
    args = parser.parse_args(argv)
    result = run(
        department=args.department,
        root=args.root,
        state_dir=args.state_dir,
        trigger=args.trigger,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "green" else 2


if __name__ == "__main__":
    raise SystemExit(main())
