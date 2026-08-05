"""Register factory outboxes in the shared mailroom configuration."""
from __future__ import annotations

import argparse
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

try:
    from .outbox_push import KINDS, ConfigError, load_config
except ImportError:  # pragma: no cover - supports direct script execution
    from outbox_push import KINDS, ConfigError, load_config


LOGGER = logging.getLogger(__name__)
DEFAULT_KIND = "escalation"


def _atomic_write(path: Path, content: str) -> None:
    """Replace a config only after its complete contents are durable."""
    temporary_path: Path | None = None
    mode = path.stat().st_mode & 0o777
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load config: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config must be a YAML mapping")
    return raw


def register_watch(
    config_path: str | Path,
    department: str,
    outbox_path: str | Path,
    *,
    kind: str = DEFAULT_KIND,
) -> dict[str, Any]:
    """Validate and idempotently add one watch to an existing config."""
    if kind not in KINDS:
        raise ConfigError(f"watch kind must be one of: {', '.join(sorted(KINDS))}")
    if not department:
        raise ConfigError("department must be a non-empty label")

    path = Path(config_path)
    outbox = str(outbox_path)
    raw = _read_mapping(path)

    # Run the same strict validator used by the watcher before changing any
    # bytes. Unknown config keys remain in ``raw`` and are written unchanged in
    # meaning, including listener configuration owned by the companion watcher.
    load_config(path)
    watches = raw["watches"]
    matching = [
        watch
        for watch in watches
        if isinstance(watch, dict)
        and watch.get("path") == outbox
        and watch.get("department") == department
    ]
    changed = False
    if matching:
        first = matching[0]
        raw["watches"] = [
            watch
            for watch in watches
            if not (
                isinstance(watch, dict)
                and watch.get("path") == outbox
                and watch.get("department") == department
                and watch is not first
            )
        ]
        changed = len(matching) > 1
    else:
        raw["watches"] = [
            *watches,
            {"path": outbox, "department": department, "kind": kind},
        ]
        changed = True

    if changed:
        _atomic_write(path, yaml.safe_dump(raw, sort_keys=False))
    return {
        "config": str(path),
        "department": department,
        "outbox": outbox,
        "kind": matching[0].get("kind", kind) if matching else kind,
        "registered": True,
        "changed": changed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register one outbox with the factory mailroom")
    parser.add_argument("--config", required=True)
    parser.add_argument("--department", required=True)
    parser.add_argument("--outbox", required=True)
    args = parser.parse_args(argv)
    try:
        result = register_watch(args.config, args.department, args.outbox)
    except (ConfigError, OSError, yaml.YAMLError) as exc:
        LOGGER.error("mailroom registration failed: %s", exc)
        return 2
    print(yaml.safe_dump(result, sort_keys=False), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
