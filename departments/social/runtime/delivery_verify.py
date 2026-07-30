"""Platform-confirmed delivery verification for social posts."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOG = logging.getLogger("social.delivery_verify")
SUCCESS_STATUSES = {"published", "delivered", "live", "simulated"}


class VerificationBlocked(RuntimeError):
    """The platform did not independently prove delivery."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read_object(path: str | Path, label: str) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise VerificationBlocked(f"{label} source missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationBlocked(f"{label} is malformed: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationBlocked(f"{label} must be a JSON object")
    return value


def _read_sink(path: Path, post_ref: str) -> dict[str, Any]:
    if not path.exists():
        raise VerificationBlocked(f"simulate sink missing: {path}")
    found: list[dict[str, Any]] = []
    line_number = 0
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            if row.get("post_ref") == post_ref:
                found.append(row)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise VerificationBlocked(
            f"simulate sink malformed at line {line_number}: {exc}"
        ) from exc
    if len(found) != 1:
        raise VerificationBlocked(
            f"simulate sink resolved post_ref to {len(found)} records"
        )
    return found[0]


def _last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    raise VerificationBlocked("zernio status returned no JSON object")


def _objects(value: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [value]
    for key in ("post", "data", "result"):
        child = value.get(key)
        if isinstance(child, dict):
            candidates.append(child)
            for nested_key in ("post", "result"):
                nested = child.get(nested_key)
                if isinstance(nested, dict):
                    candidates.append(nested)
    return candidates


def _platform_confirmation(value: dict[str, Any]) -> tuple[str, str]:
    platform_post_id = ""
    status = ""
    for candidate in _objects(value):
        if not platform_post_id:
            for key in (
                "platform_post_id",
                "platformPostId",
                "external_post_id",
                "externalPostId",
                "external_id",
                "externalId",
            ):
                found = candidate.get(key)
                if isinstance(found, str) and found:
                    platform_post_id = found
                    break
        if not status and isinstance(candidate.get("status"), str):
            status = candidate["status"].casefold()
    if not platform_post_id:
        raise VerificationBlocked(
            "platform response lacks an independent platform_post_id"
        )
    if not status:
        raise VerificationBlocked("platform response lacks delivery status")
    return platform_post_id, status


def verify(
    state_dir: str | Path,
    receipt: dict[str, Any],
    *,
    zernio_cmd: str = "zernio",
    simulate_sink: str | Path | None = None,
) -> dict[str, Any]:
    required = {"post_ref", "surface", "delivered_count", "simulated", "ts"}
    missing = sorted(required - set(receipt))
    if missing:
        raise VerificationBlocked(
            "dispatch receipt missing fields: " + ", ".join(missing)
        )
    post_ref = receipt["post_ref"]
    if not isinstance(post_ref, str) or not post_ref:
        raise VerificationBlocked("dispatch receipt post_ref is invalid")
    if not isinstance(receipt["simulated"], bool):
        raise VerificationBlocked("dispatch receipt simulated flag is invalid")

    if receipt["simulated"]:
        if receipt["delivered_count"] != 0:
            raise VerificationBlocked(
                "simulated dispatch receipt does not prove delivered_count=0"
            )
        row = _read_sink(
            Path(simulate_sink or Path(state_dir) / "simulate_delivery.jsonl"),
            post_ref,
        )
        platform_post_id = row.get("platform_post_id")
        status = row.get("status")
        if not isinstance(platform_post_id, str) or not platform_post_id:
            raise VerificationBlocked("simulate sink lacks platform_post_id")
        if platform_post_id == post_ref:
            raise VerificationBlocked(
                "scheduler echo is not independent delivery verification"
            )
        if status != "simulated" or row.get("source") != "simulate_sink":
            raise VerificationBlocked("simulate sink did not confirm synthetic delivery")
    else:
        command = shlex.split(zernio_cmd)
        if not command:
            raise VerificationBlocked("zernio command is empty")
        proc = subprocess.run(
            command + ["posts:get", post_ref],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise VerificationBlocked(
                f"zernio status failed with exit {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:200]}"
            )
        platform_post_id, status = _platform_confirmation(
            _last_json(proc.stdout or "")
        )
        if platform_post_id == post_ref:
            raise VerificationBlocked(
                "scheduler echo is not independent delivery verification"
            )
        if status not in SUCCESS_STATUSES - {"simulated"}:
            raise VerificationBlocked(f"platform delivery status is {status!r}")

    return {
        "post_ref": post_ref,
        "platform_post_id": platform_post_id,
        "status": status,
        "verified": True,
        "ts": utc_now(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify social post delivery")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--simulate-sink")
    parser.add_argument("--zernio-cmd", default="zernio")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = Path(args.out)
    try:
        result = verify(
            args.state_dir,
            _read_object(args.receipt, "dispatch receipt"),
            zernio_cmd=args.zernio_cmd,
            simulate_sink=args.simulate_sink,
        )
        atomic_write_json(out, result)
        return 0
    except (VerificationBlocked, OSError, subprocess.SubprocessError, ValueError) as exc:
        LOG.error("delivery verification blocked: %s", exc)
        atomic_write_json(
            out,
            {
                "status": "failed",
                "verified": False,
                "reason": str(exc),
                "ts": utc_now(),
            },
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
