#!/usr/bin/env bash
# SHADOW-only, receipt-gated SG-GOVERN chain. Never sends or writes externally.
set -euo pipefail
REPO="${OUTREACH_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
DEPARTMENT="outreach"
STATE_DIR="${REPO}/departments/${DEPARTMENT}/state"
mkdir -p "${STATE_DIR}"

run_node() {
    local node="$1" script="$2" receipt="$3"
    python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- \
        python3 "${REPO}/departments/${DEPARTMENT}/runtime/${script}" --root "${REPO}"
    test -s "${STATE_DIR}/${receipt}"
    python3 - "${STATE_DIR}/runs-v2.jsonl" "${node}" <<'PY'
import json, sys
path, node = sys.argv[1:]
rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
assert rows and rows[-1]["department"] == "outreach" and rows[-1]["node"] == node
assert rows[-1]["status"] == "ok" and rows[-1]["external_actions_taken"] == 0
PY
}

run_node N1 lane_sense.py lane_sense.json
run_node N2 state_reconcile.py state_reconcile.json
run_node N3 gate_monitor.py gate_monitor.json
run_node N4 queue_ager.py queue_ager.json
run_node N5 escalate.py escalate.json
run_node N6 objectives_sensor.py objectives_observed.json
