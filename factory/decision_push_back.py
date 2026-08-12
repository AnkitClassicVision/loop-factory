"""Push recorded decisions back to the machine that raised their packets.

The local decisions ledger may contain rows for many departments.  Only rows
for the requested department with a usable ``packet_id`` cross the boundary.
The destination is an append-only, exact-row deduplication ledger, matching
the conventions used by :mod:`remote_outbox_pull`.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class PushBackError(RuntimeError):
    """Raised when a push must fail without changing the destination."""


def _read_local(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PushBackError(f"cannot read {label} {path}: {exc}") from exc


def _json_rows(content: bytes, label: str) -> list[tuple[str, dict[str, Any]]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PushBackError(f"{label} is not valid UTF-8: {exc}") from exc

    rows: list[tuple[str, dict[str, Any]]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PushBackError(
                f"{label} line {line_number} is invalid JSON: "
                f"{exc.msg} at column {exc.colno}"
            ) from exc
        if not isinstance(value, dict):
            raise PushBackError(f"{label} line {line_number} is not a JSON object")
        rows.append((line, value))
    return rows


def _selected_rows(content: bytes, department: str) -> tuple[list[str], int]:
    selected: list[str] = []
    skipped = 0
    for line, row in _json_rows(content, "decisions file"):
        packet_id = row.get("packet_id")
        if row.get("department") != department or not isinstance(packet_id, str) or not packet_id.strip():
            skipped += 1
            continue
        selected.append(line)
    return selected, skipped


def _destination_rows(content: bytes) -> list[str]:
    return [line for line, _ in _json_rows(content, "destination")]


def _atomic_write(path: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise PushBackError(
            f"cannot atomically update destination {path}: {exc}"
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _merged_content(destination_content: bytes, rows: list[str]) -> tuple[bytes, int]:
    present = set(_destination_rows(destination_content))
    additions: list[str] = []
    for row in rows:
        if row in present:
            continue
        additions.append(row)
        present.add(row)

    if not additions:
        return destination_content, 0

    separator = (
        b""
        if not destination_content or destination_content.endswith((b"\n", b"\r"))
        else b"\n"
    )
    appended = "".join(f"{row}\n" for row in additions).encode("utf-8")
    return destination_content + separator + appended, len(additions)


def _push_local(destination: Path, rows: list[str]) -> int:
    destination_content = (
        _read_local(destination, "destination") if destination.exists() else b""
    )
    merged, added = _merged_content(destination_content, rows)
    if added:
        _atomic_write(destination, merged)
    return added


_REMOTE_MERGE = r"""
import json
import os
import pathlib
import sys
import tempfile

destination = pathlib.Path(sys.argv[1])
incoming_text = sys.stdin.buffer.read().decode("utf-8")
incoming = [line for line in incoming_text.splitlines() if line.strip()]
for number, line in enumerate(incoming, 1):
    if not isinstance(json.loads(line), dict):
        raise ValueError(f"incoming line {number} is not a JSON object")

if destination.exists():
    original = destination.read_bytes()
else:
    original = b""
text = original.decode("utf-8")
existing = [line for line in text.splitlines() if line.strip()]
for number, line in enumerate(existing, 1):
    if not isinstance(json.loads(line), dict):
        raise ValueError(f"destination line {number} is not a JSON object")

present = set(existing)
additions = []
for line in incoming:
    if line not in present:
        additions.append(line)
        present.add(line)

if additions:
    separator = b"" if not original or original.endswith((b"\n", b"\r")) else b"\n"
    content = original + separator + "".join(line + "\n" for line in additions).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        handle = tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix="." + destination.name + ".",
            suffix=".tmp", delete=False
        )
        temporary = pathlib.Path(handle.name)
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

print(len(additions))
"""


def _push_remote(
    destination: str, rows: list[str], ssh_host: str, identity: str | None
) -> int:
    command = ["ssh"]
    if identity:
        # The identity path is passed directly to SSH and is never opened here.
        command.extend(["-i", identity])
    command.extend(
        [
            "--",
            ssh_host,
            "python3",
            "-c",
            shlex.quote(_REMOTE_MERGE),
            shlex.quote(destination),
        ]
    )
    payload = "".join(f"{row}\n" for row in rows).encode("utf-8")
    try:
        completed = subprocess.run(
            command,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise PushBackError(f"cannot start ssh transport: {exc}") from exc
    if completed.returncode != 0:
        reason = completed.stderr.decode("utf-8", errors="replace").strip()
        reason = " ".join(reason.splitlines()) or f"ssh exited {completed.returncode}"
        raise PushBackError(f"cannot update destination on {ssh_host}: {reason}")
    output = completed.stdout.decode("utf-8", errors="replace").strip()
    try:
        return int(output)
    except ValueError as exc:
        raise PushBackError(
            f"destination on {ssh_host} returned an invalid row count"
        ) from exc


def push_back(
    decisions: str | Path,
    department: str,
    destination: str | Path,
    *,
    ssh_host: str | None = None,
    identity: str | None = None,
) -> tuple[int, int]:
    """Push eligible decisions and return ``(rows_added, rows_skipped)``."""
    decisions_content = _read_local(Path(decisions), "decisions file")
    selected, skipped = _selected_rows(decisions_content, department)
    added = (
        _push_remote(str(destination), selected, ssh_host, identity)
        if ssh_host is not None
        else _push_local(Path(destination), selected)
    )
    return added, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Push recorded decisions back to their requesting machine"
    )
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--department", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--ssh-host")
    parser.add_argument("--identity")
    args = parser.parse_args(argv)

    if args.identity and not args.ssh_host:
        parser.error("--identity requires --ssh-host")

    try:
        added, skipped = push_back(
            args.decisions,
            args.department,
            args.dest,
            ssh_host=args.ssh_host,
            identity=args.identity,
        )
    except PushBackError as exc:
        print(f"decision push back failed: {exc}", file=sys.stderr)
        return 1

    print(f"{added} rows added, {skipped} rows skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
