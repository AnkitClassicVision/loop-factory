"""N2: reconcile configured funnel and HubSpot snapshots and harvest ask replies."""
from __future__ import annotations
import argparse, glob, json, logging, sys, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path: sys.path.insert(0, str(REPO_ROOT))
from departments.outreach.runtime.common import atomic_json, emit, read_rows, resolve, utcnow

LOGGER = logging.getLogger(__name__)

def _records(paths: list[str], root: Path) -> tuple[dict[str, object], list[str]]:
    merged, absent = {}, []
    for configured in paths:
        pattern = str(resolve(root, configured))
        matches = [Path(p) for p in glob.glob(pattern)]
        if not matches: absent.append(configured); continue
        for path in matches:
            try:
                values = read_rows(path) if path.suffix == ".jsonl" else json.loads(path.read_text(encoding="utf-8"))
                if isinstance(values, dict): values = values.get("records", values.get("items", [values]))
                if not isinstance(values, list): values = [values]
                for index, row in enumerate(values):
                    if isinstance(row, dict):
                        key = str(row.get("id") or row.get("contact_id") or row.get("opportunity_id") or index)
                        merged[key] = row.get("state", row.get("status", row.get("stage", row)))
            except (OSError, ValueError, TypeError): absent.append(str(path))
    return merged, absent

def _harvest(state_dir: Path, reply_paths: list[str], root: Path) -> int:
    asks_path = state_dir / "asks.jsonl"
    asks = read_rows(asks_path)
    replies = []
    for configured in reply_paths:
        replies.extend(read_rows(resolve(root, configured)))
    by_id = {str(r.get("ask_id") or r.get("decision_id")): r for r in replies}
    harvested = 0
    for ask in asks:
        match = by_id.get(str(ask.get("ask_id")))
        if ask.get("status") == "open" and match and match.get("verdict") is not None:
            ask.update(status="harvested", harvested_at=utcnow().isoformat(), decision=match["verdict"])
            harvested += 1
    if asks: atomic_json(asks_path.with_suffix(".json"), asks); asks_path.write_text("".join(json.dumps(r, sort_keys=True)+"\n" for r in asks), encoding="utf-8")
    return harvested

def run(root: Path, estate_path: Path) -> dict:
    config = json.loads(estate_path.read_text(encoding="utf-8"))
    funnel, absent_funnel = _records(config.get("funnel_ledger_paths", []), root)
    hubspot, absent_hubspot = _records(config.get("hubspot_evidence_paths", []), root)
    reasons = []
    for key in sorted(set(funnel) | set(hubspot)):
        if key not in funnel: reasons.append({"id": key, "reason": "missing_from_funnel"})
        elif key not in hubspot: reasons.append({"id": key, "reason": "missing_from_hubspot"})
        elif funnel[key] != hubspot[key]: reasons.append({"id": key, "reason": "state_mismatch", "funnel": funnel[key], "hubspot": hubspot[key]})
    state_dir = root / "departments/outreach/state"
    harvested = _harvest(state_dir, config.get("decision_reply_paths", ["departments/outreach/state/decision_replies.jsonl"]), root)
    output = {"schema":"state-reconcile/v1", "ts":utcnow().isoformat(),
              "outreach_state_drift": None if absent_funnel or absent_hubspot else len(reasons),
              "reasons": reasons, "harvested_replies": harvested,
              "absent":{"funnel":absent_funnel,"hubspot":absent_hubspot}}
    atomic_json(state_dir / "state_reconcile.json", output); return output

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--root",default=str(REPO_ROOT)); p.add_argument("--estate"); a=p.parse_args()
    root=Path(a.root); estate=Path(a.estate) if a.estate else root/"departments/outreach/runtime/estate.json"; target=root/"departments/outreach/state/state_reconcile.json"; started=time.perf_counter()
    try: run(root,estate); emit(target.parent,"N2",started,target); return 0
    except Exception as exc:
        LOGGER.exception("state reconcile failed")
        try: emit(target.parent,"N2",started,target,[type(exc).__name__])
        except Exception: LOGGER.exception("run record append failed")
        return 2
if __name__ == "__main__": raise SystemExit(main())
