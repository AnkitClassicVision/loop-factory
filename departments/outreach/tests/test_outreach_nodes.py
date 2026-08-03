from __future__ import annotations
import json, os, shutil, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory import runrecord
from departments.outreach.runtime import gate_monitor, lane_sense, objectives_sensor, queue_ager, state_reconcile
from departments.outreach.runtime import escalate as escalate_node

REPO=Path(__file__).resolve().parents[3]

def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding="utf-8")
def write_rows(path, rows):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text("".join(json.dumps(r)+"\n" for r in rows),encoding="utf-8")
def estate(root, **values):
    path=root/"estate.json";write_json(path,values);return path
def state(root): return root/"departments/outreach/state"

def test_lane_sense_liveness(tmp_path):
    proof=tmp_path/"proof.log";proof.write_text("ok")
    config=estate(tmp_path,systemd_user_timers=[{"name":"lane","log_glob":str(proof),"stale_after_minutes":60}],sales_lanes=[])
    result=lane_sense.run(tmp_path,config,probe=lambda unit:{"ActiveState":"active","Result":"success","ExecMainStatus":"0"})
    assert result["observations"][0]["status"]=="ok"

def test_lane_sense_stale_detection(tmp_path):
    proof=tmp_path/"proof.log";proof.write_text("old");old=datetime.now(timezone.utc)-timedelta(hours=2);os.utime(proof,(old.timestamp(),old.timestamp()))
    config=estate(tmp_path,systemd_user_timers=[{"name":"lane","log_glob":str(proof),"stale_after_minutes":30}],sales_lanes=[])
    result=lane_sense.run(tmp_path,config,now=datetime.now(timezone.utc),probe=lambda unit:{"ActiveState":"active","Result":"success","ExecMainStatus":"0"})
    assert "evidence_stale" in result["observations"][0]["reasons"]

def test_podcast_owned_units_absent_from_real_registry():
    data=json.loads((REPO/"departments/outreach/runtime/estate.json").read_text());names={r["name"] for r in data["systemd_user_timers"]}
    assert names.isdisjoint({"obe-scheduled-intent-sweeper","obe-draft-bridge","obe-approved-send-executor","obe-context-sync"})

def test_reconcile_drift_count_and_reasons(tmp_path):
    write_rows(tmp_path/"f.jsonl",[{"id":"a","state":"open"},{"id":"b","state":"won"}]);write_rows(tmp_path/"h.jsonl",[{"id":"a","state":"closed"},{"id":"c","state":"new"}])
    result=state_reconcile.run(tmp_path,estate(tmp_path,funnel_ledger_paths=["f.jsonl"],hubspot_evidence_paths=["h.jsonl"],decision_reply_paths=[]))
    assert result["outreach_state_drift"]==3 and {r["reason"] for r in result["reasons"]}=={"state_mismatch","missing_from_hubspot","missing_from_funnel"}

def test_reconcile_harvests_reply(tmp_path):
    write_rows(tmp_path/"f.jsonl",[]);write_rows(tmp_path/"h.jsonl",[]);write_rows(state(tmp_path)/"asks.jsonl",[{"ask_id":"ask-1","status":"open"}]);write_rows(tmp_path/"replies.jsonl",[{"ask_id":"ask-1","verdict":"ACK"}])
    result=state_reconcile.run(tmp_path,estate(tmp_path,funnel_ledger_paths=["f.jsonl"],hubspot_evidence_paths=["h.jsonl"],decision_reply_paths=["replies.jsonl"]))
    assert result["harvested_replies"]==1 and json.loads((state(tmp_path)/"asks.jsonl").read_text().splitlines()[0])["status"]=="harvested"

def gate_fixture(tmp_path, send, receipts):
    write_rows(tmp_path/"sends.jsonl",send);write_rows(tmp_path/"receipts.jsonl",receipts)
    return estate(tmp_path,declared_send_classes=["approved_draft_execution","warm_reply_followup"],observed_send_ledgers=["sends.jsonl"],voice_gate_receipt_globs=["receipts.jsonl"])

def test_gate_in_class_approved_send_is_clean(tmp_path):
    result=gate_monitor.run(tmp_path,gate_fixture(tmp_path,[{"send_id":"s1","human_approved":True}],[{"send_id":"s1","status":"pass"}]))
    assert result["send_class_integrity"]==0
def test_gate_unmatched_class_is_violation(tmp_path):
    result=gate_monitor.run(tmp_path,gate_fixture(tmp_path,[{"send_id":"s1","send_class":"cold"}],[{"send_id":"s1","passed":True}]))
    assert result["send_class_integrity"]==1
def test_gate_missing_receipt_is_violation(tmp_path):
    result=gate_monitor.run(tmp_path,gate_fixture(tmp_path,[{"send_id":"s1","send_class":"warm_reply_followup"}],[]))
    assert result["send_class_integrity"]==1

def test_queue_ager_counts_old_not_fresh(tmp_path):
    now=datetime.now(timezone.utc);write_rows(tmp_path/"queue.jsonl",[{"id":"old","queued_at":(now-timedelta(hours=49)).isoformat()},{"id":"fresh","queued_at":(now-timedelta(hours=2)).isoformat()}])
    result=queue_ager.run(tmp_path,estate(tmp_path,approval_queue_paths=["queue.jsonl"]),now=now)
    assert result["approval_queue_aged"]==1 and result["aged"][0]["draft_id"]=="old"

def test_escalate_once_per_fingerprint(tmp_path):
    write_json(state(tmp_path)/"lane_sense.json",{"observations":[{"subject":"x","status":"unknown","reasons":["evidence_absent"]}]})
    first=escalate_node.run(tmp_path);second=escalate_node.run(tmp_path)
    assert first["new_escalations"]==1 and second["new_escalations"]==0
    assert len((state(tmp_path)/"asks.jsonl").read_text().splitlines())==1

def test_objectives_absent_when_upstream_missing(tmp_path):
    result=objectives_sensor.run(tmp_path)
    assert all(row["status"]=="absent" for row in result["objectives"].values())

def test_all_emitted_v2_records_validate(tmp_path):
    target=state(tmp_path);artifact=target/"proof.json";write_json(artifact,{"ok":True})
    from departments.outreach.runtime.common import emit
    import time
    for node in ("N1","N2","N3","N4","N5","N6"):emit(target,node,time.perf_counter(),artifact)
    for line in (target/"runs-v2.jsonl").read_text().splitlines():runrecord.validate_record(json.loads(line))

def test_orchestrator_halts_at_failing_node(tmp_path):
    runtime=tmp_path/"departments/outreach/runtime";runtime.mkdir(parents=True);(tmp_path/"departments/outreach/state").mkdir(parents=True)
    shutil.copy(REPO/"departments/outreach/runtime/outreach_daily.sh",runtime/"outreach_daily.sh")
    factory=tmp_path/"factory";factory.mkdir();(factory/"launch.py").write_text("import subprocess,sys\na=sys.argv; i=a.index('--'); raise SystemExit(subprocess.run(a[i+1:]).returncode)\n")
    stub='''import json,sys\nfrom pathlib import Path\nroot=Path(sys.argv[sys.argv.index("--root")+1]); state=root/"departments/outreach/state"; state.mkdir(parents=True,exist_ok=True)\nnode=Path(__file__).stem\nif node=="state_reconcile": raise SystemExit(7)\nname={"lane_sense":"lane_sense.json"}[node]; (state/name).write_text("{}")\nrecord={"schema":"run-record/v2","rev":2,"run_id":"x","department":"outreach","node":"N1","epoch":0,"ts":"x","attempt":1,"round":None,"release":None,"trigger":None,"engine":None,"model":None,"auth_class":None,"usage":None,"cost":{"lane":"flat_subscription","model_calls":0},"duration_ms":0,"status":"ok","errors":[],"artifacts":[],"receipts":[],"evaluator":None,"approval":None,"external_actions_taken":0}\nwith (state/"runs-v2.jsonl").open("a") as h:h.write(json.dumps(record)+"\\n")\n'''
    (runtime/"lane_sense.py").write_text(stub);(runtime/"state_reconcile.py").write_text(stub)
    result=subprocess.run(["bash",str(runtime/"outreach_daily.sh")],env={**os.environ,"OUTREACH_REPO_ROOT":str(tmp_path)},capture_output=True,text=True)
    assert result.returncode!=0 and not (state(tmp_path)/"gate_monitor.json").exists()
