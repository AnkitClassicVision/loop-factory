"""N4: count approval drafts older than the chartered 48-hour SLA."""
from __future__ import annotations
import argparse,json,logging,sys,time
from datetime import datetime,timezone
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))
from departments.outreach.runtime.common import atomic_json,emit,read_rows,resolve,utcnow
LOGGER=logging.getLogger(__name__)
def run(root:Path,estate_path:Path,now:datetime|None=None)->dict:
    cfg=json.loads(estate_path.read_text(encoding="utf-8"));now=now or datetime.now(timezone.utc);aged=[];absent=[]
    for configured in cfg.get("approval_queue_paths",[]):
        path=resolve(root,configured)
        if not path.is_file():absent.append(configured);continue
        for index,row in enumerate(read_rows(path)):
            if row.get("status") not in {None,"pending","pending_approval","queued"}:continue
            raw=row.get("queued_at") or row.get("created_at") or row.get("ts")
            try:created=datetime.fromisoformat(str(raw).replace("Z","+00:00"));created=created if created.tzinfo else created.replace(tzinfo=timezone.utc)
            except (TypeError,ValueError):continue
            age=(now-created.astimezone(timezone.utc)).total_seconds()/3600
            if age>48:aged.append({"draft_id":str(row.get("id") or row.get("draft_id") or index),"age_hours":round(age,3)})
    output={"schema":"queue-ager/v1","ts":now.isoformat(),"approval_queue_aged":len(aged),"aged":aged,"absent":absent};atomic_json(root/"departments/outreach/state/queue_ager.json",output);return output
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--root",default=str(REPO_ROOT));p.add_argument("--estate");a=p.parse_args();root=Path(a.root);estate=Path(a.estate) if a.estate else root/"departments/outreach/runtime/estate.json";target=root/"departments/outreach/state/queue_ager.json";started=time.perf_counter()
    try:run(root,estate);emit(target.parent,"N4",started,target);return 0
    except Exception as exc:
        LOGGER.exception("queue aging failed")
        try:emit(target.parent,"N4",started,target,[type(exc).__name__])
        except Exception:LOGGER.exception("run record append failed")
        return 2
if __name__=="__main__":raise SystemExit(main())
