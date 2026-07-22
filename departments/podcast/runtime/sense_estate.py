"""Sense every podcast estate unit without allowing silent inventory gaps."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from departments.podcast.runtime import record as record_node


DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "podcast" / "state"
DEFAULT_ESTATE_PATH = Path(__file__).with_name("estate.json")
ERROR_PATTERN = re.compile(r"\b(error|failed|failure|fatal|traceback|exception)\b", re.I)
Provider = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]


class CoverageError(RuntimeError):
    """Raised when sensing does not emit exactly one row per inventory item."""


def load_estate(path: str | Path = DEFAULT_ESTATE_PATH) -> dict[str, Any]:
    inventory_path = Path(path)
    try:
        value = json.loads(inventory_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"estate inventory is unreadable: {inventory_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"estate inventory is malformed: {inventory_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("estate inventory must be a JSON object")
    inventory_items(value)
    return value


def inventory_items(estate: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten timer, channel, and VPS inventory into uniquely named items."""
    timers = estate.get("systemd_user_timers", [])
    channels = estate.get("channels", [])
    vps = estate.get("vps", {})
    if vps is None:
        vps = {}
    if not isinstance(timers, list):
        raise ValueError("estate systemd_user_timers must be a list")
    if not isinstance(channels, list):
        raise ValueError("estate channels must be a list")
    if not isinstance(vps, dict):
        raise ValueError("estate vps must be an object")
    services = vps.get("services", [])
    if not isinstance(services, list):
        raise ValueError("estate vps.services must be a list")

    items: list[dict[str, Any]] = []
    for timer in timers:
        if not isinstance(timer, dict):
            raise ValueError("every systemd timer inventory item must be an object")
        items.append({"kind": "timer", **timer})
    for channel in channels:
        if not isinstance(channel, dict):
            raise ValueError("every channel inventory item must be an object")
        items.append({"kind": "channel", **channel})
    for service in services:
        if not isinstance(service, str) or not service.strip():
            raise ValueError("every VPS service inventory item must be a nonempty string")
        items.append(
            {
                "kind": "vps",
                "name": service,
                "host": vps.get("host", ""),
                "shadow_rule": vps.get("shadow_rule", ""),
            }
        )
    if not items:
        raise ValueError("estate inventory contains no units")
    names = [str(item.get("name", "")) for item in items]
    if any(not name for name in names):
        raise ValueError("every estate inventory item must have a name")
    if len(set(names)) != len(names):
        raise ValueError("estate inventory subjects must be unique")
    return items


def assert_inventory_coverage(
    estate: dict[str, Any], observations: list[dict[str, Any]]
) -> None:
    """Fail loudly unless every inventory subject has exactly one observation."""
    expected = {item["name"] for item in inventory_items(estate)}
    counts: dict[str, int] = {}
    for row in observations:
        subject = str(row.get("subject", ""))
        counts[subject] = counts.get(subject, 0) + 1
    missing = sorted(expected - set(counts))
    extra = sorted(set(counts) - expected)
    duplicate = sorted(subject for subject, count in counts.items() if count != 1)
    if missing or extra or duplicate:
        raise CoverageError(
            f"inventory coverage failed: missing={missing}, extra={extra}, duplicate={duplicate}"
        )


def _timestamp(value: datetime | str | None) -> tuple[datetime, str]:
    if value is None:
        moment = datetime.now(timezone.utc)
    elif isinstance(value, str):
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        moment = value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment, moment.isoformat()


def _run_systemctl(unit: str) -> dict[str, Any]:
    command = [
        "systemctl",
        "--user",
        "show",
        unit,
        "-p",
        "ActiveState,SubState,Result,ExecMainStatus",
    ]
    completed = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=10
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"systemctl exited {completed.returncode}"
        raise RuntimeError(detail[:240])
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def _matching_files(directory: Path, pattern: str) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob(pattern) if path.is_file())


def _tail_has_error(path: Path, limit: int = 65536) -> bool:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        handle.seek(max(0, handle.tell() - limit))
        tail = handle.read().decode("utf-8", errors="replace")
    return any(ERROR_PATTERN.search(line) for line in tail.splitlines()[-200:])


def _timer_observation(item: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    now: datetime = context["now"]
    estate = context["estate"]
    runner = context.get("systemctl_runner") or _run_systemctl
    subject = item["name"]
    base_unit = subject.removesuffix(".timer")
    timer_unit = f"{base_unit}.timer"
    service_unit = f"{base_unit}.service"
    failures: list[tuple[str, str, str]] = []
    unknowns: list[str] = []
    metrics: dict[str, Any] = {
        "expected_cadence": item.get("expected_cadence"),
        "stale_after_minutes": item.get("stale_after_minutes"),
    }

    try:
        timer_state = runner(timer_unit)
        metrics.update(
            {
                "active_state": timer_state.get("ActiveState"),
                "sub_state": timer_state.get("SubState"),
                "result": timer_state.get("Result"),
                "exec_main_status": timer_state.get("ExecMainStatus"),
            }
        )
        active = timer_state.get("ActiveState") == "active"
        result = str(timer_state.get("Result") or "") in {"", "success"}
        exit_ok = str(timer_state.get("ExecMainStatus") or "0") == "0"
        if not (active and result and exit_ok):
            failures.append(
                (
                    "timer",
                    f"timer_failed: systemd state unhealthy for {timer_unit}",
                    f"systemd://{timer_unit}",
                )
            )
    except Exception as exc:
        unknowns.append(f"timer systemctl probe failed: {exc}")

    try:
        service_state = runner(service_unit)
        metrics.update(
            {
                "service_active_state": service_state.get("ActiveState"),
                "service_sub_state": service_state.get("SubState"),
                "service_result": service_state.get("Result"),
                "service_exec_main_status": service_state.get("ExecMainStatus"),
            }
        )
        service_result = str(service_state.get("Result") or "")
        service_exit = str(service_state.get("ExecMainStatus") or "0")
        if service_result not in {"", "success"} or service_exit != "0":
            failures.append(
                (
                    "timer",
                    f"timer_failed: last service run unhealthy for {service_unit}",
                    f"systemd://{service_unit}",
                )
            )
    except Exception as exc:
        unknowns.append(f"service systemctl probe failed: {exc}")

    evidence_specs = [
        key
        for key in ("receipt_glob", "log_glob", "ledger_path", "evidence")
        if item.get(key)
    ]
    threshold = item.get("stale_after_minutes")
    artifact_kind = ""
    artifacts: list[Path] = []
    artifact_location = ""
    log_read_errors: list[str] = []
    if len(evidence_specs) != 1:
        unknowns.append(
            "missing evidence spec; expected exactly one of receipt_glob, "
            "log_glob, ledger_path, or evidence=timer_only"
        )
    elif item.get("evidence"):
        if item["evidence"] != "timer_only":
            unknowns.append(f"unsupported evidence spec: {item['evidence']}")
    elif item.get("receipt_glob"):
        artifact_kind = "receipt"
        directory = Path(estate.get("receipts_dir", ""))
        artifact_location = str(directory / item["receipt_glob"])
        artifacts = _matching_files(directory, item["receipt_glob"])
    elif item.get("log_glob"):
        artifact_kind = "log"
        directory = Path(estate.get("logs_dir", ""))
        artifact_location = str(directory / item["log_glob"])
        artifacts = _matching_files(directory, item["log_glob"])
    else:
        artifact_kind = "ledger"
        ledger = Path(estate.get("logs_dir", "")) / item["ledger_path"]
        artifact_location = str(ledger)
        if ledger.is_file():
            artifacts = [ledger]

    if artifact_kind and artifacts:
        newest = max(artifacts, key=lambda path: path.stat().st_mtime)
        age_minutes = max(0.0, (now.timestamp() - newest.stat().st_mtime) / 60)
        metrics.update(
            {
                "evidence_kind": artifact_kind,
                "evidence_path": str(newest),
                "receipt_path": str(newest),
                "receipt_age_minutes": round(age_minutes, 3),
            }
        )
        if artifact_kind == "receipt":
            try:
                receipt_text = newest.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                unknowns.append(f"receipt read failed for {newest}: {exc}")
            else:
                if not receipt_text.strip():
                    metrics["failure_hint"] = "receipt_hollow"
                    failures.append(
                        (
                            "receipt",
                            "receipt_hollow: matched receipt contains no non-whitespace content",
                            str(newest),
                        )
                    )
        if threshold is None:
            unknowns.append(f"{artifact_kind} freshness threshold is not defined")
        elif age_minutes > float(threshold):
            failures.append(
                (
                    "receipt",
                    f"{artifact_kind} is {age_minutes:.1f} minutes old; limit is {threshold}",
                    str(newest),
                )
            )
    elif artifact_kind:
        unknowns.append(f"no {artifact_kind} matched configured evidence: {artifact_location}")

    log_files = artifacts if artifact_kind == "log" else []
    error_logs: list[Path] = []
    for path in log_files:
        try:
            if _tail_has_error(path):
                error_logs.append(path)
        except Exception as exc:
            log_read_errors.append(f"log read failed for {path}: {exc}")
    unknowns.extend(log_read_errors)
    metrics.update(
        {
            "log_files_checked": len(log_files),
            "log_error_files": len(error_logs),
            "log_read_errors": len(log_read_errors),
        }
    )
    if error_logs:
        failures.append(("log", "error pattern found in log tail", str(error_logs[0])))

    if failures:
        sensor, detail, evidence = failures[0]
        status = "fail"
    elif unknowns:
        if log_read_errors:
            sensor = "log"
        elif artifact_kind and not artifacts:
            sensor = "receipt" if artifact_kind == "receipt" else artifact_kind
        elif any(message.startswith("receipt read failed") for message in unknowns):
            sensor = "receipt"
        else:
            sensor = "timer"
        detail = "; ".join(unknowns)[:240]
        evidence = artifact_location or f"systemd://{timer_unit}"
        status = "unknown"
    else:
        sensor = "timer"
        detail = "timer, service, and configured evidence probes healthy"
        evidence = f"systemd://{timer_unit}"
        status = "ok"
    return {
        "sensor": sensor,
        "subject": subject,
        "status": status,
        "evidence": evidence,
        "detail": detail,
        "metrics": metrics,
    }


def _channel_observation(item: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    metadata_present = bool(item.get("via") and item.get("shadow_check"))
    if metadata_present and context.get("shadow", True):
        detail = "config present; reachability unchecked in shadow"
    elif metadata_present:
        detail = "config present; no live reachability probe configured"
    else:
        detail = "channel configuration metadata is incomplete"
    return {
        "sensor": "channel",
        "subject": item["name"],
        "status": "unknown",
        "evidence": str(context["estate_path"]),
        "detail": detail,
        "metrics": {
            "config_present": metadata_present,
            "reachability_checked": False,
            "delivered_count": 0,
            "via": item.get("via"),
        },
    }


def _vps_observation(item: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    detail = "VPS probe skipped in shadow; SSH and network access are disabled"
    if context.get("probe_vps"):
        detail = "VPS probe requested but no injected read-only provider is configured"
    return {
        "sensor": "vps",
        "subject": item["name"],
        "status": "unknown",
        "evidence": f"vps://{item.get('host', '')}/{item['name']}",
        "detail": detail,
        "metrics": {"probe_attempted": False, "host": item.get("host", "")},
    }


def default_provider(item: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Probe one inventory item. It never performs network or mutating work."""
    if item["kind"] == "timer":
        return _timer_observation(item, context)
    if item["kind"] == "channel":
        return _channel_observation(item, context)
    return _vps_observation(item, context)


def _normalize_observation(
    item: dict[str, Any], value: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    row = {
        "ts": timestamp,
        "sensor": value.get("sensor", item["kind"]),
        "subject": item["name"],
        "status": value.get("status", "unknown"),
        "evidence": str(value.get("evidence", "")),
        "detail": str(value.get("detail", ""))[:240],
        "metrics": value.get("metrics") if isinstance(value.get("metrics"), dict) else {},
    }
    if row["sensor"] not in {"timer", "receipt", "log", "channel", "vps"}:
        raise ValueError(f"invalid observation sensor: {row['sensor']!r}")
    if row["status"] not in {"ok", "warn", "fail", "unknown"}:
        raise ValueError(f"invalid observation status: {row['status']!r}")
    return row


def collect_observations(
    estate: dict[str, Any],
    *,
    provider: Provider | None = None,
    now: datetime | str | None = None,
    shadow: bool = True,
    probe_vps: bool = False,
    estate_path: str | Path = DEFAULT_ESTATE_PATH,
    systemctl_runner: Callable[[str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Collect exactly one observation for every estate inventory item."""
    moment, timestamp = _timestamp(now)
    probe = provider or default_provider
    context = {
        "estate": estate,
        "estate_path": Path(estate_path),
        "now": moment,
        "shadow": shadow,
        "probe_vps": probe_vps,
        "systemctl_runner": systemctl_runner,
    }
    observations: list[dict[str, Any]] = []
    for item in inventory_items(estate):
        try:
            value = probe(item, context)
            if value is None:
                raise RuntimeError("provider returned no observation")
            row = _normalize_observation(item, value, timestamp)
        except Exception as exc:
            row = _normalize_observation(
                item,
                {
                    "sensor": item["kind"] if item["kind"] != "timer" else "timer",
                    "status": "unknown",
                    "evidence": str(estate_path),
                    "detail": f"probe error: {exc}",
                    "metrics": {},
                },
                timestamp,
            )
        observations.append(row)
    assert_inventory_coverage(estate, observations)
    return observations


def append_observations(path: str | Path, observations: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in observations:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def run_sense(
    state_dir: str | Path,
    *,
    estate_path: str | Path = DEFAULT_ESTATE_PATH,
    provider: Provider | None = None,
    now: datetime | str | None = None,
    shadow: bool = True,
    probe_vps: bool = False,
    systemctl_runner: Callable[[str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    estate = load_estate(estate_path)
    observations = collect_observations(
        estate,
        provider=provider,
        now=now,
        shadow=shadow,
        probe_vps=probe_vps,
        estate_path=estate_path,
        systemctl_runner=systemctl_runner,
    )
    state_dir = Path(state_dir)
    append_observations(state_dir / "observations.jsonl", observations)
    record_node.write_record(
        state_dir,
        "sense_estate",
        {
            "observations": len(observations),
            "failed": sum(row["status"] == "fail" for row in observations),
            "unknown": sum(row["status"] == "unknown" for row in observations),
        },
        shadow=shadow,
    )
    return observations


def main() -> None:
    parser = argparse.ArgumentParser(description="Sense the podcast estate inventory")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--estate", default=str(DEFAULT_ESTATE_PATH))
    parser.add_argument("--probe-vps", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--shadow", dest="shadow", action="store_true", default=True)
    mode.add_argument("--live", dest="shadow", action="store_false")
    args = parser.parse_args()
    try:
        observations = run_sense(
            args.state_dir,
            estate_path=args.estate,
            shadow=args.shadow,
            probe_vps=args.probe_vps,
        )
    except (CoverageError, OSError, TypeError, ValueError) as exc:
        print(f"estate sensing failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps({"observations": len(observations), "shadow": args.shadow}))


if __name__ == "__main__":
    main()
