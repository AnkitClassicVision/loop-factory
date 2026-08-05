"""Listen for human decisions on cards recorded by :mod:`outbox_push`.

The listener uses the same YAML file as the push process::

    ledger_file: path/to/card_ledger.jsonl
    listener:
      reader:
        - reader-command
        - --issue
        - "{issue}"
      closer:
        - closer-command
        - --issue
        - "{issue}"
        - --state
        - "{state}"
      close_enabled: true
      ack:
        - comment-command
        - --issue
        - "{issue}"
        - --body
        - "{body}"
      decisions_file: path/to/decisions.jsonl

All commands are argv templates, never shell commands. ``reader`` must contain
``{issue}`` and print either a JSON list of comments or a JSON object containing
one. Each comment must have a ``body``. ``closer`` uses ``{issue}`` and
``{state}``; optional ``ack`` uses ``{issue}`` and ``{body}``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


LOGGER = logging.getLogger(__name__)
DECISION_RE = re.compile(r"^(APPROVE|SKIP|FIX)\w*\b", re.IGNORECASE)
AGENT_MARKERS = (
    "AGENT CLAIMED:",
    "AGENT UPDATE:",
    "AGENT FOLLOW-UP:",
    "AGENT DONE:",
    "AGENT REVIEW",
    "AGENT BLOCKED:",
    "AGENT HUMAN HOLD:",
    "AGENT NEEDS INPUT:",
    "AGENT FAILED:",
    "QA REVIEW:",
)
TIMESTAMP_FIELDS = (
    "createdAt",
    "created_at",
    "updatedAt",
    "updated_at",
    "timestamp",
    "ts",
)
# Owner decision (Ankit 2026-08-05): a FIX reply is owned by the human, not
# silently re-queued to the department that raised it. The card moves to
# NEEDS_INPUT_STATE and stays open so the re-escalation keeps working against it.
OWNER = "ankit"
NEEDS_INPUT_STATE = "Agent Needs Input"
DONE_STATE = "Agent Done"
NOTES_LIMIT = 2000


class ConfigError(ValueError):
    """Raised when fail-closed configuration validation refuses a run."""


def _argv(
    value: Any,
    name: str,
    *,
    required: bool,
    placeholders: tuple[str, ...],
) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ConfigError(f"listener.{name} must be a non-empty argv list")
    joined = "\0".join(value)
    for placeholder in placeholders:
        if placeholder not in joined:
            raise ConfigError(
                f"listener.{name} must contain the {placeholder} placeholder"
            )
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load config: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config must be a YAML mapping")
    ledger_file = raw.get("ledger_file")
    listener = raw.get("listener")
    if not isinstance(ledger_file, str) or not ledger_file:
        raise ConfigError("ledger_file must be a non-empty path")
    if not isinstance(listener, dict):
        raise ConfigError("listener must be a mapping")
    close_enabled = listener.get("close_enabled")
    if not isinstance(close_enabled, bool):
        raise ConfigError("listener.close_enabled must be true or false")
    reader = _argv(
        listener.get("reader"),
        "reader",
        required=True,
        placeholders=("{issue}",),
    )
    closer = _argv(
        listener.get("closer"),
        "closer",
        required=close_enabled,
        placeholders=("{issue}", "{state}"),
    )
    ack = _argv(
        listener.get("ack"),
        "ack",
        required=False,
        placeholders=("{issue}", "{body}"),
    )
    decisions_file = listener.get("decisions_file")
    if not isinstance(decisions_file, str) or not decisions_file:
        raise ConfigError("listener.decisions_file must be a non-empty path")
    return {
        "ledger_file": ledger_file,
        "reader": reader,
        "closer": closer,
        "close_enabled": close_enabled,
        "ack": ack,
        "decisions_file": decisions_file,
    }


def _render(template: list[str], values: dict[str, str]) -> list[str]:
    return [
        item.replace("{issue}", values.get("issue", ""))
        .replace("{state}", values.get("state", ""))
        .replace("{body}", values.get("body", ""))
        for item in template
    ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _latest_ledger_rows(path: str | Path) -> dict[str, dict[str, Any]] | None:
    ledger = Path(path)
    if not ledger.exists():
        return {}
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        LOGGER.error("ledger could not be read: %s", exc)
        return None
    latest: dict[str, dict[str, Any]] = {}
    fix_hashes: dict[str, set[str]] = {}
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            LOGGER.error("ledger line %d is invalid JSON: %s", line_number, exc)
            return None
        if not isinstance(row, dict):
            LOGGER.error("ledger line %d is not a JSON object", line_number)
            return None
        row_hash = row.get("row_hash")
        status = row.get("status")
        if not isinstance(row_hash, str) or not row_hash or not isinstance(status, str):
            LOGGER.error("ledger line %d lacks row_hash or status", line_number)
            return None
        latest[row_hash] = row
        notes_hash = row.get("notes_hash")
        if status == "fix_requested" and isinstance(notes_hash, str) and notes_hash:
            fix_hashes.setdefault(row_hash, set()).add(notes_hash)
    for row_hash, row in latest.items():
        row["_fix_notes_hashes"] = fix_hashes.get(row_hash, set())
    return latest


def _last_json_value(output: str) -> list[Any] | dict[str, Any] | None:
    decoder = json.JSONDecoder()
    last: list[Any] | dict[str, Any] | None = None
    index = 0
    while index < len(output):
        starts = [
            position
            for position in (output.find("[", index), output.find("{", index))
            if position >= 0
        ]
        if not starts:
            break
        start = min(starts)
        try:
            value, end = decoder.raw_decode(output, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(value, (list, dict)):
            last = value
        index = max(end, start + 1)
    return last


def _comments_from_output(output: str) -> list[dict[str, Any]] | None:
    value = _last_json_value(output)
    if isinstance(value, list):
        comments = value
    elif isinstance(value, dict):
        comments = next(
            (candidate for candidate in value.values() if isinstance(candidate, list)),
            None,
        )
        if comments is None:
            return None
    else:
        return None
    if not all(
        isinstance(comment, dict) and isinstance(comment.get("body"), str)
        for comment in comments
    ):
        return None
    return comments


def _run_reader(argv: list[str]) -> list[dict[str, Any]] | None:
    try:
        result = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        LOGGER.error("reader could not start: %s", exc)
        return None
    if result.returncode != 0:
        LOGGER.error("reader failed with exit code %d", result.returncode)
        return None
    comments = _comments_from_output(result.stdout)
    if comments is None:
        LOGGER.error("reader output did not contain a valid comment list")
        return None
    return comments


def _timestamp(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _newest_first(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = list(enumerate(comments))

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
        index, comment = item
        for field in TIMESTAMP_FIELDS:
            parsed = _timestamp(comment.get(field))
            if parsed is not None:
                return parsed, index
        return float("-inf"), index

    return [comment for _, comment in sorted(indexed, key=sort_key, reverse=True)]


def _decision(comments: list[dict[str, Any]]) -> tuple[str, str, str] | None:
    for comment in _newest_first(comments):
        body = comment.get("body")
        if not isinstance(body, str) or not body:
            continue
        first_line = body.splitlines()[0].strip()
        if first_line.startswith(AGENT_MARKERS):
            continue
        match = DECISION_RE.match(first_line)
        if match:
            return match.group(1).lower(), first_line[:120], body
    return None


def _open_groups(latest: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Group still-open ledger rows by the card they actually point at.

    outbox_push appends one ledger row per outbox line, but the card creator
    dedupes by content and hands the SAME identifier back for related lines. In
    production 36 ledger rows collapsed onto 13 cards; ANK-293 alone carried 13.
    Polling per row would turn ONE human reply into one decision per row and fire
    the state mover once per row, so the card is the unit of work here, not the
    ledger row. Insertion order is file order, so the first row seen for an
    identifier is the earliest and supplies department/kind/summary.
    """
    groups: dict[str, dict[str, Any]] = {}
    for row_hash, card in latest.items():
        if card.get("status") not in {"open", "fix_requested"}:
            continue
        identifier = card.get("card_identifier")
        if not isinstance(identifier, str) or not identifier:
            continue
        group = groups.get(identifier)
        if group is None:
            groups[identifier] = {
                "identifier": identifier,
                "card": card,
                "row_hashes": [row_hash],
                "fix_notes_hashes": set(card.get("_fix_notes_hashes", set())),
            }
            continue
        group["row_hashes"].append(row_hash)
        group["fix_notes_hashes"] |= set(card.get("_fix_notes_hashes", set()))
    return list(groups.values())


def _resume_context(card: dict[str, Any], notes: str) -> dict[str, Any]:
    """Everything a fresh agent needs to pick a FIX up with no chat history.

    Owner decision (Ankit 2026-08-05): the human owns a FIX, and the AI must be
    able to resume it. So the decision row carries the ask, the answer, and where
    it came from, in one row. An agent that reads only this row can act.
    """
    def _text(field: str) -> str:
        value = card.get(field)
        return value if isinstance(value, str) else ""

    context: dict[str, Any] = {
        "notes": notes[:NOTES_LIMIT],
        "owner": OWNER,
        "resume_hint": f"{_text('department')}/{_text('kind')}: {_text('summary')}".strip("/: "),
        "first_raised": _text("first_raised") or _text("ts"),
    }
    for field in ("packet_text", "card_url"):
        if _text(field):
            context[field] = _text(field)
    return context


def _run_optional(argv: list[str], label: str) -> None:
    try:
        result = subprocess.run(argv, check=False)
    except OSError as exc:
        LOGGER.warning("%s could not start: %s", label, exc)
        return
    if result.returncode != 0:
        LOGGER.warning("%s failed with exit code %d", label, result.returncode)


def tick(config: dict[str, Any], *, dry_run: bool = False) -> int:
    latest = _latest_ledger_rows(config["ledger_file"])
    if latest is None:
        return 0
    reader_calls = 0
    reader_failures = 0
    for group in _open_groups(latest):
        identifier = group["identifier"]
        card = group["card"]
        row_hashes = group["row_hashes"]
        reader_calls += 1
        comments = _run_reader(
            _render(config["reader"], {"issue": identifier})
        )
        if comments is None:
            reader_failures += 1
            continue
        found = _decision(comments)
        if found is None:
            continue
        decision, first_line, notes = found
        notes_hash = hashlib.sha256(notes.encode("utf-8")).hexdigest()
        if decision == "fix" and notes_hash in group["fix_notes_hashes"]:
            continue
        if dry_run:
            LOGGER.warning(
                "dry-run would record %s for %s (%d ledger row(s)) from %r",
                decision,
                identifier,
                len(row_hashes),
                first_line,
            )
            continue
        department = card.get("department")
        kind = card.get("kind")
        decision_row = {
            "ts": _now(),
            "card_identifier": identifier,
            # row_hash stays the earliest row for compatibility; row_hashes names
            # every ledger row this one human reply settles.
            "row_hash": row_hashes[0],
            "row_hashes": row_hashes,
            "department": department if isinstance(department, str) else "",
            "kind": kind if isinstance(kind, str) else "",
            "decision": decision,
            "source": "linear-comment",
            "first_line": first_line,
        }
        packet_id = card.get("packet_id")
        if packet_id is not None:
            decision_row["packet_id"] = packet_id
        if decision == "fix":
            decision_row.update(_resume_context(card, notes))
        try:
            _append_jsonl(config["decisions_file"], decision_row)
            for row_hash in row_hashes:
                _append_jsonl(
                    config["ledger_file"],
                    {
                        "ts": _now(),
                        "row_hash": row_hash,
                        "department": decision_row["department"],
                        "kind": decision_row["kind"],
                        "card_identifier": identifier,
                        "status": (
                            "fix_requested" if decision == "fix" else f"decided:{decision}"
                        ),
                        **(
                            {"packet_id": packet_id}
                            if packet_id is not None
                            else {}
                        ),
                        **({"notes_hash": notes_hash} if decision == "fix" else {}),
                    },
                )
        except OSError as exc:
            LOGGER.error("decision files could not be appended: %s", exc)
            continue
        if decision == "fix":
            values = {
                "issue": identifier,
                "body": (
                    f"AGENT UPDATE: your reply is recorded and this card is owned by "
                    f"{OWNER}. It stays open in {NEEDS_INPUT_STATE} and keeps "
                    "re-escalating until it is resolved. Any agent can resume it "
                    "from the recorded decision row; nothing is queued silently."
                ),
                "state": NEEDS_INPUT_STATE,
            }
        else:
            values = {
                "issue": identifier,
                "body": (
                    f"AGENT DONE: decision recorded ({decision}). "
                    "This card's loop is closed."
                ),
                "state": DONE_STATE,
            }
        if config["ack"]:
            _run_optional(_render(config["ack"], values), "ack sender")
        # The closer is a state mover, so a FIX uses it too: it parks the card in
        # NEEDS_INPUT_STATE under the owner rather than closing it.
        if config["close_enabled"]:
            _run_optional(_render(config["closer"], values), "card state mover")
    if reader_calls and reader_failures == reader_calls:
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Listen for human card decisions")
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true", help="run one listener tick")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        config = load_config(args.config)
        return tick(config, dry_run=args.dry_run)
    except ConfigError as exc:
        LOGGER.error("invalid config: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
