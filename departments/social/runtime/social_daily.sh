#!/usr/bin/env bash
# GATED enable; SHADOW-only SG-REPUBLISH daily driver.
set -euo pipefail

RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${RUNTIME_DIR}/../../.." && pwd)"
DEPARTMENT="social"
STATE_DIR="${SOCIAL_STATE_DIR:-${REPO}/departments/${DEPARTMENT}/state}"
SURFACE="${SOCIAL_SURFACE:-linkedin_mybcat}"
INDEX="${SOCIAL_INDEX:-${STATE_DIR}/backcatalog_index.json}"
SUPPRESSION="${SOCIAL_SUPPRESSION:-${STATE_DIR}/suppression.jsonl}"
APPROVALS="${SOCIAL_APPROVALS:-${STATE_DIR}/approvals.yaml}"
BLOCKLIST="${SOCIAL_BLOCKLIST:-${STATE_DIR}/privacy_blocklist.yaml}"
OBSERVATIONS="${SOCIAL_OBSERVATIONS:-${STATE_DIR}/observations.jsonl}"
BRAND="${SOCIAL_BRAND:-${STATE_DIR}/brand.json}"
OFFER="${SOCIAL_OFFER:-${STATE_DIR}/offer.json}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
RUN_DIR="${STATE_DIR}/receipts/${RUN_ID}"
INCIDENTS="${STATE_DIR}/incident_candidates.json"

mkdir -p "${STATE_DIR}" "${RUN_DIR}"

incident_missing_receipt() {
  local node="$1"
  local expected="$2"
  python3 - "${INCIDENTS}" "${node}" "${expected}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
row = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "sensor": "receipt_gate",
    "subject": sys.argv[2],
    "failure_class": "missing_receipt",
    "severity": "high",
    "setpoint": "receipt exists before next step",
    "observed": f"missing:{sys.argv[3]}",
    "evidence": [f"local://{Path(sys.argv[3]).name}"],
    "one_question": "Fix the missing receipt before this process advances?",
}
try:
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
except (OSError, json.JSONDecodeError):
    existing = []
if not isinstance(existing, list):
    existing = []
existing.append(row)
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
temporary.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
}

require_node() {
  local name="$1"
  local path="${RUNTIME_DIR}/${name}.py"
  if ! test -f "${path}"; then
    echo "node missing: ${path}" >&2
    exit 4
  fi
}

run_step() {
  local node="$1"
  local receipt="$2"
  shift 2
  local rc
  if "$@"; then
    rc=0
  else
    rc=$?
  fi
  if ! test -s "${receipt}"; then
    incident_missing_receipt "${node}" "${receipt}"
    echo "missing receipt: ${node}: ${receipt}" >&2
    exit 2
  fi
  if test "${rc}" -ne 0; then
    echo "step blocked: ${node}: exit ${rc}" >&2
    exit "${rc}"
  fi
}

if test -f "${STATE_DIR}/KILLED"; then
  echo "department killed: ${STATE_DIR}/KILLED" >&2
  exit 2
fi
if test -f "${STATE_DIR}/BREAKER_${SURFACE}"; then
  echo "surface breaker open: ${SURFACE}" >&2
  exit 2
fi

for node in inventory_backcatalog select_candidate assemble_context draft_post qa_post guards kernel_bridge dispatch delivery_verify record; do
  require_node "${node}"
done

KILL_OUT="${RUN_DIR}/S6-kill.json"
BREAKER_OUT="${RUN_DIR}/S7-breaker.json"
run_step "S6-kill" "${KILL_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" kill \
  --state-dir "${STATE_DIR}" --observations "${OBSERVATIONS}" --out "${KILL_OUT}"
run_step "S7-breaker" "${BREAKER_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" breaker \
  --state-dir "${STATE_DIR}" --observations "${OBSERVATIONS}" \
  --surface "${SURFACE}" --out "${BREAKER_OUT}"

INVENTORY_OUT="${RUN_DIR}/N1-inventory.json"
run_step "N1-inventory" "${INVENTORY_OUT}" \
  python3 "${RUNTIME_DIR}/inventory_backcatalog.py" \
  --state-dir "${STATE_DIR}" --index "${INDEX}" --out "${INVENTORY_OUT}"

RESOLVED_OUT="${RUN_DIR}/S1-resolved.json"
run_step "S1-resolve" "${RESOLVED_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" resolve \
  --state-dir "${STATE_DIR}" --item "${INVENTORY_OUT}" --index "${INDEX}" \
  --surface "${SURFACE}" --out "${RESOLVED_OUT}"

CANDIDATE_OUT="${RUN_DIR}/N2-candidate.json"
run_step "N2-select" "${CANDIDATE_OUT}" \
  python3 "${RUNTIME_DIR}/select_candidate.py" \
  --state-dir "${STATE_DIR}" --inventory "${RESOLVED_OUT}" --out "${CANDIDATE_OUT}"

ELIGIBLE_OUT="${RUN_DIR}/S2-eligible.json"
run_step "S2-eligibility" "${ELIGIBLE_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" eligibility \
  --state-dir "${STATE_DIR}" --item "${CANDIDATE_OUT}" \
  --suppression "${SUPPRESSION}" --approvals "${APPROVALS}" \
  --out "${ELIGIBLE_OUT}"

CONTEXT_OUT="${RUN_DIR}/N3-context.json"
run_step "N3-assemble-context" "${CONTEXT_OUT}" \
  python3 "${RUNTIME_DIR}/assemble_context.py" \
  --state-dir "${STATE_DIR}" --candidate "${ELIGIBLE_OUT}" \
  --brand "${BRAND}" --offer "${OFFER}" --out "${CONTEXT_OUT}"

SANITIZED_OUT="${RUN_DIR}/S3-sanitized.json"
run_step "S3-privacy" "${SANITIZED_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" privacy \
  --state-dir "${STATE_DIR}" --manifest "${CONTEXT_OUT}" \
  --blocklist "${BLOCKLIST}" --out "${SANITIZED_OUT}"

MODEL_TOKEN="${RUN_DIR}/S8-model-token.json"
run_step "S8-budget" "${MODEL_TOKEN}" \
  python3 "${RUNTIME_DIR}/kernel_bridge.py" authorize-model \
  --state-dir "${STATE_DIR}" --bundle "${SANITIZED_OUT}" --out "${MODEL_TOKEN}"

DRAFT_OUT="${RUN_DIR}/N4-draft-r1.json"
run_step "N4-draft-r1" "${DRAFT_OUT}" \
  python3 "${RUNTIME_DIR}/draft_post.py" \
  --state-dir "${STATE_DIR}" --bundle "${SANITIZED_OUT}" \
  --model-token "${MODEL_TOKEN}" --round 1 --out "${DRAFT_OUT}"

QA_OUT="${RUN_DIR}/N5-qa-r1.json"
run_step "N5-qa-r1" "${QA_OUT}" \
  python3 "${RUNTIME_DIR}/qa_post.py" \
  --state-dir "${STATE_DIR}" --draft "${DRAFT_OUT}" --round 1 --out "${QA_OUT}"

QA_PASS="$(python3 - "${QA_OUT}" <<'PY'
import json
import sys
print("yes" if json.loads(open(sys.argv[1], encoding="utf-8").read()).get("pass") is True else "no")
PY
)"
if test "${QA_PASS}" != "yes"; then
  EDITED_DRAFT="${RUN_DIR}/N4-draft-r2.json"
  run_step "N4-draft-r2" "${EDITED_DRAFT}" \
    python3 "${RUNTIME_DIR}/draft_post.py" \
    --state-dir "${STATE_DIR}" --bundle "${SANITIZED_OUT}" \
    --model-token "${MODEL_TOKEN}" --prior-draft "${DRAFT_OUT}" \
    --defects "${QA_OUT}" --round 2 --out "${EDITED_DRAFT}"
  DRAFT_OUT="${EDITED_DRAFT}"
  QA_OUT="${RUN_DIR}/N5-qa-r2.json"
  run_step "N5-qa-r2" "${QA_OUT}" \
    python3 "${RUNTIME_DIR}/qa_post.py" \
    --state-dir "${STATE_DIR}" --draft "${DRAFT_OUT}" --round 2 --out "${QA_OUT}"
  QA_PASS="$(python3 - "${QA_OUT}" <<'PY'
import json
import sys
print("yes" if json.loads(open(sys.argv[1], encoding="utf-8").read()).get("pass") is True else "no")
PY
)"
fi
if test "${QA_PASS}" != "yes"; then
  incident_missing_receipt "N5-qa-non-convergence" "${QA_OUT}"
  echo "qa did not converge within two rounds" >&2
  exit 2
fi

DISPATCH_TOKEN="${RUN_DIR}/S4-S5-dispatch-token.json"
run_step "S4-S5-authorize" "${DISPATCH_TOKEN}" \
  python3 "${RUNTIME_DIR}/kernel_bridge.py" authorize-dispatch \
  --state-dir "${STATE_DIR}" --draft "${DRAFT_OUT}" --out "${DISPATCH_TOKEN}"

DISPATCH_OUT="${RUN_DIR}/N6-dispatch.json"
run_step "N6-dispatch" "${DISPATCH_OUT}" \
  python3 "${RUNTIME_DIR}/dispatch.py" \
  --state-dir "${STATE_DIR}" --draft "${DRAFT_OUT}" \
  --qa-report "${QA_OUT}" --token "${DISPATCH_TOKEN}" --out "${DISPATCH_OUT}"

VERIFY_OUT="${RUN_DIR}/N7-delivery-verification.json"
run_step "N7-delivery-verify" "${VERIFY_OUT}" \
  python3 "${RUNTIME_DIR}/delivery_verify.py" \
  --state-dir "${STATE_DIR}" --receipt "${DISPATCH_OUT}" --out "${VERIFY_OUT}"

RECORD_OUT="${RUN_DIR}/N9-record.json"
run_step "N9-record" "${RECORD_OUT}" \
  python3 "${RUNTIME_DIR}/record.py" --node "SG-REPUBLISH" \
  --state-dir "${STATE_DIR}" --payload "$(cat "${VERIFY_OUT}")" \
  --shadow --out "${RECORD_OUT}"
