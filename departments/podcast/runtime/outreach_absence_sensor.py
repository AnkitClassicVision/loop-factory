"""U12 — the dead man's switch for guest outreach.

The failure this exists to catch: the loop runs every day, exits 0 every day, and
creates nothing. A zero-output week and a quiet week look identical from the
outside, so nobody asks. Every other unit in this contract makes a specific
failure loud; this one makes ABSENCE loud, which is the only failure that cannot
announce itself.

Why it is a new sensor rather than a threshold on something that already exists,
measured 2026-08-10:

- `factory/estate_deadman.py` tracks epoch, findings, escalations and staleness.
  That is LIVENESS. A loop that runs faithfully and drafts nothing is perfectly
  alive, so the deadman stays quiet.
- `funnel_floor_sensor` measures funnel STATE — warm threads, live replies,
  recordings booked. Those floors cannot separate "the machinery stopped" from
  "the pool is empty", and right now the pool IS nearly empty, so a floor breach
  would be attributed to supply and the broken machinery would hide behind it.

The discriminating pair is (drafts created, eligible candidates available). Both
became measurable only once the feeder started writing its drop accounting; the
question "were there people to write to?" had no artifact before that.

Three outcomes, deliberately distinct, because collapsing them is how this goes
quiet again:

  ok       drafts were created in the window
  alarm    zero drafts for N days WHILE eligible candidates existed  -> machinery
  drought  zero drafts for N days AND zero eligible candidates       -> supply
  unknown  the evidence for a day is missing

`unknown` is never folded into `ok`. The department's owner amendment of
2026-08-05 already settled that for the funnel floors — a missing source makes a
gauge blind, and a blind gauge is not a passing gauge. The same rule applies
here, and it matters more: an absence alarm that reads missing evidence as
"nothing to report" is an alarm that switches itself off exactly when the thing
it watches has broken.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_PIPELINE_REPO = Path("/mnt/d_drive/repos/podcast")
DEFAULT_WINDOW_DAYS = 3


def _parse_day(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).date()
    except (ValueError, OverflowError):
        pass
    try:
        return date.fromisoformat(text)
    except (ValueError, OverflowError):
        return None


def _drafts_by_day(ledger_path: Path) -> tuple[dict[date, int], str | None]:
    """Count created drafts per day from the producer's own ledger."""
    try:
        rows = json.loads(ledger_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # The producer has never created a draft. That is a real, countable zero
        # and NOT missing evidence: the ledger is written on first success.
        return {}, None
    except (OSError, ValueError) as exc:
        return {}, f"draft ledger unreadable: {exc}"
    if not isinstance(rows, list):
        return {}, f"draft ledger is a {type(rows).__name__}, expected a list"
    counts: dict[date, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _parse_day(row.get("created_at"))
        if day is not None:
            counts[day] = counts.get(day, 0) + 1
    return counts, None


def _eligible_on(receipts_dir: Path, day: date) -> int | None:
    """How many candidates the feeder judged emailable that day, or None if unknown."""
    report = receipts_dir / f"guest-candidates-{day.strftime('%Y%m%d')}.reasons.json"
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    selected = payload.get("selected")
    return selected if isinstance(selected, int) and not isinstance(selected, bool) else None


def evaluate(
    pipeline_repo: Path,
    *,
    today: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
    receipts_dir: Path | None = None,
) -> dict[str, Any]:
    receipts = Path(receipts_dir) if receipts_dir else pipeline_repo / "episodes" / "_loop_receipts"
    drafts, ledger_error = _drafts_by_day(receipts / "guest-outreach-ledger.json")

    days: list[dict[str, Any]] = []
    for offset in range(window_days):
        day = today - timedelta(days=offset)
        eligible = _eligible_on(receipts, day)
        days.append({
            "day": day.isoformat(),
            "drafts_created": drafts.get(day, 0),
            "eligible_candidates": eligible,
            "evidence": "present" if eligible is not None else "missing",
        })

    drafted_total = sum(entry["drafts_created"] for entry in days)
    missing = [entry for entry in days if entry["eligible_candidates"] is None]
    eligible_seen = [
        entry for entry in days
        if isinstance(entry["eligible_candidates"], int) and entry["eligible_candidates"] > 0
    ]

    if ledger_error:
        status = "alarm"
        detail = (
            f"{ledger_error}. The count of produced drafts cannot be established, so "
            "the absence alarm cannot do its job and refuses to report ok."
        )
    elif drafted_total > 0:
        status = "ok"
        detail = f"{drafted_total} draft(s) created in the last {window_days} day(s)."
    elif missing:
        status = "unknown"
        detail = (
            f"zero drafts in {window_days} day(s), and {len(missing)} of those days has "
            "no feeder report, so whether anyone was emailable is UNMEASURED. A blind "
            "gauge is not a passing gauge."
        )
    elif eligible_seen:
        status = "alarm"
        detail = (
            f"zero drafts for {window_days} consecutive day(s) while "
            f"{max(e['eligible_candidates'] for e in eligible_seen)} eligible candidate(s) "
            "were available. The machinery is not producing; this is not a quiet week."
        )
    else:
        status = "drought"
        detail = (
            f"zero drafts for {window_days} day(s) and zero eligible candidates on every "
            "one of them. The machinery is fine and the funnel is empty AT THE SOURCE. "
            "This is a supply problem, not a loop problem."
        )

    return {
        "schema": "podcast.outreach.absence/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": today.isoformat(),
        "window_days": window_days,
        "status": status,
        "detail": detail,
        "drafts_in_window": drafted_total,
        "days": days,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline-repo", type=Path, default=DEFAULT_PIPELINE_REPO)
    parser.add_argument("--receipts-dir", type=Path)
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--today", type=date.fromisoformat,
                        default=datetime.now(timezone.utc).date())
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.window_days < 1:
        parser.error("--window-days must be at least 1")

    observation = evaluate(
        args.pipeline_repo, today=args.today,
        window_days=args.window_days, receipts_dir=args.receipts_dir,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(observation, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(observation, indent=2))
    # Exit 0 ok, 2 needs a human (alarm, drought or unknown). Never 1 for a
    # measured state: 1 is reserved for the sensor itself failing.
    return 0 if observation["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
