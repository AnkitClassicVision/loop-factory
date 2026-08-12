#!/usr/bin/env python3
"""U12 — watch the absence alarm fire, and watch it stay quiet when it should.

"A check nobody has watched fail is not a check" applies hardest to an alarm.
An alarm that has only ever been seen quiet is indistinguishable from an alarm
that is broken, and this particular alarm exists to catch the failure where
everything looks fine.

Five states, each driven through the real sensor:

  alarm    zero drafts while eligible candidates existed   -> the machinery died
  drought  zero drafts and nobody was emailable            -> supply, not machinery
  unknown  a day's feeder report is missing                -> blind, never "ok"
  ok       a draft was created in the window
  alarm    the draft ledger is corrupt                     -> cannot count, so not ok

The separation of `alarm` from `drought` is the point of the unit. Today the
podcast funnel is in a real drought: 13 of 14 inbox records carry no email. An
alarm that cannot tell a drought from a dead loop would either cry wolf every
day until it is ignored, or be tuned down until it never fires at all.

Exit 0 PASS, 1 FAIL.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path,
                        default=Path(__file__).resolve().parents[3])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    sensor_dir = args.repo / "departments" / "podcast" / "runtime"
    sys.path.insert(0, str(sensor_dir))
    import importlib
    sensor = importlib.import_module("outreach_absence_sensor")
    importlib.reload(sensor)

    from datetime import date
    today = date(2026, 8, 10)
    days = ["20260810", "20260809", "20260808"]
    failures: list[str] = []

    def receipts(name: str) -> Path:
        path = args.out / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def seed(path: Path, selected: int | None, ledger: str) -> None:
        for tag in days:
            report = path / f"guest-candidates-{tag}.reasons.json"
            if selected is None:
                report.unlink(missing_ok=True)
            else:
                report.write_text(json.dumps(
                    {"schema": "guest-candidate-feed-report/v1",
                     "considered": 14, "selected": selected}), encoding="utf-8")
        (path / "guest-outreach-ledger.json").write_text(ledger, encoding="utf-8")

    def expect(name: str, selected: int | None, ledger: str, wanted: str,
               tweak=None) -> None:
        path = receipts(name)
        seed(path, selected, ledger)
        if tweak:
            tweak(path)
        result = sensor.evaluate(args.repo, today=today, window_days=3,
                                 receipts_dir=path)
        if result["status"] != wanted:
            failures.append(
                f"{name}: expected {wanted!r}, got {result['status']!r} — {result['detail']}")

    expect("alarm-machinery", 1, "[]", "alarm")
    expect("drought-supply", 0, "[]", "drought")
    expect("unknown-blind", 0, "[]", "unknown",
           tweak=lambda p: (p / "guest-candidates-20260809.reasons.json").unlink())
    expect("ok-drafted", 1,
           json.dumps([{"ordinal": 1, "created_at": "2026-08-10T14:00:00+00:00",
                        "draft_id": "d1", "hold_for_human": True}]), "ok")
    expect("alarm-corrupt-ledger", 1, "{ not json", "alarm")

    # The one that would quietly undo the unit: a sensor that treats a missing
    # feeder report as "nothing to report" reports ok on a dead loop.
    path = receipts("never-ok-when-blind")
    seed(path, None, "[]")
    blind = sensor.evaluate(args.repo, today=today, window_days=3, receipts_dir=path)
    if blind["status"] == "ok":
        failures.append(
            "never-ok-when-blind: the sensor reported OK with zero drafts and no "
            "evidence at all. An alarm that switches itself off when its inputs "
            "vanish is worse than no alarm.")

    if failures:
        print("u12_absence_alarm_check: FAIL")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("u12_absence_alarm_check: PASS — the alarm fires on a dead loop, stays "
          "quiet as a drought when nobody is emailable, refuses to say ok while "
          "blind or while the ledger is unreadable, and clears once a draft exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
