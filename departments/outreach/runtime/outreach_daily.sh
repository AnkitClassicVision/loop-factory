#!/usr/bin/env bash
# SHADOW-only, receipt-gated SG-GOVERN chain. Never sends or writes externally.
set -euo pipefail
REPO="${OUTREACH_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
DEPARTMENT="outreach"
STATE_DIR="${REPO}/departments/${DEPARTMENT}/state"
export PYTHONPATH="${REPO}"
mkdir -p "${STATE_DIR}"

if [ -z "${LOOP_FACTORY_RUN_ID:-}" ] || [ -z "${OE_RECORD_SPOOL:-}" ] \
    || [ ! -d "${OE_RECORD_SPOOL:-}" ] \
    || [ ! -f "${OE_RECORD_SPOOL:-}/factory-spool.json" ]; then
  echo "Factory run identity and record spool are required" >&2
  exit 2
fi
if ! python3 - "${OE_RECORD_SPOOL}" "${LOOP_FACTORY_RUN_ID}" "${DEPARTMENT}" "${STATE_DIR}" <<'PY'
import json
import sys
from pathlib import Path

spool, run_id, department, state_dir = sys.argv[1:]
try:
    marker = json.loads((Path(spool) / "factory-spool.json").read_text(encoding="utf-8"))
    expected = {
        "schema": "factory-record-spool/v1",
        "run_id": run_id,
        "department": department,
        "trigger": "daily",
        "state_dir": str(Path(state_dir).resolve()),
    }
    if any(marker.get(key) != value for key, value in expected.items()):
        raise ValueError
    if not isinstance(marker.get("signature"), str) or not marker["signature"].strip():
        raise ValueError
except (OSError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit(1)
PY
then
  echo "Factory run identity and record spool are required" >&2
  exit 2
fi

run_node() {
    local node="$1" script="$2" receipt="$3"
    python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- \
        python3 "${REPO}/departments/${DEPARTMENT}/runtime/${script}" --root "${REPO}"
    test -s "${STATE_DIR}/${receipt}"
python3 - "${STATE_DIR}/runs-v2.jsonl" "${node}" <<'PY'
import json, sys
path, node = sys.argv[1:]
expected = {
    "N1": "lane_sense", "N2": "state_reconcile", "N3": "gate_monitor",
    "N4": "queue_ager", "N5": "escalate", "N6": "objectives_sensor",
}[node]
rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
assert rows and rows[-1]["department"] == "outreach" and rows[-1]["node"] == expected
assert rows[-1]["status"] == "ok" and rows[-1]["external_actions_taken"] == 0
PY
}

run_node N1 lane_sense.py lane_sense.json
run_node N2 state_reconcile.py state_reconcile.json
run_node N3 gate_monitor.py gate_monitor.json
run_node N4 queue_ager.py queue_ager.json
run_node N5 escalate.py escalate.json
run_node N6 objectives_sensor.py objectives_observed.json

# The shell orchestrator is itself a mapped implementation occurrence.  Its
# receipt is local and shadow-only; it does not grant any external authority.
python3 - "${REPO}" <<'PY'
import os
import sys
from pathlib import Path

from factory import runrecord

repo = Path(sys.argv[1])
state = repo / "departments/outreach/state"
artifact = state / "objectives_observed.json"
runrecord.emit_record(
    state,
    department="outreach",
    node="outreach_daily",
    contract_subgraph="SG-GOVERN",
    contract_node_id="ORCH",
    contract_impl="runtime/outreach_daily.sh",
    status="ok",
    release=runrecord.read_release(repo / "departments/outreach"),
    trigger={"kind": "time", "id": "outreach-loop.timer", "dedupe_key": os.environ["LOOP_FACTORY_RUN_ID"]},
    cost={"lane": "flat_subscription", "model_calls": 0},
    artifacts=[str(artifact)],
    receipts=[str(artifact)],
    external_actions_taken=0,
)
PY
