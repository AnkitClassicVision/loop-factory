#!/usr/bin/env python3
"""Fail-closed deadman for the loop-factory estate manager.

The estate manager watches department managers. This module watches the estate
manager's own registry, STATE.json, and append-only heartbeat. Any unreadable,
malformed, stale, future-dated, or inconsistent input is an alarm condition.
It never reports healthy from STATE.json alone.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("loop_factory.estate_deadman")
DEFAULT_MAX_AGE_SECONDS = 27 * 3600
DEFAULT_MAX_FUTURE_SKEW_SECONDS = 5 * 60


def _load_module(name: str, filename: str):
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{path.name} unreadable: {exc.__class__.__name__}"
    if not isinstance(value, dict):
        return None, f"{path.name} must contain a JSON object"
    return value, None


def _read_last_heartbeat(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError) as exc:
        return None, f"{path.name} unreadable: {exc.__class__.__name__}"
    if not rows:
        return None, f"{path.name} has no heartbeat rows"
    last_value = None
    for row_number, row in enumerate(rows, 1):
        try:
            value = json.loads(row)
        except ValueError:
            return None, f"{path.name} row {row_number} is malformed JSON"
        if not isinstance(value, dict):
            return None, f"{path.name} row {row_number} must be a JSON object"
        payload = value.get("payload")
        required_counters = ("epoch", "findings", "escalations")
        if (
            _parse_timestamp(value.get("ts")) is None
            or value.get("emitter") != "estate-manager"
            or value.get("kind") != "cycle"
            or not isinstance(payload, dict)
            or any(type(payload.get(key)) is not int for key in required_counters)
            or any(payload.get(key, -1) < 0 for key in required_counters)
        ):
            return None, f"{path.name} row {row_number} has invalid estate heartbeat schema"
        last_value = value
    return last_value, None


def _finding(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def evaluate_deadman(
    registry_dir: str | Path,
    estate_state_dir: str | Path,
    *,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    max_future_skew_seconds: int = DEFAULT_MAX_FUTURE_SKEW_SECONDS,
) -> dict[str, Any]:
    """Return a deterministic health report; any uncertainty is an alarm."""
    if max_age_seconds <= 0 or max_future_skew_seconds < 0:
        raise ValueError("deadman thresholds must be positive")

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    registry_dir = Path(registry_dir)
    estate_state_dir = Path(estate_state_dir)
    findings: list[dict[str, str]] = []

    registry = _load_module("estate_registry_deadman", "estate_registry.py")
    try:
        entries = registry.load_registry(registry_dir)
    except (OSError, ValueError, registry.RegistryError) as exc:
        findings.append(_finding("estate_registry_unreadable", f"registry validation failed: {exc}"))
    else:
        if not entries:
            findings.append(_finding("estate_registry_empty", "registry contains no entries"))

    state, state_error = _read_json_object(estate_state_dir / "STATE.json")
    heartbeat, heartbeat_error = _read_last_heartbeat(estate_state_dir / "heartbeats.jsonl")
    if state_error:
        findings.append(_finding("estate_state_unreadable", state_error))
    if heartbeat_error:
        findings.append(_finding("estate_heartbeat_unreadable", heartbeat_error))

    state_ts = _parse_timestamp(state.get("last_cycle_at")) if state else None
    heartbeat_ts = _parse_timestamp(heartbeat.get("ts")) if heartbeat else None
    if state is not None and state_ts is None:
        findings.append(_finding("estate_state_timestamp_invalid", "STATE.json last_cycle_at is missing or invalid"))
    if heartbeat is not None and heartbeat_ts is None:
        findings.append(_finding("estate_heartbeat_timestamp_invalid", "heartbeat ts is missing or invalid"))

    state_schema_valid = False
    if state is not None:
        dept_epochs = state.get("dept_epochs")
        open_findings = state.get("open_findings")
        state_schema_valid = (
            type(state.get("epoch")) is int
            and state["epoch"] >= 0
            and isinstance(dept_epochs, dict)
            and all(
                isinstance(dept_id, str)
                and (dept_epoch is None or (type(dept_epoch) is int and dept_epoch >= 0))
                for dept_id, dept_epoch in dept_epochs.items()
            )
            and isinstance(open_findings, list)
            and all(isinstance(finding, dict) for finding in open_findings)
            and type(state.get("escalations")) is int
            and state["escalations"] >= 0
        )
        if not state_schema_valid:
            findings.append(_finding(
                "estate_state_schema_invalid",
                "STATE.json has invalid epoch, dept_epochs, open_findings, or escalations fields",
            ))

    if state and heartbeat:
        state_epoch = state.get("epoch")
        heartbeat_epoch = heartbeat.get("payload", {}).get("epoch") if isinstance(heartbeat.get("payload"), dict) else None
        if heartbeat.get("emitter") != "estate-manager" or heartbeat.get("kind") != "cycle":
            findings.append(_finding("estate_heartbeat_identity_invalid", "last heartbeat is not an estate-manager cycle"))
        if type(state_epoch) is not int or type(heartbeat_epoch) is not int:
            findings.append(_finding("estate_epoch_invalid", "state and heartbeat epochs must be integers"))
        elif state_epoch != heartbeat_epoch:
            findings.append(_finding(
                "estate_epoch_mismatch",
                f"STATE.json epoch {state_epoch} does not match heartbeat epoch {heartbeat_epoch}",
            ))
        if state_ts is not None and heartbeat_ts is not None and state_ts != heartbeat_ts:
            findings.append(_finding(
                "estate_timestamp_mismatch",
                "STATE.json and heartbeat timestamps do not identify the same estate cycle",
            ))
        if state_schema_valid:
            payload = heartbeat["payload"]
            if (
                payload["findings"] != len(state["open_findings"])
                or payload["escalations"] != state["escalations"]
            ):
                findings.append(_finding(
                    "estate_counter_mismatch",
                    "heartbeat findings/escalations do not match STATE.json",
                ))

    if heartbeat_ts is not None:
        age_seconds = (observed_at - heartbeat_ts).total_seconds()
        if age_seconds < -max_future_skew_seconds:
            findings.append(_finding("estate_heartbeat_future", "heartbeat timestamp is beyond allowed clock skew"))
        elif age_seconds > max_age_seconds:
            findings.append(_finding(
                "estate_heartbeat_stale",
                f"estate heartbeat age {int(age_seconds)}s exceeds {max_age_seconds}s",
            ))

    if state_ts is not None:
        state_age_seconds = (observed_at - state_ts).total_seconds()
        if state_age_seconds < -max_future_skew_seconds:
            findings.append(_finding("estate_state_future", "state timestamp is beyond allowed clock skew"))
        elif state_age_seconds > max_age_seconds:
            findings.append(_finding(
                "estate_state_stale",
                f"estate state age {int(state_age_seconds)}s exceeds {max_age_seconds}s",
            ))

    return {
        "ok": not findings,
        "alarm": bool(findings),
        "observed_at": observed_at.isoformat(),
        "max_age_seconds": max_age_seconds,
        "findings": findings,
    }


def raise_alarm(report: dict[str, Any], outbox_path: str | Path) -> dict[str, Any]:
    """Append one factory-standard escalation packet; never sends externally."""
    human_loop = _load_module("human_in_the_loop_deadman", "human_in_the_loop.py")
    codes = [finding["code"] for finding in report["findings"]]
    issue = f"[deadman] estate watchdog alarm: {', '.join(codes)}"
    return human_loop.escalate(
        "estate",
        issue,
        outbox_path,
        context={
            "source": "estate-deadman",
            "finding_codes": codes,
            "observed_at": report["observed_at"],
            "max_age_seconds": report["max_age_seconds"],
        },
    )


def poisoned_registry_self_test(registry_dir: str | Path) -> dict[str, Any]:
    """Corrupt a temporary registry copy and prove fail-closed detection."""
    source = Path(registry_dir)
    registry_files = sorted(source.glob("*.yaml"))
    if not registry_files:
        raise RuntimeError("poisoned-registry self-test requires at least one registry YAML file")

    with tempfile.TemporaryDirectory(prefix="loop-factory-deadman-") as temp_dir:
        temp_root = Path(temp_dir)
        copied_registry = temp_root / "registry.d"
        copied_state = temp_root / "state"
        shutil.copytree(source, copied_registry)
        copied_state.mkdir()

        now = datetime.now(timezone.utc)
        (copied_state / "STATE.json").write_text(json.dumps({
            "epoch": 7,
            "last_cycle_at": now.isoformat(),
            "dept_epochs": {},
            "open_findings": [],
            "escalations": 0,
        }), encoding="utf-8")
        (copied_state / "heartbeats.jsonl").write_text(json.dumps({
            "ts": now.isoformat(),
            "emitter": "estate-manager",
            "kind": "cycle",
            "payload": {"epoch": 7, "findings": 0, "escalations": 0},
        }) + "\n", encoding="utf-8")

        poisoned = copied_registry / registry_files[0].name
        poisoned.write_text("this is deliberately invalid registry data\n", encoding="utf-8")
        report = evaluate_deadman(copied_registry, copied_state, now=now)
        codes = {finding["code"] for finding in report["findings"]}
        if not report["alarm"] or "estate_registry_unreadable" not in codes:
            raise AssertionError("deadman failed to alarm on poisoned registry copy")
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed deadman for the loop-factory estate manager")
    parser.add_argument("--registry-dir", default="estate/registry.d")
    parser.add_argument("--estate-state-dir", default="estate/state")
    parser.add_argument("--outbox", default="state/decisions_outbox.jsonl")
    parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--self-test-poisoned-registry", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.self_test_poisoned_registry:
        try:
            report = poisoned_registry_self_test(args.registry_dir)
        except (OSError, RuntimeError, AssertionError, ValueError) as exc:
            LOGGER.error("poisoned-registry self-test failed: %s", exc)
            return 2
        LOGGER.info(
            "poisoned-registry self-test passed: detected %s",
            ",".join(finding["code"] for finding in report["findings"]),
        )
        return 0

    try:
        report = evaluate_deadman(
            args.registry_dir,
            args.estate_state_dir,
            max_age_seconds=args.max_age_seconds,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        LOGGER.error("deadman evaluation failed closed: %s", exc)
        return 2

    if report["alarm"]:
        try:
            raise_alarm(report, args.outbox)
        except Exception as exc:  # the alarm path itself must fail visibly
            LOGGER.error("alarm detected but outbox append failed: %s", exc)
            return 2
        LOGGER.error(
            "estate deadman alarm recorded: %s",
            ",".join(finding["code"] for finding in report["findings"]),
        )
        return 1

    LOGGER.info("estate deadman healthy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
