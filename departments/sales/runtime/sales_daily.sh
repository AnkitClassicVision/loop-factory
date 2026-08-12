#!/usr/bin/env bash
# GATED enable; SHADOW-only; standard loop-factory trigger.
# Never auto-enabled by the factory. Shadow-only by design: a live flag is
# never used.
set -euo pipefail

REPO="${SALES_REPO_ROOT:-/mnt/d_drive/repos/loop-factory}"
DEPARTMENT="sales"
# Sensors import factory.* as a package; PYTHONPATH is on the kernel env
# allowlist, so the confined launcher passes it through.
export PYTHONPATH="${REPO}"
STATE_DIR="${SALES_STATE_DIR:-${REPO}/departments/${DEPARTMENT}/state}"
SOURCES="${STATE_DIR}/sources"
QUEUE="${STATE_DIR}/approval_queue.jsonl"
OUTBOX="${REPO}/state/decisions_outbox.jsonl"   # your human-in-the-loop consumer watches this

mkdir -p "${STATE_DIR}" "$(dirname "${OUTBOX}")"

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

# Sales proving-slice chain (SHADOW). Each node runs through the confinement
# launcher so the department holds no credentials.
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/intake_sensor.py" --shadow --state-dir "${STATE_DIR}"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/qualify_scorer.py" --shadow --state-dir "${STATE_DIR}"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/booked_sensor.py" --shadow --state-dir "${STATE_DIR}"
# Held-confirm loop: applies the owner's queue answers into confirmation
# evidence, then asks (once per event) for any attended booked call the
# source cannot attest. Runs before held_sensor so a fresh answer counts
# the same day.
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/held_confirm_card.py" --shadow --state-dir "${STATE_DIR}" --outbox "${OUTBOX}"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/held_sensor.py" --shadow --state-dir "${STATE_DIR}"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/sense_gates.py" --shadow --state-dir "${STATE_DIR}" --dept-dir "${REPO}/departments/${DEPARTMENT}"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/floor_compiler_run.py" --shadow --state-dir "${STATE_DIR}" --dept-dir "${REPO}/departments/${DEPARTMENT}"

# The conductor is the declared action driver. It must record before semantic
# verification so green proves the scheduled driver actually executed.
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/conductor_tick.py" --shadow --state-dir "${STATE_DIR}" --dept-dir "${REPO}/departments/${DEPARTMENT}"

# Budget telemetry must exist before the preliminary verdict and manager. Soft
# failure is intentional: the manager converts absent telemetry into a strict,
# owner-bound escalation rather than a fabricated zero.
python3 "${REPO}/factory/budget_telemetry.py" --department "${DEPARTMENT}" --state-dir "${STATE_DIR}" --out "${STATE_DIR}/budget_used.json" || echo "budget_telemetry refused (rc=$?) — manager will breach on missing telemetry" >&2

# Manager and approval publication complete before the final claim. In shadow,
# they may only create owner-bound decision packets; they never send.
python3 "${REPO}/factory/manager.py" --department "${DEPARTMENT}" --root "${REPO}" --outbox "${OUTBOX}" --budget "${STATE_DIR}/budget_used.json"
python3 "${REPO}/factory/human_in_the_loop.py" push --queue "${QUEUE}" --department "${DEPARTMENT}" --outbox "${OUTBOX}"
