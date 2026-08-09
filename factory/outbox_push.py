"""Push new human-outbox JSONL rows through configured sender commands.

Configuration YAML::

    cursor_file: path/to/cursor.json
    ledger_file: path/to/card_ledger.jsonl  # required when senders.buzz is configured
    watches:
      - path: path/to/decisions_outbox.jsonl
        department: department-label
        kind: escalation  # or approval
    senders:
      ping:
        - sender-command
        - --text
        - "{text}"
      card:
        - card-command
        - --title
        - "{title}"
        - --body
        - "{body}"
        # Optional once approved in live config:
        # - --dedupe-key
        # - "{dedupe_key}"
      buzz:
        - buzz-command
        - --card
        - "{card}"
      card_enabled: true

Sender values are argv templates, never shell commands. Ping templates may use
``{text}``, ``{department}``, and ``{kind}``; card templates may use
``{title}``, ``{body}``, ``{dedupe_key}``, ``{department}``, and ``{kind}``; optional buzz
templates may use ``{card}``, ``{text}``, ``{action_mode}``, ``{department}``,
and ``{kind}``.

Exit codes: 2 for invalid configuration, 3 for a sender delivery failure,
and 4 when every configured watch path is missing during a non-dry-run tick.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


LOGGER = logging.getLogger(__name__)
KINDS = frozenset({"escalation", "approval"})
HASH_LIMIT = 200
TEXT_LIMIT = 800


class ConfigError(ValueError):
    """Raised when fail-closed configuration validation refuses a run."""


def _argv(value: Any, name: str, *, required: bool) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ConfigError(f"senders.{name} must be a non-empty argv list")
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load config: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config must be a YAML mapping")
    cursor_file = raw.get("cursor_file")
    watches = raw.get("watches")
    senders = raw.get("senders")
    if not isinstance(cursor_file, str) or not cursor_file:
        raise ConfigError("cursor_file must be a non-empty path")
    if not isinstance(watches, list):
        raise ConfigError("watches must be a list")
    if not isinstance(senders, dict):
        raise ConfigError("senders must be a mapping")
    card_enabled = senders.get("card_enabled")
    if not isinstance(card_enabled, bool):
        raise ConfigError("senders.card_enabled must be true or false")
    ping = _argv(senders.get("ping"), "ping", required=True)
    card = _argv(senders.get("card"), "card", required=card_enabled)
    buzz = _argv(senders.get("buzz"), "buzz", required=False)
    ledger_file = raw.get("ledger_file")
    if ledger_file is not None and (
        not isinstance(ledger_file, str) or not ledger_file
    ):
        raise ConfigError("ledger_file must be a non-empty path when configured")
    if buzz and ledger_file is None:
        raise ConfigError("ledger_file is required when senders.buzz is configured")
    if card_enabled and buzz:
        if "{dedupe_key}" not in "\0".join(card):
            raise ConfigError(
                "senders.card must contain {dedupe_key} when senders.buzz is configured"
            )
        if "{action_mode}" not in "\0".join(buzz):
            raise ConfigError(
                "senders.buzz must contain {action_mode} when bound to a card"
            )
    clean_watches = []
    for index, watch in enumerate(watches):
        if not isinstance(watch, dict):
            raise ConfigError(f"watches[{index}] must be a mapping")
        watch_path = watch.get("path")
        department = watch.get("department")
        kind = watch.get("kind")
        if not isinstance(watch_path, str) or not watch_path:
            raise ConfigError(f"watches[{index}].path must be a non-empty path")
        if not isinstance(department, str) or not department:
            raise ConfigError(f"watches[{index}].department must be a non-empty label")
        if kind not in KINDS:
            raise ConfigError(f"watches[{index}].kind must be escalation or approval")
        clean_watches.append(
            {"path": watch_path, "department": department, "kind": kind}
        )
    return {
        "cursor_file": cursor_file,
        "watches": clean_watches,
        "ping": ping,
        "card": card,
        "buzz": buzz,
        "card_enabled": card_enabled,
        "ledger_file": ledger_file,
    }


def _load_cursor(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cursor file is unreadable or invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError("cursor file must contain a JSON object")
    return value


def _save_cursor(path: Path, cursor: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(cursor, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _state(cursor: dict[str, Any], watch_path: str) -> dict[str, Any]:
    value = cursor.get(watch_path)
    if not isinstance(value, dict):
        value = {}
    offset = value.get("offset_lines", 0)
    hashes = value.get("last_hashes", [])
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ConfigError(f"cursor offset for {watch_path!r} is invalid")
    if not isinstance(hashes, list) or not all(isinstance(item, str) for item in hashes):
        raise ConfigError(f"cursor hashes for {watch_path!r} are invalid")
    value = {"offset_lines": offset, "last_hashes": hashes[-HASH_LIMIT:]}
    cursor[watch_path] = value
    return value


def _row_summary(row: dict[str, Any]) -> str:
    """The row's own one-line summary (eli5/issue/question), for titles."""
    return next(
        (
            row.get(field)
            for field in ("eli5", "issue", "question")
            if isinstance(row.get(field), str) and row.get(field)
        ),
        "",
    )


def _sanitized_text(row: dict[str, Any], department: str, kind: str) -> str:
    message = next(
        (
            row.get(field)
            for field in ("eli5", "issue", "question")
            if isinstance(row.get(field), str) and row.get(field)
        ),
        "",
    )
    ts = row.get("ts") if isinstance(row.get("ts"), str) else ""
    parts = [f"Department: {department}", f"Kind: {kind}", message]
    if ts:
        parts.append(f"Time: {ts}")
    return "\n".join(parts)[:TEXT_LIMIT]


def _render(template: list[str], values: dict[str, str]) -> list[str]:
    return [
        item.replace("{text}", values.get("text", ""))
        .replace("{title}", values.get("title", ""))
        .replace("{body}", values.get("body", ""))
        .replace("{card}", values.get("card", ""))
        .replace("{dedupe_key}", values.get("dedupe_key", ""))
        .replace("{action_mode}", values.get("action_mode", ""))
        .replace("{department}", values["department"])
        .replace("{kind}", values["kind"])
        for item in template
    ]


def _send(argv: list[str]) -> bool:
    try:
        result = subprocess.run(argv, check=False)
    except OSError as exc:
        LOGGER.error("sender could not start: %s", exc)
        return False
    return result.returncode == 0


def _send_captured(argv: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        LOGGER.error("sender could not start: %s", exc)
        return False, ""
    return result.returncode == 0, result.stdout


def _last_json_object(output: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    last: dict[str, Any] | None = None
    index = 0
    while index < len(output):
        start = output.find("{", index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(output, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(value, dict):
            last = value
        index = max(end, start + 1)
    return last


def _card_identifier(card: dict[str, Any] | None) -> str | None:
    identifier = card.get("identifier") if isinstance(card, dict) else None
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    return identifier.strip()


def _append_ledger(
    path: str,
    *,
    digest: str,
    department: str,
    kind: str,
    summary: str,
    card: dict[str, Any] | None,
    packet_text: str = "",
    packet_id: Any = None,
    action_mode: str = "decision",
    fyi_only: bool = False,
) -> bool:
    identifier = _card_identifier(card)
    url = card.get("url") if isinstance(card, dict) else None
    tracked = identifier is not None
    now = datetime.now(timezone.utc).isoformat()
    ledger_row = {
        "ts": now,
        # first_raised is the age the re-escalation cadence measures from, and it
        # is what a resuming agent needs to know how long this has been waiting.
        "first_raised": now,
        "row_hash": digest,
        "department": department,
        "kind": kind,
        "summary": summary,
        # The sanitized ask itself, so a FIX decision row can carry the original
        # question to whoever picks it up. Already capped at TEXT_LIMIT upstream.
        "packet_text": packet_text,
        "card_identifier": identifier if tracked else None,
        "card_url": url if isinstance(url, str) and url else None,
        "status": "open" if tracked else "untracked",
        "action_mode": action_mode,
        "fyi_only": fyi_only,
    }
    if packet_id is not None:
        ledger_row["packet_id"] = packet_id
    ledger_path = Path(path)
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(ledger_row, sort_keys=True) + "\n")
    except OSError as exc:
        LOGGER.warning("card ledger could not be appended: %s", exc)
        return False
    if not tracked:
        LOGGER.warning(
            "card sender output had no usable identifier; ledger row is untracked"
        )
    return tracked


def tick(config: dict[str, Any], *, dry_run: bool = False) -> int:
    cursor_path = Path(config["cursor_file"])
    cursor = _load_cursor(cursor_path)
    any_ping_failure = False
    card_failures = 0
    changed = False
    missing_watch_paths: list[str] = []

    for watch in config["watches"]:
        watch_path = watch["path"]
        source = Path(watch_path)
        state = _state(cursor, watch_path)
        if not source.exists():
            missing_watch_paths.append(watch_path)
            continue
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            LOGGER.error("watch file could not be read: %s", exc)
            continue
        for line_index in range(state["offset_lines"], len(lines)):
            raw = lines[line_index]
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                LOGGER.error("%s line %d is invalid JSON: %s", source, line_index + 1, exc)
                break
            if not isinstance(row, dict):
                LOGGER.error("%s line %d is not a JSON object", source, line_index + 1)
                break
            digest = hashlib.sha256(f"{watch_path}{raw}".encode()).hexdigest()
            row_digest = hashlib.sha256(raw.encode()).hexdigest()
            fyi_only = (
                isinstance(row.get("card"), dict)
                and row["card"].get("fyi_only") is True
            )
            action_mode = "fyi" if fyi_only else "decision"
            if digest in state["last_hashes"]:
                state["offset_lines"] = line_index + 1
                changed = True
                continue
            text = _sanitized_text(row, watch["department"], watch["kind"])
            # Card shape per open-engine-card-format-v1: single-line title from the
            # row's own summary line; human-action body LEADS with YOUR MOVE and the
            # exact reply strings (approval grammar itself stays human-only).
            summary_line = " ".join(_row_summary(row).split())[:80] or f"{watch['kind']} row"
            if fyi_only:
                reply_instructions = (
                    "Reply with first line: ACKNOWLEDGE (mark this update seen), "
                    "SNOOZE 24H (pause reminders for 24 hours), or RETIRE "
                    "(stop future reminders for this item)."
                )
            elif watch["kind"] == "escalation":
                reply_instructions = (
                    "Reply with first line: APPROVE (keep or accept this handling "
                    "as-is), or FIX: <change> (request a change or retirement; add "
                    "notes on the lines below)."
                )
            else:
                reply_instructions = (
                    "Reply with first line: APPROVE (confirm/apply), SKIP (dismiss), "
                    "or FIX: <what to change> (add notes on the lines below)."
                )
            values = {
                "text": text,
                "title": (
                    f"[{watch['department']}] fyi: {summary_line}"
                    if fyi_only
                    else f"[{watch['department']}] {watch['kind']}: {summary_line}"
                ),
                "body": (
                    "## YOUR MOVE (10 seconds)\n"
                    f"{summary_line}\n"
                    f"{reply_instructions}\n\n"
                    "## Detail\n" + text
                ),
                "department": watch["department"],
                "kind": watch["kind"],
                "action_mode": action_mode,
                "dedupe_key": (
                    f"loop-factory-fyi:{watch['department']}"
                    if fyi_only
                    else row_digest
                ),
            }
            ping_argv = _render(config["ping"], values)
            card_argv = _render(config["card"], values) if config["card_enabled"] else []
            if dry_run:
                LOGGER.warning("dry-run ping argv: %r", ping_argv)
                if card_argv:
                    LOGGER.warning("dry-run card argv: %r", card_argv)
                continue
            bound_notification = bool(card_argv and config["buzz"])
            if not bound_notification:
                if not _send(ping_argv):
                    any_ping_failure = True
                    LOGGER.error("ping sender failed for %s line %d", source, line_index + 1)
                    # Ping failures stop before cursor mutation, so the row retries in order.
                    break
            if card_argv:
                ledger_file = config.get("ledger_file")
                card_success, card_stdout = _send_captured(card_argv)
                if not card_success:
                    card_failures += 1
                    LOGGER.error(
                        "card sender failed for %s line %d", source, line_index + 1
                    )
                    # Do not consume an undelivered card. The repeated plain ping is the
                    # deliberate loud outage signal until this in-order retry succeeds.
                    break
                else:
                    card = _last_json_object(card_stdout)
                    identifier = _card_identifier(card)
                    if bound_notification and identifier is None:
                        card_failures += 1
                        LOGGER.error(
                            "card sender returned no usable identifier for %s line %d",
                            source,
                            line_index + 1,
                        )
                        break
                    ledger_tracked = False
                    if ledger_file:
                        # Ledger append behavior is unchanged once card delivery succeeds.
                        ledger_tracked = _append_ledger(
                            ledger_file,
                            digest=digest,
                            department=watch["department"],
                            kind=watch["kind"],
                            summary=summary_line,
                            card=card,
                            packet_text=text,
                            packet_id=row.get("packet_id"),
                            action_mode=action_mode,
                            fyi_only=fyi_only,
                        )
                    if ledger_file and identifier is not None and not ledger_tracked:
                        card_failures += 1
                        LOGGER.error(
                            "card ledger persistence failed for %s line %d",
                            source,
                            line_index + 1,
                        )
                        break
                    if bound_notification and not ledger_tracked:
                        card_failures += 1
                        LOGGER.error(
                            "bound notification requires a tracked ledger row for %s line %d",
                            source,
                            line_index + 1,
                        )
                        break
                    if identifier is not None and config["buzz"]:
                        buzz_values = {**values, "card": identifier}
                        buzz_argv = _render(config["buzz"], buzz_values)
                        if not _send(buzz_argv):
                            # Card plus ledger already make this re-armable, so a lost
                            # buzz remains a delay and the row is consumed as before.
                            LOGGER.warning(
                                "buzz sender failed for %s line %d",
                                source,
                                line_index + 1,
                            )
            state["last_hashes"] = (state["last_hashes"] + [digest])[-HASH_LIMIT:]
            state["offset_lines"] = line_index + 1
            changed = True

    if changed and not dry_run:
        _save_cursor(cursor_path, cursor)
    if (
        not dry_run
        and config["watches"]
        and len(missing_watch_paths) == len(config["watches"])
    ):
        LOGGER.error(
            "outbox watch stalled; every configured watch path is missing: %s",
            ", ".join(missing_watch_paths),
        )
        return 4
    return 3 if card_failures or any_ping_failure else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Push new outbox rows to configured senders")
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true", help="run one tick (currently required mode)")
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
