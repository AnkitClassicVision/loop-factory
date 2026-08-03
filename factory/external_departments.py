"""Read-only projection of configured external departments into the estate.

External repositories are sources only.  This module reads a small, fixed set
of files, validates and sanitizes their contents, then atomically publishes a
local mirror under ``estate/state/external/<configured-name>``.  Boardfeed
consumes only that mirror; it never follows the configuration back to an
external repository.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    yaml = None

from factory.charter_loader import CharterError, load_charter
from factory.runrecord import validate_record


MIRROR_SCHEMA = "external-department-mirror/v1"
RECEIPT_SCHEMA = "external-department-refresh-receipt/v1"
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
AUTONOMY_STATES = frozenset({"shadow", "draft_only", "gated_live", "autonomous"})
OBJECTIVES_OBSERVED_SCHEMA = "objectives-observed/v1"


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _regular_file(path: Path) -> bool:
    """Accept regular files only; never follow a final-component symlink."""
    try:
        result = path.lstat()
    except OSError:
        return False
    return not path.is_symlink() and stat.S_ISREG(result.st_mode)


def _read_json(path: Path) -> tuple[Any | None, str]:
    if not path.exists():
        return None, "absent"
    if not _regular_file(path):
        return None, "invalid"
    try:
        return json.loads(path.read_text(encoding="utf-8")), "present"
    except (OSError, ValueError):
        return None, "invalid"


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    if isinstance(value, str):
        return value
    return None


def load_config(path: str | Path) -> list[dict[str, Any]]:
    """Load and validate the external department list; absence is a no-op."""
    config_path = Path(path)
    if not config_path.exists():
        return []
    if yaml is None:
        raise ValueError("PyYAML is required to load external department config")
    if not _regular_file(config_path):
        raise ValueError(f"external department config is not a regular file: {config_path}")
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid external department config: {config_path}") from exc
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("external department config must be a list")

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError(f"external department entry {index} must be a mapping")
        unknown = set(raw) - {"name", "root", "state"}
        if unknown:
            raise ValueError(
                f"external department entry {index} has unknown key {sorted(unknown)[0]!r}"
            )
        name = raw.get("name")
        root = raw.get("root")
        state = raw.get("state")
        if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None:
            raise ValueError(f"external department entry {index} has invalid name")
        if name in seen:
            raise ValueError(f"duplicate external department name: {name}")
        if not isinstance(root, str) or not root:
            raise ValueError(f"external department {name!r} has invalid root")
        if not isinstance(state, str) or not state:
            raise ValueError(f"external department {name!r} has invalid state")
        root_path = Path(root)
        state_path = Path(state)
        if not root_path.is_absolute() or not state_path.is_absolute():
            raise ValueError(f"external department {name!r} paths must be absolute")
        seen.add(name)
        output.append({"name": name, "root": root_path, "state": state_path})
    return output


def _read_runs(path: Path, name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    health = {"status": "absent", "valid_rows": 0, "invalid_rows": 0}
    if not path.exists():
        return [], health
    health["status"] = "present"
    if not _regular_file(path):
        health.update({"status": "invalid", "invalid_rows": 1})
        return [], health
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        health.update({"status": "invalid", "invalid_rows": 1})
        return [], health

    records: list[dict[str, Any]] = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
            validated = validate_record(value)
            if validated.get("department") != name:
                raise ValueError("record department does not match configured name")
        except (TypeError, ValueError):
            health["invalid_rows"] += 1
            continue
        records.append(validated)
    records.sort(
        key=lambda row: (
            str(row.get("ts", "")),
            str(row.get("run_id", "")),
            str(row.get("node", "")),
            int(row.get("attempt", 0)),
        )
    )
    health["valid_rows"] = len(records)
    return records, health


def _read_state(path: Path, name: str) -> tuple[dict[str, Any] | None, str]:
    value, status = _read_json(path)
    if status != "present":
        return None, status
    if not isinstance(value, dict):
        return None, "invalid"
    required_valid = (
        value.get("department") == name
        and isinstance(value.get("epoch"), int)
        and not isinstance(value.get("epoch"), bool)
        and _parse_ts(value.get("last_cycle_at")) is not None
        and value.get("autonomy_state") in AUTONOMY_STATES
        and isinstance(value.get("open_findings"), list)
        and isinstance(value.get("escalations"), int)
        and not isinstance(value.get("escalations"), bool)
    )
    if not required_valid:
        return None, "invalid"
    return {
        "department": name,
        "epoch": value["epoch"],
        "last_cycle_at": value["last_cycle_at"],
        "autonomy_state": value["autonomy_state"],
        "open_findings": len(value["open_findings"]),
        "escalations": value["escalations"],
    }, "present"


def _read_heartbeats(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    health = {"status": "absent", "valid_rows": 0, "invalid_rows": 0}
    if not path.exists():
        return None, health
    health["status"] = "present"
    if not _regular_file(path):
        health.update({"status": "invalid", "invalid_rows": 1})
        return None, health
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        health.update({"status": "invalid", "invalid_rows": 1})
        return None, health

    latest: dict[str, Any] | None = None
    for raw in lines:
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            health["invalid_rows"] += 1
            continue
        if (
            not isinstance(value, dict)
            or _parse_ts(value.get("ts")) is None
            or not isinstance(value.get("ok"), bool)
        ):
            health["invalid_rows"] += 1
            continue
        latest = {"ts": value["ts"], "ok": value["ok"]}
        health["valid_rows"] += 1
    if latest is None and health["invalid_rows"]:
        health["status"] = "invalid"
    return latest, health


def _read_objectives(path: Path) -> tuple[dict[str, Any] | None, str]:
    value, status = _read_json(path)
    if status != "present":
        return None, status
    if not isinstance(value, dict) or value.get("schema") != OBJECTIVES_OBSERVED_SCHEMA:
        return None, "invalid"
    values = value.get("values")
    if _parse_ts(value.get("ts")) is None or not isinstance(values, dict):
        return None, "invalid"
    clean: dict[str, Any] = {}
    for objective_id, observed in values.items():
        if not isinstance(objective_id, str) or not objective_id:
            return None, "invalid"
        scalar = _safe_scalar(observed)
        if scalar is None or isinstance(scalar, bool):
            return None, "invalid"
        clean[objective_id] = scalar
    return {
        "schema": OBJECTIVES_OBSERVED_SCHEMA,
        "ts": value["ts"],
        "values": dict(sorted(clean.items())),
    }, "present"


def _read_display_metadata(path: Path, name: str) -> tuple[dict[str, Any] | None, str]:
    if not path.exists():
        return None, "absent"
    if not _regular_file(path):
        return None, "invalid"
    try:
        charter = load_charter(path, expect_department=name)
    except CharterError:
        return None, "invalid"
    setpoints = charter.get("setpoints")
    objectives = setpoints.get("objectives") if isinstance(setpoints, dict) else None
    if objectives is None:
        objectives = charter.get("objectives")
    clean_objectives: dict[str, dict[str, Any]] = {}
    if objectives is not None:
        if not isinstance(objectives, dict):
            return None, "invalid"
        for objective_id, raw in objectives.items():
            if not isinstance(objective_id, str) or not objective_id:
                return None, "invalid"
            values = raw if isinstance(raw, dict) else {"setpoint": raw}
            clean_values: dict[str, Any] = {}
            for key in ("label", "setpoint", "minimum", "target", "unit"):
                if key not in values:
                    continue
                scalar = _safe_scalar(values[key])
                if scalar is None:
                    return None, "invalid"
                clean_values[key] = scalar
            clean_objectives[objective_id] = clean_values
    return {
        "autonomy_state": charter["autonomy_state"],
        "objectives": dict(sorted(clean_objectives.items())),
    }, "present"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _prepare_output_dir(repo_root: Path, name: str) -> Path:
    """Create the mirror directory without traversing a symlink outside root."""
    current = repo_root
    for component in ("estate", "state", "external", name):
        current = current / component
        try:
            result = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            continue
        if current.is_symlink() or not stat.S_ISDIR(result.st_mode):
            raise ValueError(f"unsafe external mirror output path: {current}")
    return current


def _invalid_count(sources: dict[str, Any]) -> int:
    total = 0
    for health in sources.values():
        if isinstance(health, dict):
            total += int(health.get("invalid_rows", 0))
            if health.get("status") == "invalid" and "invalid_rows" not in health:
                total += 1
        elif health == "invalid":
            total += 1
    return total


def refresh(
    repo_root: str | Path,
    *,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Refresh every configured mirror without writing to any source path."""
    repo_root = Path(repo_root).resolve()
    config = Path(config_path) if config_path is not None else repo_root / "estate" / "external_departments.yaml"
    entries = load_config(config)
    if not entries:
        return {
            "schema": RECEIPT_SCHEMA,
            "configured": 0,
            "mirrored": 0,
            "invalid": 0,
            "departments": [],
        }

    receipts: list[dict[str, Any]] = []
    for entry in entries:
        name = entry["name"]
        root = entry["root"]
        state = entry["state"]
        runs, runs_health = _read_runs(state / "runs-v2.jsonl", name)
        state_value, state_status = _read_state(state / "STATE.json", name)
        heartbeat, heartbeat_health = _read_heartbeats(state / "heartbeats.jsonl")
        observed, observed_status = _read_objectives(state / "objectives_observed.json")
        metadata, charter_status = _read_display_metadata(root / "charter.yaml", name)
        sources: dict[str, Any] = {
            "runs_v2": runs_health,
            "state": state_status,
            "heartbeats": heartbeat_health,
            "objectives_observed": observed_status,
            "charter": charter_status,
        }
        absent = sorted(
            key
            for key, health in sources.items()
            if health == "absent"
            or isinstance(health, dict) and health.get("status") == "absent"
        )
        unavailable = sorted(
            key
            for key, health in sources.items()
            if isinstance(health, str) and health in {"absent", "invalid"}
            or isinstance(health, dict)
            and health.get("status") in {"absent", "invalid"}
        )
        invalid = _invalid_count(sources)
        mirror = {
            "schema": MIRROR_SCHEMA,
            "name": name,
            "state": state_value,
            "heartbeat": heartbeat,
            "runs": runs,
            "objectives_observed": observed,
            "display": metadata,
            "source_health": {
                "absent": absent,
                "invalid": invalid,
                "unavailable": unavailable,
            },
        }
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "name": name,
            "valid_runs": runs_health["valid_rows"],
            "invalid_runs": runs_health["invalid_rows"],
            "invalid": invalid,
            "absent": absent,
            "sources": sources,
        }
        target = _prepare_output_dir(repo_root, name)
        _atomic_write(
            target / "mirror.json",
            json.dumps(mirror, sort_keys=True, separators=(",", ":")) + "\n",
        )
        _atomic_write(
            target / "refresh-receipt.json",
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        )
        receipts.append(receipt)
    return {
        "schema": RECEIPT_SCHEMA,
        "configured": len(entries),
        "mirrored": len(receipts),
        "invalid": sum(receipt["invalid"] for receipt in receipts),
        "departments": receipts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh read-only external department mirrors")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)
    try:
        receipt = refresh(args.repo_root, config_path=args.config)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
