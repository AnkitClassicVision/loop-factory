"""Expectation-manifest reconcile sensor: declared expectations vs reality.

Owner decision (Ankit, 2026-08-04): every step declares the artifacts it
expects and by when; gaps from the manifest summon healing. This sensor is
the WATCHDOG side: it runs every manifest in departments/podcast/manifests/
against the estate ground-truth snapshot (state/sources/expectation-snapshot
.json, fetched read-only from the VPS writer) and records one observation per
manifest. The VPS self-healer acts; this sensor verifies independently, so a
dead healer cannot hide — deltas that persist here become incidents through
the normal manager cycle and reach the owner on the Telegram + Linear lane.

Shadow-compliant: read-only over sources, writes only department records.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from factory import expectation_manifest, runrecord

RUNTIME_DIR = Path(__file__).resolve().parent
DEPARTMENT_DIR = RUNTIME_DIR.parent


def _observation(process: str, status: str, evidence: str, detail: str, metrics: dict) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "sensor": "expectation",
        "subject": f"expectation-{process}",
        "status": status,
        "evidence": evidence,
        "detail": detail,
        "metrics": metrics,
    }


def _append(state_dir: Path, observation: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(observation, sort_keys=True) + "\n")


def run(state_dir: Path, sources: Path, manifests_dir: Path) -> tuple[str, list[dict]]:
    snapshot_path = sources / "expectation-snapshot.json"
    observations: list[dict] = []
    manifest_paths = sorted(manifests_dir.glob("*.yaml"))
    if not manifest_paths:
        obs = _observation("none", "unknown", str(manifests_dir),
                          "no expectation manifests found (fail closed)", {})
        _append(state_dir, obs)
        return "fail", [obs]
    try:
        snapshots = expectation_manifest.load_snapshots(snapshot_path)
    except expectation_manifest.ManifestError as exc:
        obs = _observation("snapshot", "unknown", str(snapshot_path),
                          f"snapshot unreadable: {exc}", {})
        _append(state_dir, obs)
        return "fail", [obs]
    worst = "ok"
    receipts_dir = state_dir / "expectation-receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    for path in manifest_paths:
        try:
            manifest = expectation_manifest.load_manifest(path)
            receipt = expectation_manifest.reconcile(manifest, DEPARTMENT_DIR, snapshots)
        except expectation_manifest.ManifestError as exc:
            obs = _observation(path.stem, "unknown", str(path),
                              f"reconcile failed closed: {exc}", {})
            _append(state_dir, obs)
            observations.append(obs)
            worst = "fail"
            continue
        receipt_path = receipts_dir / f"{manifest.process}-latest.json"
        receipt_path.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n",
                                encoding="utf-8")
        deltas = receipt["deltas"]
        status = "alarm" if deltas else "ok"
        if deltas:
            worst = "fail"
        detail = (
            f"{len(deltas)} expectation delta(s)" if deltas
            else f"all expectations met ({receipt['counts']['ok']} ok, "
                 f"{receipt['counts']['authorized_skips']} receipted skips, "
                 f"{receipt['counts']['pending']} pending)"
        )
        obs = _observation(manifest.process, status, str(receipt_path), detail, {
            "counts": receipt["counts"],
            "deltas": [
                {"instance": d["instance"], "step": d["step"], "heal": d["heal"],
                 "age_minutes": d["age_minutes"]}
                for d in deltas
            ],
        })
        _append(state_dir, obs)
        observations.append(obs)
    return worst, observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shadow", action="store_true")
    parser.add_argument("--sources", default=str(DEPARTMENT_DIR / "state" / "sources"))
    parser.add_argument("--state-dir", default=str(DEPARTMENT_DIR / "state"))
    parser.add_argument("--manifests", default=str(DEPARTMENT_DIR / "manifests"))
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    started = time.perf_counter()
    errors: list[str] = []
    try:
        worst, observations = run(state_dir, Path(args.sources), Path(args.manifests))
    except Exception as exc:  # noqa: BLE001 — a crashed sensor must leave a record
        worst, observations = "fail", []
        errors.append(str(exc)[:300])
    try:
        runrecord.emit_record(
            state_dir,
            department="podcast",
            node="expectation_reconcile",
            # Node health, not finding health: alarms are carried by the
            # observations; only a crashed/errored run is not "ok".
            status="ok" if not errors else "error",
            release=runrecord.read_release(state_dir.parent),
            trigger={
                "kind": "time",
                "id": "podcast-daily",
                "dedupe_key": (
                    f"{datetime.now(timezone.utc).date().isoformat()}-expectation_reconcile"
                ),
            },
            duration_ms=int((time.perf_counter() - started) * 1000),
            errors=errors,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"runrecord emit failed: {exc}", file=sys.stderr)
    print(json.dumps({"status": worst,
                      "observations": [o["subject"] + ":" + o["status"] for o in observations],
                      "errors": errors}))
    return 0 if worst == "ok" and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
