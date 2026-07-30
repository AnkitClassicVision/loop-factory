"""Append validated proposal cards to the human approval queue, once."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from factory.charter_loader import load_charter


LOGGER = logging.getLogger(__name__)
VALID_CLASSES = frozenset({"process_change", "prompt_update", "other"})
VALID_KINDS = frozenset({"approve", "skip", "fix"})
DEFAULT_CHARTER = Path(__file__).resolve().parents[1] / "charter.yaml"
PROTECTED_NAME_PARTS = ("charter", "runbook", "graph")


def _write_json(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_card(card: Any, valid_evidence: frozenset[str]) -> dict[str, Any]:
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
        isinstance(item, str) and item in valid_evidence for item in evidence
    ):
        raise ValueError("proposal card evidence is absent from the evidence pack")
    return {
        "question": question,
        "kind": kind,
        "class": proposal_class,
        "evidence": evidence,
    }


def _content_hash(card: dict[str, Any]) -> str:
    canonical = json.dumps(card, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _valid_evidence_ids(evidence_pack: Any) -> frozenset[str]:
    if not isinstance(evidence_pack, dict) or evidence_pack.get("sanitized") is not True:
        raise ValueError("evidence pack must be a sanitized SG-LEARN evidence pack")
    rows = evidence_pack.get("rows")
    if not isinstance(rows, list):
        raise ValueError("evidence pack rows are missing")
    return frozenset(
        row["row_id"]
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("row_id"), str)
    )


def _ttl_hours(charter_path: str | Path) -> int | float:
    charter = load_charter(charter_path, expect_department="social")
    ttl = (charter.get("escalation") or {}).get("no_reply_ttl_hours")
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0:
        raise ValueError("charter escalation.no_reply_ttl_hours is missing or invalid")
    return ttl


@contextmanager
def _queue_lock(state_path: Path) -> Iterator[None]:
    lock_path = state_path / ".approval_queue.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _safe_out_path(state_dir: str | Path, out: str | Path) -> Path:
    state_path = Path(state_dir).resolve()
    target = Path(out).resolve()
    if target != state_path and state_path not in target.parents:
        raise ValueError("receipt path must remain inside state-dir")
    protected_targets = {
        (state_path / "approval_queue.jsonl").resolve(),
        (state_path / ".approval_queue.lock").resolve(),
    }
    if target in protected_targets:
        raise ValueError("receipt path may not target the approval queue or its lock")
    if any(part in target.name.lower() for part in PROTECTED_NAME_PARTS):
        raise ValueError("receipt path may not target a governance file name")
    return target


def append_cards(
    cards: list[dict[str, Any]],
    state_dir: str | Path,
    *,
    evidence_pack: dict[str, Any],
    charter_path: str | Path = DEFAULT_CHARTER,
    now: str | None = None,
) -> dict[str, Any]:
    """Append valid cards idempotently. This function never applies a proposal."""
    state_path = Path(state_dir)
    queue = state_path / "approval_queue.jsonl"
    state_path.mkdir(parents=True, exist_ok=True)
    valid_evidence = _valid_evidence_ids(evidence_pack)
    ttl_hours = _ttl_hours(charter_path)
    with _queue_lock(state_path):
        existing_hashes: set[str] = set()
        if queue.exists():
            for line_number, raw in enumerate(queue.read_text(encoding="utf-8").splitlines(), 1):
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"malformed approval queue line {line_number}: {exc}"
                    ) from exc
                if isinstance(row, dict) and isinstance(row.get("content_hash"), str):
                    existing_hashes.add(row["content_hash"])

        timestamp = now or datetime.now(timezone.utc).isoformat()
        entries: list[dict[str, Any]] = []
        for raw_card in cards:
            card = _validate_card(raw_card, valid_evidence)
            digest = _content_hash(card)
            if digest in existing_hashes:
                continue
            entries.append(
                {
                    **card,
                    "ts": timestamp,
                    "ttl_hours": ttl_hours,
                    "status": "pending",
                    "content_hash": digest,
                }
            )
            existing_hashes.add(digest)
        if entries:
            with queue.open("a", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()
    return {"queued": len(entries), "duplicates": len(cards) - len(entries)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue SG-LEARN proposal cards")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--cards", required=True)
    parser.add_argument("--evidence-pack", required=True)
    parser.add_argument("--charter", default=str(DEFAULT_CHARTER))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        out_path = _safe_out_path(args.state_dir, args.out)
        cards = json.loads(Path(args.cards).read_text(encoding="utf-8"))
        evidence_pack = json.loads(Path(args.evidence_pack).read_text(encoding="utf-8"))
        if not isinstance(cards, list):
            raise ValueError("cards input must be a JSON list")
        result = append_cards(
            cards,
            args.state_dir,
            evidence_pack=evidence_pack,
            charter_path=args.charter,
        )
    except FileNotFoundError as exc:
        LOGGER.error("%s", exc)
        if "out_path" in locals():
            _write_json(out_path, {"status": "missing", "reason": str(exc)})
        return 3
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        LOGGER.error("%s", exc)
        if "out_path" in locals():
            _write_json(out_path, {"status": "blocked", "reason": str(exc)})
        return 2
    _write_json(out_path, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
