"""Exclusive, expiring driver lease with durable arbitration receipts."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class Lease:
    path: Path
    holder: str
    nonce: str
    expires_at: str


class LeaseHeld(RuntimeError):
    def __init__(self, holder: str):
        self.holder = holder
        super().__init__(f"lease held by {holder}")


def _utc(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("holder"), str):
        raise ValueError("invalid lease")
    return value


def _payload(holder: str, ttl_s: int, now: datetime, takeover_from: str | None = None) -> dict:
    row = {
        "holder": holder,
        "acquired_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_s)).isoformat(),
        "nonce": uuid.uuid4().hex,
    }
    if takeover_from is not None:
        row["takeover_from"] = takeover_from
    return row


def acquire(state_dir, *, holder: str, ttl_s: int, now=None) -> Lease:
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    path = state / "driver.lease"
    current = _utc(now)
    row = _payload(holder, ttl_s, current)
    encoded = (json.dumps(row, sort_keys=True) + "\n").encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = _read(path)
        try:
            expires = datetime.fromisoformat(str(existing["expires_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        except (KeyError, TypeError, ValueError) as exc:
            raise LeaseHeld(existing["holder"]) from exc
        if expires > current:
            raise LeaseHeld(existing["holder"])
        row = _payload(holder, ttl_s, current, existing["holder"])
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=state, prefix=".driver.lease.", delete=False) as handle:
                temp_name = handle.name
                json.dump(row, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
    else:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    return Lease(path=path, holder=holder, nonce=row["nonce"], expires_at=row["expires_at"])


def release(lease: Lease) -> None:
    current = _read(lease.path)
    if current.get("nonce") != lease.nonce:
        raise LeaseHeld(str(current.get("holder", "unknown")))
    lease.path.unlink()


def refusal_receipt(state_dir, *, loser: str, holder: str, now=None) -> Path:
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    path = state / "lease-refusals.jsonl"
    row = {"ts": _utc(now).isoformat(), "loser": loser, "holder": holder}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path

