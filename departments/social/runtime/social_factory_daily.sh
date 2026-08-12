#!/usr/bin/env bash
# Release-bound Factory entrypoint for the legacy Social shadow pipeline.
set -euo pipefail

RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${RUNTIME_DIR}/../../.." && pwd)"
DEPARTMENT="social"
STATE_DIR="${SOCIAL_STATE_DIR:-${REPO}/departments/${DEPARTMENT}/state}"

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

# The legacy script preserves its own stage-level v2 receipt run ID. A nonzero
# stage exit prevents this terminal action-driver record, so the Factory verdict
# fails closed as missing rather than inferring success.
bash "${RUNTIME_DIR}/social_daily.sh"

latest_harvest="$(python3 - "${STATE_DIR}/receipts" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
paths = sorted(root.glob("*/N11-review-harvest.json")) if root.is_dir() else []
print(paths[-1] if paths else "")
PY
)"
if [ -z "${latest_harvest}" ]; then
  echo "social terminal harvest receipt is missing" >&2
  exit 2
fi
python3 "${RUNTIME_DIR}/social_daily_driver.py" \
  --state-dir "${STATE_DIR}" --dept-dir "${REPO}/departments/${DEPARTMENT}" \
  --receipt "${latest_harvest}"
