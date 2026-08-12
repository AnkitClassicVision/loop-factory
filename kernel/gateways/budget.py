"""Fail-closed budget reservations for kernel work."""

import fcntl
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path

from kernel.jsonl_store import (
    append_jsonl,
    ensure_parent_dirs,
    open_no_follow,
    trusted_root_for,
)


DEFAULT_CEILINGS = {
    "model_calls": 900,
    "dollars": 40,
    "worker_minutes": 1200,
}


class BudgetExceeded(RuntimeError):
    """Raised when work has no budget or would exceed its ceiling."""


class BudgetReviewRequired(RuntimeError):
    """Raised when projected usage reaches the review threshold."""


class BudgetBroker:
    def __init__(self, ledger_path, ceilings=None):
        self.ledger_path = Path(ledger_path)
        self.trusted_root = trusted_root_for(self.ledger_path)
        self.ceilings = dict(DEFAULT_CEILINGS if ceilings is None else ceilings)
        self._reservations = {}
        self._telemetry_failed = False
        self._load_ledger()

    def _load_ledger(self):
        self._reservations = {}
        try:
            fd = open_no_follow(
                self.ledger_path, os.O_RDONLY, trusted_root=self.trusted_root
            )
            with os.fdopen(fd, encoding="utf-8") as ledger:
                for line in ledger:
                    row = json.loads(line)
                    event = row.get("event", "reserve")
                    rid = row["rid"]
                    if event == "reserve":
                        self._reservations[rid] = (
                            row["kind"],
                            row["amount"],
                            None,
                        )
                    elif event == "commit":
                        kind, amount, _ = self._reservations[rid]
                        self._reservations[rid] = (kind, amount, row["actual"])
                    elif event == "release":
                        del self._reservations[rid]
                    else:
                        raise ValueError(f"unknown ledger event: {event}")
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            self._telemetry_failed = True

    @contextmanager
    def _transaction(self):
        lock_path = self.ledger_path.with_suffix(self.ledger_path.suffix + ".lock")
        ensure_parent_dirs(lock_path, trusted_root=self.trusted_root)
        try:
            fd = open_no_follow(
                lock_path,
                os.O_CREAT | os.O_RDWR,
                0o600,
                trusted_root=self.trusted_root,
            )
        except OSError as exc:
            self._telemetry_failed = True
            raise BudgetExceeded("budget telemetry unavailable") from exc
        with os.fdopen(fd, "a+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                self._telemetry_failed = False
                self._load_ledger()
                if self._telemetry_failed:
                    raise BudgetExceeded("budget telemetry unavailable")
                yield
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _append(self, row):
        try:
            append_jsonl(
                self.ledger_path, row, trusted_root=self.trusted_root
            )
        except (OSError, TypeError, ValueError) as exc:
            self._telemetry_failed = True
            raise BudgetExceeded("budget telemetry unavailable") from exc

    @property
    def telemetry_ok(self):
        """False when the ledger existed but could not be replayed — readers
        must treat usage() as unverifiable, never as zero."""
        return not self._telemetry_failed

    def usage(self, kind):
        return sum(
            amount if actual is None else actual
            for reserved_kind, amount, actual in self._reservations.values()
            if reserved_kind == kind
        )

    def reserve(self, kind, amount, now):
        with self._transaction():
            if kind not in self.ceilings:
                raise BudgetExceeded("no ceiling for " + kind)

            projected = self.usage(kind) + amount
            cap = self.ceilings[kind]
            if projected > cap:
                raise BudgetExceeded(f"{kind} budget exceeded")
            if projected >= 0.8 * cap:
                raise BudgetReviewRequired(f"{kind} budget requires review")

            rid = f"{kind}-{now}-{os.urandom(8).hex()}"
            self._append({"rid": rid, "kind": kind, "amount": amount, "now": now})
            self._reservations[rid] = (kind, amount, None)
            return rid

    def commit(self, rid, actual):
        if (
            isinstance(actual, bool)
            or not isinstance(actual, (int, float))
            or not math.isfinite(actual)
            or actual < 0
        ):
            raise BudgetExceeded("budget actual must be a finite non-negative number")
        with self._transaction():
            kind, amount, _ = self._reservations[rid]
            self._append({"event": "commit", "rid": rid, "actual": actual})
            self._reservations[rid] = (kind, amount, actual)

    def release(self, rid):
        with self._transaction():
            self._reservations[rid]
            self._append({"event": "release", "rid": rid})
            del self._reservations[rid]
