#!/usr/bin/env bash
# Hand-invoked, shadow-only SG-DIGEST cycle. No dispatch or network effects.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
DEPT_ROOT="departments/pulse"
if [[ "${1:-}" == "--root" ]]; then
  [[ -n "${2:-}" ]] || { echo "--root requires a path" >&2; exit 2; }
  DEPT_ROOT="$2"
  shift 2
fi
[[ $# -eq 0 ]] || { echo "unexpected arguments" >&2; exit 2; }

STATE_DIR="${DEPT_ROOT}/state"
mkdir -p "${STATE_DIR}"
CYCLE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
EPOCH="$(date -u +%s)"

append_run_record() {
  local node="$1" status="$2" duration_ms="$3" artifact="$4"
  PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" python3 -c '
import sys
from datetime import datetime, timezone
from pathlib import Path
from factory.runrecord import append_record, build_record, new_run_id
state_dir, node, status, duration_ms, artifact, cycle_id, epoch = sys.argv[1:]
record = build_record(
    schema="run-record/v2", rev=1, run_id=new_run_id(), department="pulse",
    node=node, epoch=int(epoch), ts=datetime.now(timezone.utc).isoformat(),
    attempt=1, round=None, release=None,
    trigger={"kind": "manual", "id": cycle_id, "dedupe_key": cycle_id + node},
    engine=None, model=None, auth_class=None, usage=None,
    cost={"lane": "flat_subscription", "model_calls": 0},
    duration_ms=int(duration_ms), status=status, errors=[], artifacts=[artifact],
    receipts=[], evaluator=None, approval=None, external_actions_taken=0,
)
append_record(Path(state_dir), record)
' "${STATE_DIR}" "$node" "$status" "$duration_ms" "$artifact" "$CYCLE_ID" "$EPOCH"
}

run_node() {
  local node="$1" script="$2" receipt="$3"
  local start end duration status
  start="$(date +%s%N)"
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 "${SCRIPT_DIR}/${script}" --root "${DEPT_ROOT}"
  local node_rc=$?
  end="$(date +%s%N)"
  duration="$(( (end - start) / 1000000 ))"
  status="ok"
  if [[ $node_rc -ne 0 || ! -f "$receipt" ]]; then
    status="error"
  fi
  append_run_record "$node" "$status" "$duration" "$receipt" || exit $?
  if [[ "$status" != "ok" ]]; then
    [[ $node_rc -ne 0 ]] && exit "$node_rc"
    exit 1
  fi
}

run_node N1 intake_scan.py "${STATE_DIR}/intake.json"
run_node N3 clarify_ask.py "${STATE_DIR}/asks.jsonl"
run_node N2 digest_build.py "${STATE_DIR}/digest-$(date -u +%F).md"
run_node N4 objectives_sensor.py "${STATE_DIR}/objectives_observed.json"
