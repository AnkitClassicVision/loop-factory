"""Plan and send recurring re-escalations for open human-outbox cards.

Plan-only usage::

    python3 factory/reescalate.py --ledger card_ledger.jsonl \
        --now 2026-08-05T12:00:00+00:00 --plan-only

Send mode additionally requires ``--config``. The preferred YAML shape is::

    reescalation:
      sender:
        - sender-command
        - --issue
        - "{card_identifier}"
        - --text
        - "{text}"

For compatibility with the outbox configuration, ``senders.reescalation`` and
``senders.ping`` are also accepted. Sender values are argv templates, never
shell commands. Available placeholders are ``{card_identifier}``, ``{issue}``,
``{reescalation_count}``, ``{reason}``, ``{text}``, ``{now}``,
``{department}``, and ``{first_raised}``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml


LOGGER = logging.getLogger(__name__)
ELIGIBLE_STATUSES = frozenset({"open", "fix_requested", "snoozed"})
DELIVERY_PENDING = "delivery_pending"
NORMAL_BASE_HOURS = 48
NORMAL_CAP_HOURS = 336
URGENT_FLOOR_HOURS = 2


class ReescalationError(ValueError):
    """Raised when input or configuration cannot be used safely."""


@dataclass(frozen=True)
class DueCard:
    card_identifier: str
    reescalation_count: int
    reason: str
    row: dict[str, Any]

    def public(self) -> dict[str, str | int]:
        return {
            "card_identifier": self.card_identifier,
            "reescalation_count": self.reescalation_count,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class QuarantinedRow:
    identity: str
    reason: str


def _datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ReescalationError(f"{field} must be a non-empty ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReescalationError(f"{field} is not valid ISO 8601: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReescalationError("reescalation_count must be a non-negative integer")
    return value


def _latest_rows(
    path: str | Path,
) -> tuple[list[tuple[int, dict[str, Any]]], list[QuarantinedRow]]:
    """Return the complete last ledger row for each row hash."""
    ledger = Path(path)
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReescalationError(f"ledger could not be read: {exc}") from exc

    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    quarantined: list[QuarantinedRow] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReescalationError(
                f"ledger line {line_number} is invalid JSON: {exc}"
            ) from exc
        if not isinstance(row, dict):
            quarantined.append(
                QuarantinedRow(
                    identity=f"ledger line {line_number}",
                    reason=f"ledger line {line_number} is not a JSON object",
                )
            )
            continue
        row_hash = row.get("row_hash")
        if not isinstance(row_hash, str) or not row_hash:
            identifier = row.get("card_identifier")
            identity = (
                identifier
                if isinstance(identifier, str) and identifier
                else f"ledger line {line_number}"
            )
            quarantined.append(
                QuarantinedRow(
                    identity=identity,
                    reason=f"ledger line {line_number} lacks row_hash",
                )
            )
            continue
        latest[row_hash] = (line_number, row)
    return list(latest.values()), quarantined


def _cards(
    path: str | Path,
) -> tuple[list[dict[str, Any]], list[QuarantinedRow]]:
    """Collapse eligible latest ledger rows to one stable row per card."""
    grouped: dict[str, dict[str, Any]] = {}
    latest, quarantined = _latest_rows(path)
    for _, row in latest:
        if row.get("status") == DELIVERY_PENDING:
            identifier = row.get("card_identifier")
            quarantined.append(
                QuarantinedRow(
                    identity=identifier if isinstance(identifier, str) and identifier else row["row_hash"],
                    reason="delivery confirmation is pending manual reconciliation",
                )
            )
            continue
        if row.get("status") not in ELIGIBLE_STATUSES:
            continue
        identifier = row.get("card_identifier")
        if not isinstance(identifier, str) or not identifier:
            continue
        key = row["row_hash"] if row.get("status") == "snoozed" else identifier
        grouped.setdefault(key, row)
    return list(grouped.values()), quarantined


def _normal_interval(count: int) -> timedelta:
    if count <= 2:
        hours = NORMAL_BASE_HOURS
    else:
        hours = min(
            NORMAL_BASE_HOURS * (2 ** (count - 2)),
            NORMAL_CAP_HOURS,
        )
    return timedelta(hours=hours)


def _cadence(
    row: dict[str, Any], now: datetime
) -> tuple[datetime, timedelta, str, int]:
    count = _count(row.get("reescalation_count"))
    if row.get("status") == "snoozed":
        wake = _datetime(row.get("snooze_until"), "snooze_until")
        return wake, timedelta(0), "FYI snooze expired", count
    clock_field = "last_ping_at" if row.get("last_ping_at") else "first_raised"
    start = _datetime(row.get(clock_field), clock_field)
    urgency = row.get("urgency", "normal")
    if urgency == "normal":
        interval = _normal_interval(count)
        reason = f"normal cadence: {int(interval.total_seconds() // 3600)}h elapsed"
    elif urgency == "urgent":
        due = _datetime(row.get("due"), "due")
        if start >= due:
            interval = timedelta(hours=URGENT_FLOOR_HOURS)
            reason = "urgent cadence: past due, 2h interval elapsed"
        else:
            interval = max(
                (due - start) / 2,
                timedelta(hours=URGENT_FLOOR_HOURS),
            )
            hours = interval.total_seconds() / 3600
            reason = f"urgent cadence: {hours:g}h midpoint interval elapsed"
    else:
        raise ReescalationError("urgency must be 'normal' or 'urgent'")
    return start, interval, reason, count


def _due_cards_with_quarantine(
    path: str | Path, now: datetime
) -> tuple[list[DueCard], list[QuarantinedRow]]:
    due: list[DueCard] = []
    rows, quarantined = _cards(path)
    for row in rows:
        try:
            start, interval, reason, count = _cadence(row, now)
        except ReescalationError as exc:
            quarantined.append(
                QuarantinedRow(
                    identity=row["card_identifier"],
                    reason=str(exc),
                )
            )
            continue
        if now >= start + interval:
            due.append(
                DueCard(
                    card_identifier=row["card_identifier"],
                    reescalation_count=count,
                    reason=reason,
                    row=row,
                )
            )
    return due, quarantined


def due_cards(path: str | Path, now: datetime) -> list[DueCard]:
    due, _ = _due_cards_with_quarantine(path, now)
    return due


def _sender_from_config(path: str | Path) -> list[str]:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ReescalationError(f"cannot load config: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReescalationError("config must be a YAML mapping")

    value: Any = None
    section = raw.get("reescalation")
    if isinstance(section, dict):
        value = section.get("sender")
    senders = raw.get("senders")
    if value is None and isinstance(senders, dict):
        value = senders.get("reescalation", senders.get("ping"))
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ReescalationError(
            "reescalation sender must be a non-empty argv list"
        )
    joined = "\0".join(value)
    if not any(
        placeholder in joined
        for placeholder in ("{card_identifier}", "{issue}", "{text}")
    ):
        raise ReescalationError(
            "reescalation sender must identify the card with "
            "{card_identifier}, {issue}, or {text}"
        )
    return value


def _render(template: list[str], values: dict[str, str]) -> list[str]:
    rendered: list[str] = []
    for item in template:
        for key, value in values.items():
            item = item.replace("{" + key + "}", value)
        rendered.append(item)
    return rendered


def _append_ping(path: str | Path, card: DueCard, now_text: str) -> None:
    row = dict(card.row)
    row.update(
        {
            "ts": now_text,
            "last_ping_at": now_text,
            "reescalation_count": card.reescalation_count + 1,
            "status": "open" if card.row["status"] == "snoozed" else card.row["status"],
        }
    )
    ledger = Path(path)
    try:
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError as exc:
        raise ReescalationError(f"ledger ping row could not be appended: {exc}") from exc


def _append_delivery_row(
    path: str | Path, card: DueCard, now_text: str, status: str
) -> None:
    row = dict(card.row)
    row.update({"ts": now_text, "status": status})
    if status == DELIVERY_PENDING:
        identity = (
            f"{card.row['row_hash']}\n{card.reescalation_count + 1}\n{now_text}"
        )
        row["delivery_intent_key"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        row["delivery_prior_status"] = card.row["status"]
    ledger = Path(path)
    try:
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError as exc:
        raise ReescalationError(
            f"ledger delivery row could not be appended: {exc}"
        ) from exc


def send_due(
    ledger: str | Path,
    cards: list[DueCard],
    sender: list[str],
    now_text: str,
) -> int:
    failures = 0
    for card in cards:
        values = {
            "card_identifier": card.card_identifier,
            "issue": card.card_identifier,
            "reescalation_count": str(card.reescalation_count + 1),
            "reason": card.reason,
            "now": now_text,
            "department": (
                card.row.get("department")
                if isinstance(card.row.get("department"), str)
                else ""
            ),
            "first_raised": (
                card.row.get("first_raised")
                if isinstance(card.row.get("first_raised"), str)
                else ""
            ),
            "action_mode": (
                card.row.get("action_mode")
                if isinstance(card.row.get("action_mode"), str)
                else "decision"
            ),
            "text": (
                f"Re-escalation due for {card.card_identifier}. "
                f"Ping {card.reescalation_count + 1}. {card.reason}."
            ),
        }
        argv = _render(sender, values)
        try:
            _append_delivery_row(ledger, card, now_text, DELIVERY_PENDING)
        except ReescalationError as exc:
            LOGGER.error("%s", exc)
            failures += 1
            continue
        try:
            result = subprocess.run(argv, check=False)
        except OSError as exc:
            LOGGER.error("sender could not start for %s: %s", card.card_identifier, exc)
            failures += 1
            try:
                _append_delivery_row(ledger, card, now_text, card.row["status"])
            except ReescalationError as compensation_exc:
                LOGGER.error("%s", compensation_exc)
            continue
        if result.returncode != 0:
            LOGGER.error(
                "sender failed for %s with exit code %d",
                card.card_identifier,
                result.returncode,
            )
            failures += 1
            try:
                _append_delivery_row(ledger, card, now_text, card.row["status"])
            except ReescalationError as compensation_exc:
                LOGGER.error("%s", compensation_exc)
            continue
        try:
            _append_ping(ledger, card, now_text)
        except ReescalationError as exc:
            LOGGER.error("%s", exc)
            failures += 1
    return 3 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-escalate unanswered cards")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--now")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    try:
        now_text = (
            args.now
            if args.now is not None
            else datetime.now(timezone.utc).isoformat()
        )
        now = _datetime(now_text, "now")
        cards, quarantined = _due_cards_with_quarantine(args.ledger, now)
        for row in quarantined:
            print(f"quarantined {row.identity}: {row.reason}")
            LOGGER.error("quarantined %s: %s", row.identity, row.reason)
        print(
            json.dumps(
                {
                    "due": [card.public() for card in cards],
                    "quarantined": len(quarantined),
                }
            )
        )
        if args.plan_only:
            return 2 if quarantined else 0
        if not args.config:
            raise ReescalationError("send mode requires --config")
        sender = _sender_from_config(args.config)
        send_result = send_due(args.ledger, cards, sender, now_text)
        return 2 if quarantined else send_result
    except ReescalationError as exc:
        LOGGER.error("re-escalation refused: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
