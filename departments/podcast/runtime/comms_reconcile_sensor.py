"""Reconcile referral communication counts without performing external actions."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory import runrecord


ASKED_KEYS = ("outbound_referral_touch_count", "outbound_touch_count")
REPLIED_KEYS = ("inbound_reply_count", "inbound_replies_count")


def _read_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("input must contain a JSON object")
    return data


def _count(container: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key in container:
            value = container[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value
    return None


def reconcile(tracker: dict[str, Any], ledger: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return deterministic findings for gaps in the asked-to-harvested chain."""
    summary = tracker.get("summary")
    counts = summary if isinstance(summary, dict) else tracker
    asked = _count(counts, ASKED_KEYS)
    replied = _count(counts, REPLIED_KEYS)

    referrals = ledger.get("referrals")
    harvested = len(referrals) if isinstance(referrals, list) else None
    findings: list[dict[str, Any]] = []

    missing = []
    if asked is None:
        missing.append("asked")
    if replied is None:
        missing.append("replied")
    if harvested is None:
        missing.append("harvested")
    if missing:
        findings.append({
            "code": "count_missing",
            "counts": missing,
            "detail": f"Required count is unknown: {', '.join(missing)}.",
        })

    if asked is not None and replied is not None and asked > 0 and replied == 0:
        findings.append({
            "code": "open_loop",
            "chain": "asked->replied",
            "asked": asked,
            "replied": replied,
            "harvested": harvested,
            "severity": "warn",
            "detail": f"{asked} referral touches were asked, but no inbound replies were counted.",
        })
    if replied is not None and harvested is not None and replied > 0 and harvested == 0:
        findings.append({
            "code": "open_loop",
            "chain": "replied->harvested",
            "asked": asked,
            "replied": replied,
            "harvested": harvested,
            "severity": "action",
            "detail": f"{replied} inbound replies were counted, but no referrals were harvested.",
        })
    return {"findings": findings}


def run(tracker_path: Path, ledger_path: Path, sla_hours: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """Read local inputs and return findings; SLA is accepted for CLI compatibility."""
    del sla_hours
    try:
        tracker = _read_object(tracker_path)
        ledger = _read_object(ledger_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"findings": [{
            "code": "input_unreadable",
            "detail": f"Could not read an input file: {type(exc).__name__}.",
        }]}
    return reconcile(tracker, ledger)


def run_stage(
    tracker_path: Path,
    ledger_path: Path,
    state_dir: Path,
    sla_hours: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Reconcile and fail closed unless its inline receipt reaches v2 storage."""
    started = time.perf_counter()
    result = run(tracker_path, ledger_path, sla_hours)
    codes = [str(row.get("code", "unknown")) for row in result["findings"]]
    unreadable = "input_unreadable" in codes
    runrecord.emit_record(
        state_dir,
        department="podcast",
        node="comms_reconcile_sensor",
        status="blocked" if unreadable else "ok",
        release=runrecord.read_release(Path(state_dir).parent),
        trigger={
            "kind": "time",
            "id": "podcast-daily",
            "dedupe_key": (
                f"{datetime.now(timezone.utc).date().isoformat()}-comms_reconcile_sensor"
            ),
        },
        cost={"lane": "flat_subscription", "model_calls": 0},
        duration_ms=int((time.perf_counter() - started) * 1000),
        errors=codes if unreadable else [],
        artifacts=[{
            "kind": "stdout_receipt",
            "node": "comms_reconcile_sensor",
            "finding_codes": codes,
        }],
        external_actions_taken=0,
    )
    return result


def main() -> None:
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracker", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--sla-hours", type=int)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=repo / "departments/podcast/state",
    )
    args = parser.parse_args()
    print(json.dumps(
        run_stage(args.tracker, args.ledger, args.state_dir, args.sla_hours),
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
