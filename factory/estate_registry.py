#!/usr/bin/env python3
"""Estate registry v1.

Per spec §9.4/§13.13, estate/registry.d/*.yaml files are merged read-only.
This module uses a stdlib simple-YAML subset parser matching the inventory emit
format and validates fail-closed.
"""
from pathlib import Path


REQUIRED = [
    "id",
    "owner",
    "surface",
    "schedule",
    "health_check",
    "heartbeat_path",
    "kill_switch",
]


class RegistryError(Exception):
    """Raised when registry input is malformed or invalid."""


def _strip_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_simple_yaml(text):
    """Parse the small YAML subset emitted by the estate inventory."""
    entries = []
    current = None
    saw_header = False

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not saw_header:
            if stripped != "entries:":
                raise RegistryError(f"line {line_number}: expected entries: header")
            saw_header = True
            continue

        if stripped.startswith("- "):
            if current is not None:
                entries.append(current)
            current = {}
            field = stripped[2:]
        else:
            if current is None:
                raise RegistryError(f"line {line_number}: field before first entry")
            field = stripped

        if ":" not in field:
            raise RegistryError(f"line {line_number}: expected key: value")
        key, value = field.split(":", 1)
        key = key.strip()
        if not key:
            raise RegistryError(f"line {line_number}: empty key")
        current[key] = _strip_quotes(value.strip())

    if not saw_header:
        raise RegistryError("expected entries: header")
    if current is not None:
        entries.append(current)
    return entries


def validate_entry(entry):
    """Return required keys absent from an entry."""
    return [key for key in REQUIRED if key not in entry]


def load_registry(directory):
    """Load, validate, and merge sorted registry partitions read-only."""
    merged = []
    seen_ids = set()
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            entries = _parse_simple_yaml(path.read_text())
        except (OSError, RegistryError) as exc:
            raise RegistryError(f"{path.name}: {exc}") from exc

        for entry in entries:
            missing = validate_entry(entry)
            entry_id = entry.get("id", "<missing id>")
            if missing:
                raise RegistryError(
                    f"{path.name}: entry {entry_id}: missing required keys: {', '.join(missing)}"
                )
            if entry_id in seen_ids:
                raise RegistryError(f"{path.name}: duplicate entry id: {entry_id}")
            seen_ids.add(entry_id)
            entry["_source_file"] = path.name
            merged.append(entry)
    return merged
