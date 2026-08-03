"""Render event-driven Loop Factory triage units without invoking systemctl."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
TRIAGE_TEMPLATE = HERE / "loop-factory-triage@.service"
OUTBOX_PATH_TEMPLATE = HERE / "outbox-watch@.path"
OUTBOX_SERVICE_TEMPLATE = HERE / "outbox-watch@.service"
DROPIN = "[Unit]\nOnFailure=loop-factory-triage@%n.service\n"


def _unit_value(value: str) -> str:
    """Escape one literal systemd value while preserving readable normal paths."""
    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("systemd unit values may not contain NUL or newlines")
    return (
        value.replace("\\", "\\x5c")
        .replace("%", "%%")
        .replace(" ", "\\x20")
        .replace("\t", "\\x09")
    )


def _service_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValueError("--units entries must not be empty")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError(f"invalid systemd service name: {value!r}")
    if "." not in name:
        name += ".service"
    if not name.endswith(".service"):
        raise ValueError(f"OnFailure hooks require a .service unit: {value!r}")
    return name


def _outbox_instance(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def _render(path: Path, replacements: dict[str, str]) -> str:
    text = path.read_text(encoding="utf-8")
    for marker, value in replacements.items():
        text = text.replace("{{" + marker + "}}", value)
    if "{{" in text or "}}" in text:
        raise ValueError(f"unresolved template marker in {path}")
    return text


def install(
    repo_root: str | Path,
    user_unit_dir: str | Path,
    *,
    units: list[str] | None = None,
    outboxes: list[str] | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    unit_dir = Path(user_unit_dir).expanduser().resolve()
    repo_value = _unit_value(str(root))
    planned: dict[Path, str] = {
        unit_dir / "loop-factory-triage@.service": _render(
            TRIAGE_TEMPLATE, {"REPO": repo_value}
        )
    }

    for configured in outboxes or []:
        configured_path = Path(configured).expanduser()
        outbox = (
            configured_path if configured_path.is_absolute() else root / configured_path
        ).resolve()
        instance = _outbox_instance(outbox)
        service_name = f"outbox-watch@{instance}.service"
        replacements = {
            "REPO": repo_value,
            "OUTBOX": _unit_value(str(outbox)),
            "OUTBOX_DESCRIPTION": _unit_value(outbox.name),
            "WATCH_SERVICE": service_name,
        }
        planned[unit_dir / f"outbox-watch@{instance}.path"] = _render(
            OUTBOX_PATH_TEMPLATE, replacements
        )
        planned[unit_dir / service_name] = _render(
            OUTBOX_SERVICE_TEMPLATE, replacements
        )

    dropins: set[Path] = set()
    for configured in units or []:
        service = _service_name(configured)
        target = unit_dir / f"{service}.d" / "10-triage-onfailure.conf"
        planned[target] = DROPIN
        dropins.add(target)

    conflicts = [
        path
        for path in sorted(dropins)
        if path.exists() and path.read_text(encoding="utf-8") != DROPIN
    ]
    if conflicts and not force:
        joined = ", ".join(str(path) for path in conflicts)
        raise ValueError(f"refusing to overwrite non-matching OnFailure drop-in: {joined}")

    written: list[str] = []
    unchanged: list[str] = []
    would_write: list[str] = []
    for path, content in sorted(planned.items(), key=lambda item: str(item[0])):
        if path.exists() and path.read_text(encoding="utf-8") == content:
            unchanged.append(str(path))
            continue
        would_write.append(str(path))
        if dry_run:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))

    return {
        "dry_run": dry_run,
        "files_planned": [str(path) for path in sorted(planned)],
        "files_would_write": would_write,
        "files_written": written,
        "files_unchanged": unchanged,
        "daemon_reload_required": bool(would_write),
        "reminder": "Owner/coordinator reviews the files, then runs: systemctl --user daemon-reload",
        "systemctl_invoked": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument(
        "--user-unit-dir",
        default=str(Path("~/.config/systemd/user").expanduser()),
    )
    parser.add_argument("--units", nargs="*", default=[])
    parser.add_argument("--outboxes", nargs="*", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = install(
            args.repo_root,
            args.user_unit_dir,
            units=args.units,
            outboxes=args.outboxes,
            dry_run=args.dry_run,
            force=args.force,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
