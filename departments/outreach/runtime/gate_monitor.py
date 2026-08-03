"""N3: verify observed sends belong to a class and carry a passing gate receipt."""
from __future__ import annotations
import argparse, glob, json, logging, sys, time
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path: sys.path.insert(0,str(REPO_ROOT))
from departments.outreach.runtime.common import atomic_json, emit, read_rows, resolve, utcnow
LOGGER=logging.getLogger(__name__)

def _glob_rows(patterns:list[str], root:Path)->tuple[list[dict],list[str]]:
    rows=[]; absent=[]
    for configured in patterns:
        matches=[Path(p) for p in glob.glob(str(resolve(root,configured)))]
        if not matches: absent.append(configured)
        for path in matches:
            try:
                if path.suffix==".jsonl": rows.extend(read_rows(path))
                else:
                    value=json.loads(path.read_text(encoding="utf-8")); rows.extend(value if isinstance(value,list) else [value])
            except (OSError,ValueError): absent.append(str(path))
    return rows,absent

def run(root:Path, estate_path:Path)->dict:
    cfg=json.loads(estate_path.read_text(encoding="utf-8")); declared=set(cfg.get("declared_send_classes",[]))
    receipts,absent_receipts=_glob_rows(cfg.get("voice_gate_receipt_globs",[]),root)
    sends,absent_sends=_glob_rows(cfg.get("observed_send_ledgers",[]),root)
    passing={str(r.get("send_id") or r.get("id") or r.get("action_id")) for r in receipts if r.get("passed") is True or r.get("status") in {"pass","passed","approved","ok"}}
    reasons=[]
    for index,row in enumerate(sends):
        if row.get("status") in {"rejected","blocked","skipped","pending"}: continue
        sid=str(row.get("send_id") or row.get("id") or row.get("action_id") or index); cls=row.get("send_class") or ("approved_draft_execution" if row.get("human_approved") is True else None)
        if cls not in declared: reasons.append({"send_id":sid,"reason":"undeclared_send_class","send_class":cls})
        elif sid not in passing: reasons.append({"send_id":sid,"reason":"missing_passing_gate_receipt","send_class":cls})
    evaluated=len(receipts); passed=len(passing)
    output={"schema":"gate-monitor/v1","ts":utcnow().isoformat(),"send_class_integrity":len(reasons),"reasons":reasons,
            "voice_gate_pass_rate":(passed/evaluated if evaluated else None),"receipt_count":evaluated,"observed_send_count":len(sends),
            "absent":{"receipts":absent_receipts,"observed_sends":absent_sends}}
    target=root/"departments/outreach/state/gate_monitor.json"; atomic_json(target,output); return output
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--root",default=str(REPO_ROOT));p.add_argument("--estate");a=p.parse_args();root=Path(a.root);estate=Path(a.estate) if a.estate else root/"departments/outreach/runtime/estate.json";target=root/"departments/outreach/state/gate_monitor.json";started=time.perf_counter()
    try:run(root,estate);emit(target.parent,"N3",started,target);return 0
    except Exception as exc:
        LOGGER.exception("gate monitor failed")
        try:emit(target.parent,"N3",started,target,[type(exc).__name__])
        except Exception:LOGGER.exception("run record append failed")
        return 2
if __name__=="__main__":raise SystemExit(main())
