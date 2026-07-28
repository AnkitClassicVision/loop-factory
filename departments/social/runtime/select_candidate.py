"""Deterministically rank eligible SG-REPUBLISH content.

Formula: rank_score = age_days_since_last_resurface_or_publish
                      + 30 * prior_engagement.score.
Higher wins; item_id ascending is the deterministic tie-break.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path


LOG = logging.getLogger(__name__)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _quarantine(state_dir: Path, item_id: str, reasons: list[str]) -> None:
    _write(
        state_dir / "quarantine" / f"{item_id}.json",
        {"item_id": item_id, "reasons": reasons},
    )


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _suppressed_ids(path: Path | None) -> set[str]:
    if path is None:
        return set()
    result = set()
    for row in _rows(path):
        if row.get("suppressed", True) or str(row.get("status", "")).lower() in {
            "deleted", "retracted", "opt_out", "opt-out"
        }:
            value = row.get("item_id")
            if value:
                result.add(str(value))
    return result


def run(
    *, index: Path, suppression: Path | None, cooldown_days: int,
    as_of: datetime, state_dir: Path, out: Path,
) -> int:
    try:
        rows = _rows(index)
        suppressed = _suppressed_ids(suppression)
        eligible = []
        for item in rows:
            if str(item.get("item_id")) in suppressed:
                continue
            last_value = item.get("last_resurfaced_at")
            if last_value:
                age_days = (as_of - _dt(str(last_value))).total_seconds() / 86400
                if age_days < cooldown_days:
                    continue
            else:
                age_days = (as_of - _dt(str(item["published_at"]))).total_seconds() / 86400
            engagement = float(item["prior_engagement"]["score"])
            score = age_days + 30.0 * engagement
            eligible.append((score, str(item["item_id"]), age_days, engagement, item))
        if not eligible:
            reason = "no eligible candidates after cooldown and suppression gates"
            _write(out, {"status": "blocked", "reason": reason})
            _quarantine(state_dir, "selection", [reason])
            return 2
        score, _, age_days, engagement, item = sorted(
            eligible, key=lambda value: (-value[0], value[1])
        )[0]
        candidate = {
            "item": item,
            "rank_score": round(score, 6),
            "rationale": (
                "rank_score=age_days+30*prior_engagement.score; "
                f"age_days={age_days:.6f}; prior_engagement.score={engagement:.6f}"
            ),
        }
        _write(out, candidate)
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        reason = f"candidate source unreadable or invalid: {exc}"
        LOG.error(reason)
        _write(out, {"status": "missing", "reason": reason})
        return 3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--suppression", type=Path)
    parser.add_argument("--cooldown-days", type=int, default=30)
    parser.add_argument("--as-of")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    as_of = _dt(args.as_of) if args.as_of else datetime.now(timezone.utc)
    raise SystemExit(run(
        index=args.index, suppression=args.suppression,
        cooldown_days=args.cooldown_days, as_of=as_of,
        state_dir=args.state_dir, out=args.out,
    ))


if __name__ == "__main__":
    main()
