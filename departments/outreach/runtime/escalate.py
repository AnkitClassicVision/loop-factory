"""N5: escalate each previously unseen unknown failure fingerprint once."""
from __future__ import annotations
import argparse,hashlib,json,logging,sys,time
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from factory import human_in_the_loop
from departments.outreach.runtime.common import append_row,atomic_json,emit,read_json,read_rows,utcnow
LOGGER=logging.getLogger(__name__)

def _failures(state:Path)->list[dict]:
    found=[]
    lane=read_json(state/"lane_sense.json",{}) or {}
    for row in lane.get("observations",[]):
        if row.get("status") in {"fail","unknown"}:found.append({"node":"N1","subject":row.get("subject"),"reasons":row.get("reasons",[])})
    reconcile=read_json(state/"state_reconcile.json",{}) or {}
    for side,paths in reconcile.get("absent",{}).items():
        for path in paths:found.append({"node":"N2","subject":side,"reasons":["evidence_absent",path]})
    gate=read_json(state/"gate_monitor.json",{}) or {}
    found.extend({"node":"N3",**row} for row in gate.get("reasons",[]))
    queue=read_json(state/"queue_ager.json",{}) or {}
    found.extend({"node":"N4","subject":row.get("draft_id"),"reasons":["approval_queue_aged"]} for row in queue.get("aged",[]))
    return found

def run(root:Path)->dict:
    state=root/"departments/outreach/state";dedupe_path=state/"escalation_fingerprints.jsonl";seen={r.get("fingerprint") for r in read_rows(dedupe_path)};created=[]
    for failure in _failures(state):
        fingerprint=hashlib.sha256(json.dumps(failure,sort_keys=True,separators=(",",":")).encode()).hexdigest()
        if fingerprint in seen:continue
        ask_id=f"outreach-{fingerprint[:16]}";ts=utcnow().isoformat();ask={"ask_id":ask_id,"fingerprint":fingerprint,"status":"open","created_at":ts,"return_path":"state_reconcile","return_sla_hours":48,"summary":f"Unknown outreach governance failure in {failure['node']}"}
        append_row(state/"asks.jsonl",ask);outbox=state/"outbox"/f"{ask_id}.json";atomic_json(outbox,{"schema":"outreach-escalation-draft/v1","ask_id":ask_id,"summary":ask["summary"],"failure":failure,"pii_free":True})
        human_in_the_loop.escalate("outreach",ask["summary"],state/"decisions_outbox.jsonl",{"ask_id":ask_id,"fingerprint":fingerprint})
        append_row(dedupe_path,{"fingerprint":fingerprint,"ask_id":ask_id,"ts":ts});seen.add(fingerprint);created.append(ask_id)
    output={"schema":"escalate/v1","ts":utcnow().isoformat(),"new_escalations":len(created),"ask_ids":created};atomic_json(state/"escalate.json",output);return output
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--root",default=str(REPO_ROOT));a=p.parse_args();root=Path(a.root);target=root/"departments/outreach/state/escalate.json";started=time.perf_counter()
    try:run(root);emit(target.parent,"N5",started,target);return 0
    except Exception as exc:
        LOGGER.exception("escalation failed")
        try:emit(target.parent,"N5",started,target,[type(exc).__name__])
        except Exception:LOGGER.exception("run record append failed")
        return 2
if __name__=="__main__":raise SystemExit(main())
