"""SG-SENSE N3 — compare independent observations against the charter.

Thresholds come FROM THE CHARTER, never from code constants (factory law,
enforced via factory/charter_loader.py). Emits derived anti-gaming signals:
cap_near (posts/week vs weekly_touch_ceiling & pace_ceiling_near_frac),
faux_work_signal (weekly touches below faux_work_touch_floor),
delivery_verified_gap (platform_verified_delivery_pct target), budget_near
(budget_near_frac vs weekly ceilings), and gaming_signal (self-reported
metrics contradicting the independent Zernio rows) — each carries its
evidence rows inline (charter C6, C9, C16).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.charter_loader import CharterError, load_charter, thresholds  # noqa: E402

logger = logging.getLogger("compare_charter")

DEFAULT_STATE_DIR = REPO_ROOT / "departments" / "social" / "state"
DEFAULT_CHARTER_PATH = REPO_ROOT / "departments" / "social" / "charter.yaml"

GAMING_TOLERANCE = 0.15
BUDGET_METRIC_TO_CEILING_KEY = {
    "model_calls_used": "model_calls",
    "worker_minutes_used": "worker_minutes",
    "dollars_used": "dollars",
}


class ObservationError(RuntimeError):
    """Raised when the observations evidence is missing, unreadable, or malformed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_observations(path: str | Path) -> list[dict[str, Any]]:
    """Load jsonl observation rows. An empty file is a legitimate zero-signal
    (a working feed with nothing to report); a missing/unreadable/malformed
    file is an outage and must be reported as missing, never silently zeroed."""
    obs_path = Path(path)
    if not obs_path.exists():
        raise ObservationError(f"observations evidence is missing: {obs_path}")
    try:
        lines = obs_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ObservationError(f"observations evidence is unreadable: {obs_path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ObservationError(
                f"observations evidence is malformed at {obs_path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise ObservationError(
                f"observations evidence is malformed at {obs_path}:{line_number}: row is not an object"
            )
        if value.get("status") == "missing":
            source = value.get("source") or "unknown"
            raise ObservationError(
                f"required observation source is missing at {obs_path}:{line_number}: {source}"
            )
        if not isinstance(value.get("source"), str) or not value["source"]:
            raise ObservationError(
                f"observations evidence is malformed at {obs_path}:{line_number}: missing source"
            )
        metric = value.get("metric")
        if not isinstance(metric, str) or not metric:
            raise ObservationError(
                f"observations evidence is malformed at {obs_path}:{line_number}: missing metric"
            )
        if "value" not in value:
            raise ObservationError(
                f"observations evidence is malformed at {obs_path}:{line_number}: missing value"
            )
        rows.append(value)
    return rows


def load_self_reported(path: str | Path | None) -> list[dict[str, Any]]:
    """Optional self-reported metrics used only for the gaming-signal cross-check.
    Absent/unreadable input simply yields no gaming evidence — it never blocks
    the independently-sourced comparison."""
    if not path:
        return []
    try:
        return load_observations(path)
    except ObservationError as exc:
        logger.warning("self-reported evidence unavailable, gaming check skipped: %s", exc)
        return []


def outcome_targets(charter: dict[str, Any]) -> dict[str, Any]:
    setpoints = charter.get("setpoints") or {}
    targets: dict[str, Any] = {}
    operational = setpoints.get("operational") or {}
    if operational.get("metric"):
        targets[operational["metric"]] = operational.get("target")
    outcome = setpoints.get("outcome") or {}
    if outcome.get("metric"):
        targets[outcome["metric"]] = outcome.get("target")
    for row in setpoints.get("outcome_additional") or []:
        if isinstance(row, dict) and row.get("metric"):
            targets[row["metric"]] = row.get("target")
    return targets


def derive_signals(
    observations: list[dict[str, Any]],
    self_reported: list[dict[str, Any]],
    charter: dict[str, Any],
    now: str,
) -> list[dict[str, Any]]:
    th = thresholds(charter)
    targets = outcome_targets(charter)
    signals: list[dict[str, Any]] = []

    zernio_rows = [row for row in observations if row.get("source") == "zernio"]
    post_refs = sorted({row["post_ref"] for row in zernio_rows if row.get("post_ref")})
    posts_count = len(post_refs)

    ceiling = th.get("weekly_touch_ceiling")
    near_frac = th.get("pace_ceiling_near_frac")
    if isinstance(ceiling, (int, float)) and isinstance(near_frac, (int, float)):
        signals.append(
            {
                "metric": "cap_near",
                "value": posts_count >= ceiling * near_frac,
                "source": "compare_charter",
                "ts": now,
                "evidence": {
                    "posts_count": posts_count,
                    "weekly_touch_ceiling": ceiling,
                    "pace_ceiling_near_frac": near_frac,
                    "post_refs": post_refs,
                },
            }
        )

    floor = th.get("faux_work_touch_floor")
    if isinstance(floor, (int, float)):
        signals.append(
            {
                "metric": "faux_work_signal",
                "value": posts_count < floor,
                "source": "compare_charter",
                "ts": now,
                "evidence": {
                    "posts_count": posts_count,
                    "faux_work_touch_floor": floor,
                    "post_refs": post_refs,
                },
            }
        )

    verified_rows = [row for row in zernio_rows if row.get("metric") == "platform_verified"]
    target_pct = targets.get("platform_verified_delivery_pct")
    if verified_rows and isinstance(target_pct, (int, float)):
        verified_count = sum(1 for row in verified_rows if row.get("value") in (1, 1.0, True))
        observed_pct = (verified_count / len(verified_rows)) * 100.0
        signals.append(
            {
                "metric": "delivery_verified_gap",
                "value": observed_pct < target_pct,
                "source": "compare_charter",
                "ts": now,
                "evidence": {
                    "observed_pct": observed_pct,
                    "target_pct": target_pct,
                    "rows": verified_rows,
                },
            }
        )

    budget_ceilings = th.get("budget_ceilings") or {}
    budget_near_frac = th.get("budget_near_frac")
    budget_rows = [row for row in observations if row.get("source") == "budget_ledger"]
    if budget_rows and isinstance(budget_near_frac, (int, float)):
        near = False
        evidence_rows = []
        for row in budget_rows:
            ceiling_key = BUDGET_METRIC_TO_CEILING_KEY.get(str(row.get("metric")))
            ceiling_value = budget_ceilings.get(ceiling_key) if ceiling_key else None
            value = row.get("value")
            if (
                ceiling_key
                and isinstance(ceiling_value, (int, float))
                and ceiling_value > 0
                and isinstance(value, (int, float))
            ):
                evidence_rows.append(row)
                if (value / ceiling_value) >= budget_near_frac:
                    near = True
        if evidence_rows:
            signals.append(
                {
                    "metric": "budget_near",
                    "value": near,
                    "source": "compare_charter",
                    "ts": now,
                    "evidence": {
                        "budget_ceilings": budget_ceilings,
                        "budget_near_frac": budget_near_frac,
                        "rows": evidence_rows,
                    },
                }
            )

    if self_reported:
        by_key = {
            (row.get("post_ref"), row.get("surface"), row.get("metric")): row for row in zernio_rows
        }
        mismatches = []
        for reported in self_reported:
            key = (reported.get("post_ref"), reported.get("surface"), reported.get("metric"))
            independent = by_key.get(key)
            if independent is None:
                continue
            reported_value, independent_value = reported.get("value"), independent.get("value")
            if isinstance(reported_value, (int, float)) and isinstance(independent_value, (int, float)):
                denominator = max(abs(independent_value), 1e-9)
                if abs(reported_value - independent_value) / denominator > GAMING_TOLERANCE:
                    mismatches.append({"self_reported": reported, "independent": independent})
        signals.append(
            {
                "metric": "gaming_signal",
                "value": bool(mismatches),
                "source": "compare_charter",
                "ts": now,
                "evidence": {"tolerance": GAMING_TOLERANCE, "mismatches": mismatches},
            }
        )

    return signals


def write_missing(out_path: str | Path, reason: str) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"status": "missing", "reason": reason, "source": "compare_charter", "ts": _now()}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def write_signals(out_path: str | Path, signals: list[dict[str, Any]]) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in signals:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Compare SG-SENSE observations against the charter")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--out", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--self-reported", default=None)
    parser.add_argument("--charter", default=str(DEFAULT_CHARTER_PATH))
    args = parser.parse_args()

    try:
        charter = load_charter(args.charter, expect_department="social")
    except CharterError as exc:
        logger.error("charter unavailable: %s", exc)
        write_missing(args.out, str(exc))
        raise SystemExit(3)

    try:
        observations = load_observations(args.observations)
    except ObservationError as exc:
        logger.error("observations unavailable: %s", exc)
        write_missing(args.out, str(exc))
        raise SystemExit(3)

    self_reported = load_self_reported(args.self_reported)
    signals = derive_signals(observations, self_reported, charter, _now())
    write_signals(args.out, signals)
    logger.info("derived %d signals from %d observation rows", len(signals), len(observations))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
