"""N5: baseline findings, then escalate unseen transitions with a bounded digest."""
from __future__ import annotations
import argparse,hashlib,json,logging,sys,time
from datetime import timedelta
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from factory import human_in_the_loop
from departments.outreach.runtime.common import append_row,atomic_json,emit,read_json,read_rows,utcnow
LOGGER=logging.getLogger(__name__)
DIRECT_ASK_CEILING=5

def _finding(node:str,lane:object,failure_class:object,path:object,condition:object)->dict:
    return {"node":node,"lane":str(lane or "unknown"),"failure_class":str(failure_class or "unknown"),
            "evidence_path":str(path or "unknown"),"observed_condition":str(condition or "unknown")}

def _failures(state:Path)->list[dict]:
    found=[]
    lane=read_json(state/"lane_sense.json",{}) or {}
    for row in lane.get("observations",[]):
        if row.get("status") not in {"fail","unknown"}:continue
        reasons=row.get("reasons") or ["unknown"]
        path=(row.get("metrics") or {}).get("evidence_path") or row.get("evidence_path")
        for reason in reasons:found.append(_finding("N1",row.get("subject"),str(reason).split(":",1)[0],path,reason))
    reconcile=read_json(state/"state_reconcile.json",{}) or {}
    for side,paths in reconcile.get("absent",{}).items():
        for path in paths:found.append(_finding("N2",side,"evidence_absent",path,"configured evidence path has no readable records"))
    for row in reconcile.get("reasons",[]):
        found.append(_finding("N2",row.get("id"),row.get("reason"),"state_reconcile.json",row.get("reason")))
    gate=read_json(state/"gate_monitor.json",{}) or {}
    for side,paths in gate.get("absent",{}).items():
        for path in paths:found.append(_finding("N3",side,"evidence_absent",path,"configured evidence path has no readable records"))
    for row in gate.get("reasons",[]):
        found.append(_finding("N3",row.get("send_class") or row.get("send_id"),row.get("reason"),"gate_monitor.json",row.get("reason")))
    queue=read_json(state/"queue_ager.json",{}) or {}
    for path in queue.get("absent",[]):found.append(_finding("N4","approval_queue","evidence_absent",path,"configured approval queue path is absent"))
    for row in queue.get("aged",[]):found.append(_finding("N4","approval_queue","approval_queue_aged","queue_ager.json",f"pending age exceeds 48 hours; count item {row.get('draft_id')}"))
    return found

def _identity(failure:dict)->str:
    material={key:failure[key] for key in ("node","lane","failure_class","evidence_path")}
    return hashlib.sha256(json.dumps(material,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def _fingerprint(failure:dict)->str:
    return hashlib.sha256(json.dumps(failure,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def _lane_identity(node:object,lane:object)->str:
    return f"lane:{node}:{lane}"

def _write_ask(state:Path,ask:dict,failure:dict,current)->None:
    append_row(state/"asks.jsonl",ask)
    atomic_json(state/"outbox"/f"{ask['ask_id']}.json",{"schema":"outreach-escalation-draft/v1","ask_id":ask["ask_id"],"summary":ask["summary"],**{k:ask[k] for k in ("lane","failure_class","evidence_path","observed_condition")},"failure":failure,"pii_free":True})
    result=human_in_the_loop.escalate(
        "outreach",ask["summary"],state/"decisions_outbox.jsonl",
        {"ask_id":ask["ask_id"],"fingerprint":ask["fingerprint"]},
        owner="ankit",deadline=(current+timedelta(hours=48)).isoformat(),
        next_action="Review the outreach transition and choose the documented repair or hold path",
    )
    if result.get("escalated") is not True:
        raise ValueError(f"outreach escalation blocked: {result.get('reason')}")

def run(root:Path)->dict:
    state=root/"departments/outreach/state";dedupe_path=state/"escalation_fingerprints.jsonl";baseline_path=state/"first_run_baseline.jsonl"
    seen={r.get("fingerprint") for r in read_rows(dedupe_path)};prior={r.get("identity"):r for r in read_rows(baseline_path)};failures=_failures(state);eligible=[];current=utcnow();ts=current.isoformat()
    current_ids=set()
    for failure in failures:
        identity=_identity(failure);current_ids.add(identity);fingerprint=_fingerprint(failure);previous=prior.get(identity)
        lane_previous=prior.get(_lane_identity(failure["node"],failure["lane"]))
        if previous is None and not (lane_previous and lane_previous.get("status")=="healthy"):
            append_row(baseline_path,{"schema":"first-run-baseline/v1","ts":ts,"identity":identity,"lane":failure["lane"],"failure_class":failure["failure_class"],"evidence_path":failure["evidence_path"],"status":"failing"})
            continue
        if fingerprint not in seen:eligible.append((failure,fingerprint))
    # Record healthy transitions for identities that disappeared, enabling a later healthy->failed ask.
    for identity,row in prior.items():
        if identity not in current_ids and row.get("status")!="healthy":append_row(baseline_path,{**row,"ts":ts,"status":"healthy"})
    lane_doc=read_json(state/"lane_sense.json",{}) or {}
    for row in lane_doc.get("observations",[]):
        append_row(baseline_path,{"schema":"first-run-baseline/v1","ts":ts,"identity":_lane_identity("N1",row.get("subject")),"lane":str(row.get("subject") or "unknown"),"status":"healthy" if row.get("status")=="ok" else "failing"})
    created=[]
    direct=eligible[:DIRECT_ASK_CEILING]
    for failure,fingerprint in direct:
        ask_id=f"outreach-{fingerprint[:16]}";summary=f"{failure['lane']}: {failure['failure_class']} at {failure['evidence_path']} ({failure['observed_condition']})"
        ask={"ask_id":ask_id,"fingerprint":fingerprint,"status":"open","created_at":ts,"return_path":"state_reconcile","return_sla_hours":48,"summary":summary,**{k:failure[k] for k in ("lane","failure_class","evidence_path","observed_condition")}}
        _write_ask(state,ask,failure,current);append_row(dedupe_path,{"fingerprint":fingerprint,"ask_id":ask_id,"ts":ts});seen.add(fingerprint);created.append(ask_id)
    overflow=eligible[DIRECT_ASK_CEILING:]
    if overflow:
        members=[{"lane":f["lane"],"failure_class":f["failure_class"],"evidence_path":f["evidence_path"],"observed_condition":f["observed_condition"],"fingerprint":fp} for f,fp in overflow]
        digest_fp=hashlib.sha256(json.dumps([m["fingerprint"] for m in members],sort_keys=True).encode()).hexdigest();ask_id=f"outreach-digest-{digest_fp[:16]}"
        counts={}
        for member in members:counts[(member["lane"],member["failure_class"])]=counts.get((member["lane"],member["failure_class"]),0)+1
        condition="; ".join(f"{lane}/{cls}: {count}" for (lane,cls),count in sorted(counts.items()))
        failure={"node":"N5","lane":"multiple","failure_class":"transition_digest","evidence_path":"multiple paths","observed_condition":condition,"findings":members}
        ask={"ask_id":ask_id,"fingerprint":digest_fp,"status":"open","created_at":ts,"return_path":"state_reconcile","return_sla_hours":48,"summary":f"{len(members)} additional outreach transitions: {condition}",**{k:failure[k] for k in ("lane","failure_class","evidence_path","observed_condition")}}
        _write_ask(state,ask,failure,current);created.append(ask_id)
        for member in members:append_row(dedupe_path,{"fingerprint":member["fingerprint"],"ask_id":ask_id,"ts":ts,"digest":True})
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
