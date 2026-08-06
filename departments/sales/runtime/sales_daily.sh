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

json_object_field() {
    local field="$1"
    python3 -c 'import json, sys; row=json.load(sys.stdin); assert isinstance(row, dict); value=row[sys.argv[1]]; assert isinstance(value, str) and value; print(value)' "${field}"
}

mkdir -p "${STATE_DIR}" "$(dirname "${OUTBOX}")"

# P1: mint the run manifest BEFORE the first node. Mint refusal is a hard
# stop: a run that cannot declare its plan does not run (deny-by-default).
mint_out="$(PYTHONPATH="${REPO}" python3 -m kernel.run_manifest mint --department "${DEPARTMENT}" --dept-dir "${REPO}/departments/${DEPARTMENT}" --state-dir "${STATE_DIR}" --trigger daily)"
LOOP_FACTORY_RUN_ID="$(json_object_field run_id <<<"${mint_out}")"
export LOOP_FACTORY_RUN_ID

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


# The verifier's observation reaches the manager during this run. Exit 2 is
# an advisory verdict; every other nonzero result blocks the chain.
ver_rc=0
PYTHONPATH="${REPO}" python3 -m kernel.run_manifest verify --dept-dir "${REPO}/departments/${DEPARTMENT}" --state-dir "${STATE_DIR}" --run-id "${LOOP_FACTORY_RUN_ID}" || ver_rc=$?
if [ "${ver_rc}" -ne 0 ] && [ "${ver_rc}" -ne 2 ]; then
    echo "run_manifest verify failed with rc=${ver_rc} (not a verdict)" >&2
    exit "${ver_rc}"
fi

# Manager cycle (deterministic; charter is the source of truth).
python3 "${REPO}/factory/manager.py" --department "${DEPARTMENT}" --root "${REPO}" --outbox "${OUTBOX}" --budget "${STATE_DIR}/budget_used.json"

# Publish pending approvals to the human-in-the-loop outbox.
python3 "${REPO}/factory/human_in_the_loop.py" push --queue "${QUEUE}" --department "${DEPARTMENT}" --outbox "${OUTBOX}"

# Shadow conductor observes last (post-verify: required:false in the roster).
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/conductor_tick.py" --shadow --state-dir "${STATE_DIR}" --dept-dir "${REPO}/departments/${DEPARTMENT}"
