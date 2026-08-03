"""N1: sense timer liveness and artifact freshness for the outreach estate."""
from __future__ import annotations

import argparse
import glob
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from departments.outreach.runtime.common import atomic_json, emit

LOGGER = logging.getLogger(__name__)


def systemd_user_probe(unit: str) -> dict[str, str]:
    result = subprocess.run(["systemctl", "--user", "show", unit, "-p",
                             "ActiveState,SubState,Result,ExecMainStatus"],
                            capture_output=True, text=True, timeout=10, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"systemctl exited {result.returncode}")
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def run(root: Path, estate_path: Path, now: datetime | None = None,
        probe=systemd_user_probe) -> dict:
    estate = json.loads(estate_path.read_text(encoding="utf-8"))
    now = now or datetime.now(timezone.utc)
    observations = []
    entries = [("timer", row) for row in estate.get("systemd_user_timers", [])]
    entries += [("sales_lane", row) for row in estate.get("sales_lanes", [])]
    for kind, item in entries:
        reasons, metrics = [], {}
        name = item["name"]
        if kind == "timer":
            try:
                state = probe(f"{name}.timer")
                metrics["timer_state"] = state
                if state.get("ActiveState") != "active" or state.get("Result", "success") not in ("", "success") or state.get("ExecMainStatus", "0") != "0":
                    reasons.append("timer_unhealthy")
            except Exception as exc:
                reasons.append(f"timer_probe_unknown:{type(exc).__name__}")
        evidence = item.get("evidence")
        pattern = item.get("log_glob") or item.get("receipt_glob") or item.get("ledger_path") or item.get("evidence_path")
        if evidence != "timer_only":
            matches = [Path(p) for p in glob.glob(str(pattern or "")) if Path(p).is_file()]
            if not matches:
                reasons.append("evidence_absent")
            else:
                newest = max(matches, key=lambda p: p.stat().st_mtime)
                age = max(0.0, (now.timestamp() - newest.stat().st_mtime) / 60)
                metrics.update({"evidence_path": str(newest), "evidence_age_minutes": round(age, 3)})
                threshold = item.get("stale_after_minutes")
                if threshold is not None and age > float(threshold):
                    reasons.append("evidence_stale")
        observations.append({"subject": name, "kind": kind,
                             "status": "unknown" if any("unknown" in r or "absent" in r for r in reasons) else ("fail" if reasons else "ok"),
                             "reasons": reasons, "metrics": metrics})
    output = {"schema": "lane-sense/v1", "ts": now.isoformat(), "lanes_sensed": len(observations), "observations": observations}
    target = root / "departments/outreach/state/lane_sense.json"
    atomic_json(target, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--estate")
    parser.add_argument("--systemd-fixture")
    args = parser.parse_args()
    root = Path(args.root)
    estate = Path(args.estate) if args.estate else root / "departments/outreach/runtime/estate.json"
    probe = systemd_user_probe
    if args.systemd_fixture:
        fixture = json.loads(Path(args.systemd_fixture).read_text(encoding="utf-8"))
        probe = lambda unit: fixture[unit]
    started = time.perf_counter()
    target = root / "departments/outreach/state/lane_sense.json"
    try:
        run(root, estate, probe=probe)
        emit(target.parent, "N1", started, target)
    except Exception as exc:
        LOGGER.exception("lane sensing failed")
        try: emit(target.parent, "N1", started, target, [type(exc).__name__])
        except Exception: LOGGER.exception("run record append failed")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
