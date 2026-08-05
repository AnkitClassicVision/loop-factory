"""Pull approval packets into a local mailroom outbox.

The source may be a local JSONL file or a file read through SSH.  The local
destination is the durable deduplication ledger: only exact, nonblank source
rows that are not already present are appended.  Destination replacement is
atomic, and no destination write is attempted until the complete source has
been fetched and validated.
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


class PullError(RuntimeError):
    """Raised when a pull must fail without changing the destination."""


def _read_local(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PullError(f"cannot read source {path}: {exc}") from exc


def _read_remote(source: str, ssh_host: str, identity: str | None) -> bytes:
    command = ["ssh"]
    if identity:
        # The identity is passed directly to SSH.  This process never opens it.
        command.extend(["-i", identity])
    command.extend(["--", ssh_host, "cat", "--", shlex.quote(source)])
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise PullError(f"cannot start ssh transport: {exc}") from exc
    if completed.returncode != 0:
        reason = completed.stderr.decode("utf-8", errors="replace").strip()
        reason = " ".join(reason.splitlines()) or f"ssh exited {completed.returncode}"
        raise PullError(f"cannot fetch source from {ssh_host}: {reason}")
    return completed.stdout


def _json_lines(content: bytes, label: str) -> list[str]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PullError(f"{label} is not valid UTF-8: {exc}") from exc

    rows: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            raise PullError(
                f"{label} line {line_number} is invalid JSON: "
                f"{exc.msg} at column {exc.colno}"
            ) from exc
        rows.append(line)
    return rows


def _read_destination(path: Path) -> bytes:
    if not path.exists():
        return b""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PullError(f"cannot read destination {path}: {exc}") from exc


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
        raise PullError(f"cannot atomically update destination {path}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def pull(
    source: str,
    destination: str | Path,
    *,
    ssh_host: str | None = None,
    identity: str | None = None,
) -> int:
    """Merge unseen source rows into destination and return the number added."""
    source_content = (
        _read_remote(source, ssh_host, identity)
        if ssh_host is not None
        else _read_local(Path(source))
    )
    source_rows = _json_lines(source_content, "source")

    destination_path = Path(destination)
    destination_content = _read_destination(destination_path)
    destination_rows = _json_lines(destination_content, "destination")
    present = set(destination_rows)

    additions: list[str] = []
    for row in source_rows:
        if row in present:
            continue
        additions.append(row)
        present.add(row)

    if not additions:
        return 0

    separator = (
        b""
        if not destination_content or destination_content.endswith((b"\n", b"\r"))
        else b"\n"
    )
    appended = "".join(f"{row}\n" for row in additions).encode("utf-8")
    _atomic_write(destination_path, destination_content + separator + appended)
    return len(additions)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pull a local or remote JSONL outbox into the local mailroom"
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--ssh-host")
    parser.add_argument("--identity")
    args = parser.parse_args(argv)

    if args.identity and not args.ssh_host:
        parser.error("--identity requires --ssh-host")

    try:
        added = pull(
            args.source,
            args.dest,
            ssh_host=args.ssh_host,
            identity=args.identity,
        )
    except PullError as exc:
        print(f"remote outbox pull failed: {exc}", file=sys.stderr)
        return 1

    print(f"{added} rows added")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
