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
SURFACE_COUNTS="${SOCIAL_SURFACE_COUNTS:-${STATE_DIR}/surface_counts.json}"
BRAND="${SOCIAL_BRAND:-${STATE_DIR}/brand.json}"
OFFER="${SOCIAL_OFFER:-${STATE_DIR}/offer.json}"
DRAFT_ENGINES="${SOCIAL_DRAFT_ENGINES:-claude_subscription codex_oauth glm_oauth}"
QA_ENGINE="${SOCIAL_QA_ENGINE:-codex_oauth}"
ENGINES_FILE="${SOCIAL_ENGINES_FILE:-${STATE_DIR}/engines.yaml}"
ENGINE_TIMEOUT="${SOCIAL_ENGINE_TIMEOUT:-600}"
QA_RETRY_BACKOFF_SECONDS="${SOCIAL_QA_RETRY_BACKOFF_SECONDS:-1}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
RUN_DIR="${STATE_DIR}/receipts/${RUN_ID}"
INCIDENTS="${STATE_DIR}/incident_candidates.json"

mkdir -p "${STATE_DIR}" "${RUN_DIR}"

incident_receipt_failure() {
  local node="$1"
  local expected="$2"
  local failure_class="$3"
  python3 - "${INCIDENTS}" "${node}" "${expected}" "${failure_class}" <<'PY'
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
    "failure_class": sys.argv[4],
    "severity": "high",
    "setpoint": "receipt exists and is valid JSON before next step",
    "observed": f"{sys.argv[4]}:{sys.argv[3]}",
    "evidence": [f"local://{Path(sys.argv[3]).name}"],
    "one_question": "Fix the failed receipt before this process advances?",
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

receipt_is_valid_json() {
  local receipt="$1"
  python3 - "${receipt}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    text = path.read_text(encoding="utf-8")
    try:
        values = [json.loads(text)]
    except json.JSONDecodeError:
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    invalid_statuses = {"blocked", "missing", "failed", "error"}
    valid = bool(values) and all(
        isinstance(value, dict)
        and bool(value)
        and value.get("status") not in invalid_statuses
        for value in values
    )
except (OSError, UnicodeError, json.JSONDecodeError):
    valid = False
raise SystemExit(0 if valid else 1)
PY
}

qa_has_only_engine_unavailable() {
  local receipt="$1"
  python3 - "${receipt}" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
defects = report.get("defects") if isinstance(report, dict) else None
only_unavailable = (
    isinstance(defects, list)
    and bool(defects)
    and all(
        isinstance(defect, dict)
        and defect.get("code") == "qa_engine_unavailable"
        for defect in defects
    )
)
raise SystemExit(0 if only_unavailable else 1)
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
    incident_receipt_failure "${node}" "${receipt}" "missing_receipt"
    echo "missing receipt: ${node}: ${receipt}" >&2
    exit 2
  fi
  if ! receipt_is_valid_json "${receipt}"; then
    incident_receipt_failure "${node}" "${receipt}" "invalid_receipt"
    echo "invalid receipt: ${node}: ${receipt}" >&2
    exit 2
  fi
  if test "${rc}" -ne 0; then
    echo "step blocked: ${node}: exit ${rc}" >&2
    exit "${rc}"
  fi
}

run_qa_round() {
  local round_number="$1"
  local draft="$2"
  local attempt=1
  local node

  QA_ENGINE_UNAVAILABLE_EXHAUSTED="no"
  while test "${attempt}" -le 3; do
    node="N5-qa-r${round_number}"
    if test "${attempt}" -gt 1; then
      node="${node}-try${attempt}"
    fi
    QA_OUT="${RUN_DIR}/${node}.json"
    run_step "${node}" "${QA_OUT}" \
      python3 "${RUNTIME_DIR}/qa_post.py" \
      --state-dir "${STATE_DIR}" --out "${QA_OUT}" \
      --draft "${draft}" --bundle "${SANITIZED_OUT}" \
      --engine "${QA_ENGINE}" --engines-file "${ENGINES_FILE}" \
      --engine-timeout "${ENGINE_TIMEOUT}"

    if ! qa_has_only_engine_unavailable "${QA_OUT}"; then
      return
    fi
    if test "${attempt}" -eq 3; then
      QA_ENGINE_UNAVAILABLE_EXHAUSTED="yes"
      return
    fi
    if test "${QA_RETRY_BACKOFF_SECONDS}" -gt 0; then
      sleep "$((attempt * QA_RETRY_BACKOFF_SECONDS))"
    fi
    attempt=$((attempt + 1))
  done
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

run_draft_with_fallback() {
  local receipt="$1"
  local node="$2"
  shift 2
  local skipped_qa=""
  for engine in ${DRAFT_ENGINES}; do
    if test "${engine}" = "${QA_ENGINE}"; then
      skipped_qa="${engine}"
      echo "deferring draft engine ${engine}: same as QA engine" >&2
      continue
    fi
    DRAFT_ENGINE="${engine}"
    if "$@" --engine "${engine}"; then
      if test -s "${receipt}" && receipt_is_valid_json "${receipt}"; then
        return 0
      fi
    fi
    echo "draft engine ${engine} failed, trying next" >&2
    rm -f "${receipt}"
  done
  if test -n "${skipped_qa}"; then
    echo "last resort: using ${skipped_qa} for both draft and QA" >&2
    DRAFT_ENGINE="${skipped_qa}"
    if "$@" --engine "${skipped_qa}"; then
      if test -s "${receipt}" && receipt_is_valid_json "${receipt}"; then
        return 0
      fi
    fi
    rm -f "${receipt}"
  fi
  incident_receipt_failure "${node}" "${receipt}" "all_draft_engines_failed"
  echo "all draft engines exhausted" >&2
  exit 2
}

KILL_OUT="${RUN_DIR}/S6-kill.json"
BREAKER_OUT="${RUN_DIR}/S7-breaker.json"
run_step "S6-kill" "${KILL_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" kill \
  --state-dir "${STATE_DIR}" --observations "${OBSERVATIONS}" --out "${KILL_OUT}"
run_step "S7-breaker" "${BREAKER_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" breaker \
  --state-dir "${STATE_DIR}" --observations "${OBSERVATIONS}" \
  --surface "${SURFACE}" --out "${BREAKER_OUT}"

INVENTORY_SOURCE="${RUN_DIR}/N1-inventory-source.json"
run_step "N1-inventory-source" "${INVENTORY_SOURCE}" \
  python3 - "${INDEX}" "${INVENTORY_SOURCE}" <<'PY'
import json
import sys
from pathlib import Path

index = Path(sys.argv[1])
out = Path(sys.argv[2])
rows = [
    json.loads(line)
    for line in index.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
if not rows or any(not isinstance(row, dict) for row in rows):
    raise SystemExit("canonical inventory index must contain JSON object rows")
out.write_text(
    json.dumps({"status": "prepared", "items": rows}, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

INVENTORY_OUT="${RUN_DIR}/N1-inventory.json"
run_step "N1-inventory" "${INVENTORY_OUT}" \
  python3 "${RUNTIME_DIR}/inventory_backcatalog.py" \
  --state-dir "${STATE_DIR}" --items "${INVENTORY_SOURCE}" \
  --index "${INDEX}" --out "${INVENTORY_OUT}"

INDEX_INSTALLED="${RUN_DIR}/N1-index-installed.json"
run_step "N1-index-install" "${INDEX_INSTALLED}" \
  python3 - "${INVENTORY_OUT}" "${INDEX}" "${INDEX_INSTALLED}" <<'PY'
import json
import os
import sys
from pathlib import Path

source = Path(sys.argv[1])
index = Path(sys.argv[2])
receipt = Path(sys.argv[3])
rows = [
    json.loads(line)
    for line in source.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
if not rows or any(not isinstance(row, dict) for row in rows):
    raise SystemExit("refreshed inventory must contain JSON object rows")
index.parent.mkdir(parents=True, exist_ok=True)
temporary = index.with_name(f".{index.name}.{os.getpid()}.tmp")
temporary.write_text(
    "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
    encoding="utf-8",
)
os.replace(temporary, index)
receipt.write_text(
    json.dumps(
        {"status": "installed", "index": index.name, "row_count": len(rows)},
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY

CANDIDATE_OUT="${RUN_DIR}/N2-candidate.json"
run_step "N2-select" "${CANDIDATE_OUT}" \
  python3 "${RUNTIME_DIR}/select_candidate.py" \
  --state-dir "${STATE_DIR}" --index "${INDEX}" \
  --suppression "${SUPPRESSION}" --out "${CANDIDATE_OUT}"

RESOLVE_INDEX="${RUN_DIR}/S1-index.json"
run_step "S1-index-view" "${RESOLVE_INDEX}" \
  python3 - "${INDEX}" "${RESOLVE_INDEX}" <<'PY'
import json
import sys
from pathlib import Path

index = Path(sys.argv[1])
out = Path(sys.argv[2])
rows = [
    json.loads(line)
    for line in index.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
if not rows or any(not isinstance(row, dict) for row in rows):
    raise SystemExit("canonical identity index must contain JSON object rows")
out.write_text(
    json.dumps({"status": "prepared", "items": rows}, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

# guards.py resolve validates one selected candidate's item_id/source_type/url
# against exactly one row in a single JSON index document. It cannot consume
# N1's JSONL refresh receipt, so N2 selects from the canonical JSONL index first
# and S1 then resolves that candidate through a JSON view of the same rows.
RESOLVED_OUT="${RUN_DIR}/S1-resolved.json"
run_step "S1-resolve" "${RESOLVED_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" resolve \
  --state-dir "${STATE_DIR}" --item "${CANDIDATE_OUT}" \
  --index "${RESOLVE_INDEX}" --surface "${SURFACE}" --out "${RESOLVED_OUT}"

ELIGIBLE_OUT="${RUN_DIR}/S2-eligible.json"
run_step "S2-eligibility" "${ELIGIBLE_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" eligibility \
  --state-dir "${STATE_DIR}" --item "${RESOLVED_OUT}" \
  --suppression "${SUPPRESSION}" --approvals "${APPROVALS}" \
  --out "${ELIGIBLE_OUT}"

CONTEXT_PACKET="${RUN_DIR}/N3-brand-offer.json"
run_step "N3-brand-offer" "${CONTEXT_PACKET}" \
  python3 - "${BRAND}" "${OFFER}" "${CONTEXT_PACKET}" <<'PY'
import json
import sys
from pathlib import Path

brand_source = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
offer_source = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if not isinstance(brand_source, dict) or not isinstance(offer_source, dict):
    raise SystemExit("brand and offer sources must be JSON objects")
brand = brand_source.get("brand", brand_source)
offer = offer_source.get("offer", offer_source)
out = Path(sys.argv[3])
out.write_text(
    json.dumps(
        {
            "status": "prepared",
            "brand": brand,
            "offer": offer,
        },
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY

CONTEXT_OUT="${RUN_DIR}/N3-context.json"
run_step "N3-assemble-context" "${CONTEXT_OUT}" \
  python3 "${RUNTIME_DIR}/assemble_context.py" \
  --state-dir "${STATE_DIR}" --candidate "${ELIGIBLE_OUT}" \
  --brand "${CONTEXT_PACKET}" --out "${CONTEXT_OUT}"

SANITIZED_OUT="${RUN_DIR}/S3-sanitized.json"
run_step "S3-privacy" "${SANITIZED_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" privacy \
  --state-dir "${STATE_DIR}" --manifest "${CONTEXT_OUT}" \
  --blocklist "${BLOCKLIST}" --out "${SANITIZED_OUT}"

MODEL_TOKEN="${RUN_DIR}/S8-model-token.json"
run_step "S8-budget" "${MODEL_TOKEN}" \
  python3 "${RUNTIME_DIR}/kernel_bridge.py" authorize-model \
  --state-dir "${STATE_DIR}" --bundle "${SANITIZED_OUT}" --out "${MODEL_TOKEN}"

DRAFT_RAW="${RUN_DIR}/N4-draft-r1-raw.json"
run_draft_with_fallback "${DRAFT_RAW}" "N4-draft-r1-raw" \
  python3 "${RUNTIME_DIR}/draft_post.py" \
  --state-dir "${STATE_DIR}" --out "${DRAFT_RAW}" \
  --bundle "${SANITIZED_OUT}" --surface "${SURFACE}" \
  --engines-file "${ENGINES_FILE}" \
  --engine-timeout "${ENGINE_TIMEOUT}"

# draft_post numbers an initial draft as round 0, while dispatch.py requires a
# positive round. Normalize the first completed draft to publishing round 1.
DRAFT_OUT="${RUN_DIR}/N4-draft-r1.json"
run_step "N4-draft-r1" "${DRAFT_OUT}" \
  python3 - "${DRAFT_RAW}" "${DRAFT_OUT}" <<'PY'
import json
import sys
from pathlib import Path

source = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(source, dict) or source.get("round") != 0:
    raise SystemExit("initial draft must be a JSON object with round 0")
source["round"] = 1
Path(sys.argv[2]).write_text(
    json.dumps(source, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

run_qa_round 1 "${DRAFT_OUT}"
if test "${QA_ENGINE_UNAVAILABLE_EXHAUSTED}" = "yes"; then
  incident_receipt_failure \
    "N5-qa-engine-unavailable" "${QA_OUT}" "engine_unavailable"
  echo "qa engine unavailable after three attempts" >&2
  exit 2
fi

QA_PASS="$(python3 - "${QA_OUT}" <<'PY'
import json
import sys
print("yes" if json.loads(open(sys.argv[1], encoding="utf-8").read()).get("pass") is True else "no")
PY
)"
if test "${QA_PASS}" != "yes"; then
  EDITED_DRAFT="${RUN_DIR}/N4-draft-r2.json"
  run_draft_with_fallback "${EDITED_DRAFT}" "N4-draft-r2" \
    python3 "${RUNTIME_DIR}/draft_post.py" \
    --state-dir "${STATE_DIR}" --out "${EDITED_DRAFT}" \
    --bundle "${SANITIZED_OUT}" --surface "${SURFACE}" \
    --engines-file "${ENGINES_FILE}" \
    --revise --prior-draft "${DRAFT_OUT}" --qa-report "${QA_OUT}" \
    --engine-timeout "${ENGINE_TIMEOUT}"
  DRAFT_OUT="${EDITED_DRAFT}"
  run_qa_round 2 "${DRAFT_OUT}"
  if test "${QA_ENGINE_UNAVAILABLE_EXHAUSTED}" = "yes"; then
    incident_receipt_failure \
      "N5-qa-engine-unavailable" "${QA_OUT}" "engine_unavailable"
    echo "qa engine unavailable after three attempts" >&2
    exit 2
  fi
  QA_PASS="$(python3 - "${QA_OUT}" <<'PY'
import json
import sys
print("yes" if json.loads(open(sys.argv[1], encoding="utf-8").read()).get("pass") is True else "no")
PY
)"
fi
if test "${QA_PASS}" != "yes"; then
  incident_receipt_failure "N5-qa-non-convergence" "${QA_OUT}" "qa_non_convergence"
  echo "qa did not converge within two rounds" >&2
  exit 2
fi

DISPATCH_TOKEN="${RUN_DIR}/S4-S5-dispatch-token.json"
run_step "S4-S5-authorize" "${DISPATCH_TOKEN}" \
  python3 "${RUNTIME_DIR}/kernel_bridge.py" authorize-dispatch \
  --state-dir "${STATE_DIR}" --draft "${DRAFT_OUT}" --out "${DISPATCH_TOKEN}"

KILL_PRE_DISPATCH_OUT="${RUN_DIR}/S6-kill-pre-dispatch.json"
BREAKER_PRE_DISPATCH_OUT="${RUN_DIR}/S7-breaker-pre-dispatch.json"
run_step "S6-kill-pre-dispatch" "${KILL_PRE_DISPATCH_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" kill \
  --state-dir "${STATE_DIR}" --observations "${OBSERVATIONS}" \
  --out "${KILL_PRE_DISPATCH_OUT}"
run_step "S7-breaker-pre-dispatch" "${BREAKER_PRE_DISPATCH_OUT}" \
  python3 "${RUNTIME_DIR}/guards.py" breaker \
  --state-dir "${STATE_DIR}" --observations "${OBSERVATIONS}" \
  --surface "${SURFACE}" --out "${BREAKER_PRE_DISPATCH_OUT}"

DISPATCH_OUT="${RUN_DIR}/N6-dispatch.json"
SIMULATE_SINK="${RUN_DIR}/simulate-delivery.jsonl"
run_step "N6-dispatch" "${DISPATCH_OUT}" \
  python3 "${RUNTIME_DIR}/dispatch.py" \
  --state-dir "${STATE_DIR}" --draft "${DRAFT_OUT}" \
  --qa-report "${QA_OUT}" --token "${DISPATCH_TOKEN}" \
  --surface-counts "${SURFACE_COUNTS}" --simulate-sink "${SIMULATE_SINK}" \
  --out "${DISPATCH_OUT}"

DISPATCH_STATUS="$(python3 - "${DISPATCH_OUT}" <<'PY'
import json
import sys
sys.stdout.write(
    json.loads(open(sys.argv[1], encoding="utf-8").read()).get(
        "status", "dispatched"
    )
)
PY
)"
if test "${DISPATCH_STATUS}" = "yielded"; then
  exit 0
fi

VERIFY_OUT="${RUN_DIR}/N7-delivery-verification.json"
run_step "N7-delivery-verify" "${VERIFY_OUT}" \
  python3 "${RUNTIME_DIR}/delivery_verify.py" \
  --state-dir "${STATE_DIR}" --receipt "${DISPATCH_OUT}" \
  --simulate-sink "${SIMULATE_SINK}" --out "${VERIFY_OUT}"

RECORD_OUT="${RUN_DIR}/N9-record.json"
run_step "N9-record" "${RECORD_OUT}" \
  python3 "${RUNTIME_DIR}/record.py" --node "SG-REPUBLISH" \
  --state-dir "${STATE_DIR}" --payload "$(cat "${VERIFY_OUT}")" \
  --shadow --out "${RECORD_OUT}"
