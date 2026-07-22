"""Fail-closed budget reservations for kernel work."""

import json
from pathlib import Path


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
        self.ceilings = dict(DEFAULT_CEILINGS if ceilings is None else ceilings)
        self._reservations = {}
        self._telemetry_failed = False
        self._load_ledger()

    def _load_ledger(self):
        if not self.ledger_path.exists():
            return
        try:
            with self.ledger_path.open(encoding="utf-8") as ledger:
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
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            self._telemetry_failed = True

    def _append(self, row):
        try:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as ledger:
                ledger.write(json.dumps(row, separators=(",", ":")) + "\n")
        except OSError as exc:
            self._telemetry_failed = True
            raise BudgetExceeded("budget telemetry unavailable") from exc

    def usage(self, kind):
        return sum(
            amount if actual is None else actual
            for reserved_kind, amount, actual in self._reservations.values()
            if reserved_kind == kind
        )

    def reserve(self, kind, amount, now):
        if self._telemetry_failed:
            raise BudgetExceeded("budget telemetry unavailable")
        if kind not in self.ceilings:
            raise BudgetExceeded("no ceiling for " + kind)

        projected = self.usage(kind) + amount
        cap = self.ceilings[kind]
        if projected > cap:
            raise BudgetExceeded(f"{kind} budget exceeded")
        if projected >= 0.8 * cap:
            raise BudgetReviewRequired(f"{kind} budget requires review")

        rid = f"{kind}-{len(self._reservations)}-{now}"
        self._append({"rid": rid, "kind": kind, "amount": amount, "now": now})
        self._reservations[rid] = (kind, amount, None)
        return rid

    def commit(self, rid, actual):
        kind, amount, _ = self._reservations[rid]
        self._append({"event": "commit", "rid": rid, "actual": actual})
        self._reservations[rid] = (kind, amount, actual)

    def release(self, rid):
        self._append({"event": "release", "rid": rid})
        del self._reservations[rid]
