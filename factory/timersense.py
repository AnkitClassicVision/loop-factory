"""Snapshot systemd user timers for deterministic estate consumers."""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Mapping, Sequence


LOG = logging.getLogger(__name__)
SCHEMA = "timers-snapshot/v1"
_TIMESTAMP_RE = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{4}-\d{2}-\d{2}\s+"
    r"\d{2}:\d{2}:\d{2}\s+\S+"
)
_UNIT_RE = re.compile(r"(?P<unit>\S+\.timer)\s+(?P<service>\S+\.service)(?:\s|$)")
DEFAULT_GROUP_OVERRIDES = {"social-*": "social"}


def _run(argv: Sequence[str]) -> str:
    """Run a command and return stdout; kept as the system boundary for tests."""
    completed = subprocess.run(
        list(argv), check=True, capture_output=True, text=True
    )
    return completed.stdout


def _parse_timestamp(value: str | None) -> str | None:
    if not value or value.strip() in {"-", "n/a"}:
        return None
    value = value.strip()
    for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S %Z"):
        try:
            parsed = datetime.strptime(value, fmt)
            # systemd commonly emits local abbreviations that strptime accepts
            # without an offset. Convert those using the host's local timezone.
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            continue
    return None


def parse_list_timers(text: str) -> list[dict[str, str | None]]:
    """Parse list-timers without relying on its variable-width columns."""
    timers: list[dict[str, str | None]] = []
    for line in text.splitlines():
        match = _UNIT_RE.search(line)
        if not match:
            continue
        timestamps = _TIMESTAMP_RE.findall(line[: match.start()])
        timers.append(
            {
                "unit": match.group("unit"),
                "service": match.group("service"),
                "next_run": _parse_timestamp(timestamps[0]) if timestamps else None,
                "last_run": _parse_timestamp(timestamps[1]) if len(timestamps) > 1 else None,
            }
        )
    return timers


def parse_show(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def group_for_unit(
    unit: str, overrides: Mapping[str, str] | None = None
) -> str:
    """Return a broad family, with a small caller-extensible override seam."""
    stem = unit.removesuffix(".timer")
    effective = DEFAULT_GROUP_OVERRIDES if overrides is None else overrides
    for pattern, group in effective.items():
        if fnmatch.fnmatch(stem, pattern):
            return group
    if "-loop-" in stem or stem.endswith("-loop"):
        return stem.split("-", 1)[0]
    return "other"


def _enabled(value: str | None) -> bool | str:
    if value in {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}:
        return True
    if value in {"disabled", "masked", "masked-runtime", "static", "indirect"}:
        return False
    return "unknown"


def _result(value: str | None) -> str:
    if value == "success":
        return "success"
    if value and value not in {"unknown", "n/a"}:
        return "failure"
    return "unknown"


def collect(
    patterns: Sequence[str] = (),
    group_overrides: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    listing = _run(
        ["systemctl", "--user", "list-timers", "--all", "--no-pager", "--no-legend"]
    )
    rows = parse_list_timers(listing)
    if patterns:
        rows = [row for row in rows if any(fnmatch.fnmatch(str(row["unit"]), p) for p in patterns)]

    timers: list[dict[str, object]] = []
    properties = (
        "ActiveState,SubState,Result,ExecMainStatus,"
        "ExecMainExitTimestamp,UnitFileState"
    )
    for row in rows:
        show = parse_show(
            _run(["systemctl", "--user", "show", str(row["service"]), f"--property={properties}"])
        )
        exit_value = show.get("ExecMainStatus")
        try:
            exit_status: int | str = int(exit_value) if exit_value not in {None, ""} else "unknown"
        except ValueError:
            exit_status = "unknown"
        last_run = row["last_run"]
        if "ExecMainExitTimestamp" in show:
            last_run = _parse_timestamp(show["ExecMainExitTimestamp"])
        timers.append(
            {
                "unit": row["unit"],
                "service": row["service"],
                "enabled": _enabled(show.get("UnitFileState")),
                "next_run": row["next_run"],
                "last_run": last_run,
                "last_result": _result(show.get("Result")),
                "exit_status": exit_status,
                "group": group_for_unit(str(row["unit"]), group_overrides),
            }
        )
    return timers


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = handle.name
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def snapshot(out: Path, patterns: Sequence[str] = (), tolerate_missing: bool = False) -> dict[str, object]:
    note: str | None = None
    try:
        timers = collect(patterns)
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        if not tolerate_missing:
            raise RuntimeError(f"systemctl unavailable: {exc}") from exc
        LOG.warning("systemctl unavailable: %s", exc)
        timers = []
        note = "systemctl unavailable"
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timers": timers,
    }
    if note:
        payload["note"] = note
    _atomic_json(out, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("estate/state/timers.json"))
    parser.add_argument("--pattern", action="append", default=[])
    parser.add_argument("--tolerate-missing", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = snapshot(args.out, args.pattern, args.tolerate_missing)
    except RuntimeError as exc:
        LOG.error("%s", exc)
        return 1
    timers = payload["timers"]
    receipt = {
        "timers": len(timers),
        "groups": sorted({timer["group"] for timer in timers}),
        "out": str(args.out),
    }
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
