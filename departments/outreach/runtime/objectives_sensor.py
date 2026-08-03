"""N6: emit objectives-observed/v1 without converting absent evidence to zero."""
from __future__ import annotations
import argparse,logging,sys,time
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from departments.outreach.runtime.common import append_row,atomic_json,emit,read_json,utcnow
LOGGER=logging.getLogger(__name__)
def run(root:Path)->dict:
    state=root/"departments/outreach/state";specs={"outreach_state_drift":("state_reconcile.json","outreach_state_drift"),"send_class_integrity":("gate_monitor.json","send_class_integrity"),"approval_queue_aged":("queue_ager.json","approval_queue_aged")};objectives={}
    for name,(filename,key) in specs.items():
        upstream=read_json(state/filename,None);value=upstream.get(key) if isinstance(upstream,dict) else None
        objectives[name]={"status":"absent"} if value is None else {"status":"observed","value":value,"setpoint":0}
    gate=read_json(state/"gate_monitor.json",{}) or {};baseline={"ts":utcnow().isoformat(),"voice_gate_pass_rate":gate.get("voice_gate_pass_rate"),"receipt_count":gate.get("receipt_count",0)};append_row(state/"objective_baselines.jsonl",baseline)
    output={"schema":"objectives-observed/v1","ts":utcnow().isoformat(),"objectives":objectives,"baselines":baseline};atomic_json(state/"objectives_observed.json",output);return output
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--root",default=str(REPO_ROOT));a=p.parse_args();root=Path(a.root);target=root/"departments/outreach/state/objectives_observed.json";started=time.perf_counter()
    try:run(root);emit(target.parent,"N6",started,target);return 0
    except Exception as exc:
        LOGGER.exception("objective sensing failed")
        try:emit(target.parent,"N6",started,target,[type(exc).__name__])
        except Exception:LOGGER.exception("run record append failed")
        return 2
if __name__=="__main__":raise SystemExit(main())
