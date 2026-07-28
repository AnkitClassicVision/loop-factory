"""Append validated proposal cards to the human approval queue, once."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)
VALID_CLASSES = frozenset({"process_change", "prompt_update", "other"})
VALID_KINDS = frozenset({"approve", "skip", "fix"})


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_card(card: Any) -> dict[str, Any]:
    if not isinstance(card, dict):
        raise ValueError("proposal card must be an object")
    question = card.get("question")
    kind = card.get("kind")
    proposal_class = card.get("class")
    evidence = card.get("evidence")
    if proposal_class not in VALID_CLASSES:
        raise ValueError("proposal card class is missing or invalid; self-modification refused")
    if kind not in VALID_KINDS:
        raise ValueError("proposal card kind must be approve, skip, or fix")
    if not isinstance(question, str) or question.count("?") != 1 or not question.endswith("?"):
        raise ValueError("proposal card must contain exactly one question")
    if kind not in question.lower():
        raise ValueError("proposal question must be phrased as its approve/skip/fix kind")
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item for item in evidence
    ):
        raise ValueError("proposal card must carry evidence row ids")
    return {
        "question": question,
        "kind": kind,
        "class": proposal_class,
        "evidence": evidence,
    }


def _content_hash(card: dict[str, Any]) -> str:
    canonical = json.dumps(card, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def append_cards(
    cards: list[dict[str, Any]],
    state_dir: str | Path,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Append valid cards idempotently. This function never applies a proposal."""
    state_path = Path(state_dir)
    queue = state_path / "approval_queue.jsonl"
    state_path.mkdir(parents=True, exist_ok=True)
    existing_hashes: set[str] = set()
    if queue.exists():
        for line_number, raw in enumerate(queue.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed approval queue line {line_number}: {exc}") from exc
            if isinstance(row, dict) and isinstance(row.get("content_hash"), str):
                existing_hashes.add(row["content_hash"])

    timestamp = now or datetime.now(timezone.utc).isoformat()
    entries: list[dict[str, Any]] = []
    for raw_card in cards:
        card = _validate_card(raw_card)
        digest = _content_hash(card)
        if digest in existing_hashes:
            continue
        entries.append(
            {
                **card,
                "ts": timestamp,
                "ttl_hours": 24,
                "status": "pending",
                "content_hash": digest,
            }
        )
        existing_hashes.add(digest)
    if entries:
        with queue.open("a", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return {"queued": len(entries), "duplicates": len(cards) - len(entries)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue SG-LEARN proposal cards")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--cards", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        cards = json.loads(Path(args.cards).read_text(encoding="utf-8"))
        if not isinstance(cards, list):
            raise ValueError("cards input must be a JSON list")
        result = append_cards(cards, args.state_dir)
    except FileNotFoundError as exc:
        LOGGER.error("%s", exc)
        _write_json(args.out, {"status": "missing", "reason": str(exc)})
        return 3
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("%s", exc)
        _write_json(args.out, {"status": "blocked", "reason": str(exc)})
        return 2
    _write_json(args.out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
