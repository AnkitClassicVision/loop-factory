#!/usr/bin/env bash
# GATED enable; SHADOW-only; standard loop-factory trigger.
# Instantiated from templates/department_daily.sh.template for
# DEPARTMENT=podcast, REPO=/mnt/d_drive/repos/loop-factory. Review the commands,
# then explicitly enable its systemd timer (templates/systemd/). Never
# auto-enabled by the factory. Shadow-only by design: a live flag is never used.
set -euo pipefail

REPO="${PODCAST_REPO_ROOT:-/mnt/d_drive/repos/loop-factory}"
DEPARTMENT="podcast"
# Sensors import factory.* as a package; PYTHONPATH is on the kernel env
# allowlist, so the confined launcher passes it through.
export PYTHONPATH="${REPO}"
STATE_DIR="${PODCAST_STATE_DIR:-${REPO}/departments/${DEPARTMENT}/state}"
SOURCES="${STATE_DIR}/sources"
QUEUE="${STATE_DIR}/approval_queue.jsonl"
OUTBOX="${REPO}/state/decisions_outbox.jsonl"   # your human-in-the-loop consumer watches this

append_heal_failure() {
    local fingerprint="$1" stage="$2" exit_code="$3" detail="$4" playbook="${5:-}"
    python3 - "${STATE_DIR}/heal_failures.jsonl" "${fingerprint}" "${playbook}" "${stage}" "${exit_code}" "${detail}" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, fingerprint, playbook, stage, exit_code, detail = sys.argv[1:]
row = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "fingerprint": fingerprint,
    "playbook": playbook,
    "mode": "proposed",
    "commands": [],
    "result": "failed",
    "detail": f"orchestrator {stage} failure: {detail}",
    "stage": stage,
    "exit_code": int(exit_code),
}
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(row, sort_keys=True) + "\n")
PY
}

validate_json_object() {
    python3 -c 'import json, sys; assert isinstance(json.load(sys.stdin), dict)'
}

json_object_field() {
    local field="$1"
    python3 -c 'import json, sys; row=json.load(sys.stdin); assert isinstance(row, dict); value=row[sys.argv[1]]; assert isinstance(value, str) and value; print(value)' "${field}"
}

run_heal_phase() {
    local incident_list fingerprint selection playbook output rc
    rc=0
    incident_list="$(
        python3 -c 'import json, sys; incidents = json.load(open(sys.argv[1], encoding="utf-8")); assert isinstance(incidents, dict); print("\n".join(sorted(key for key, incident in incidents.items() if isinstance(incident, dict) and incident.get("state") in {"open", "department_defect"})))' "${STATE_DIR}/incidents.json"
    )" || rc=$?
    if [ "${rc}" -ne 0 ]; then
        echo "incident-list load failed with rc=${rc}" >&2
        return "${rc}"
    fi

    while IFS= read -r fingerprint; do
        [ -n "${fingerprint}" ] || continue
        rc=0
        selection="$(
            python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/heal_select.py" --state-dir "${STATE_DIR}" --fingerprint "${fingerprint}" --shadow
        )" || rc=$?
        if [ "${rc}" -ne 0 ]; then
            append_heal_failure "${fingerprint}" "heal_select" "${rc}" "process exited nonzero"
            continue
        fi
        # Empty stdout is the selector's recorded-refusal contract.
        if [ -z "${selection}" ]; then
            continue
        fi
        rc=0
        playbook="$(json_object_field id <<<"${selection}")" || rc=$?
        if [ "${rc}" -ne 0 ]; then
            append_heal_failure "${fingerprint}" "heal_select_parse" "${rc}" "stdout was not a selection object"
            continue
        fi

        rc=0
        output="$(
            python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/heal_apply.py" --state-dir "${STATE_DIR}" --fingerprint "${fingerprint}" --playbook "${playbook}" --shadow
        )" || rc=$?
        if [ "${rc}" -ne 0 ]; then
            append_heal_failure "${fingerprint}" "heal_apply" "${rc}" "process exited nonzero" "${playbook}"
            continue
        fi
        if ! validate_json_object <<<"${output}"; then
            append_heal_failure "${fingerprint}" "heal_apply_parse" 1 "stdout was not a receipt object" "${playbook}"
            continue
        fi

        rc=0
        output="$(
            python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/heal_verify.py" --state-dir "${STATE_DIR}" --fingerprint "${fingerprint}" --playbook "${playbook}" --shadow
        )" || rc=$?
        if [ "${rc}" -ne 0 ]; then
            append_heal_failure "${fingerprint}" "heal_verify" "${rc}" "process exited nonzero" "${playbook}"
            continue
        fi
        if ! validate_json_object <<<"${output}"; then
            append_heal_failure "${fingerprint}" "heal_verify_parse" 1 "stdout was not a receipt object" "${playbook}"
        fi
    done <<<"${incident_list}"
}

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

if [ "${1:-}" = "--heal-phase-only" ]; then
    run_heal_phase
    exit $?
fi

# 1) Watchdog chain (SHADOW). Each node runs through the confinement launcher
#    (factory/launch.py) so the department holds no credentials, and stays in
#    shadow. These nodes are authored by a concurrent lane; referenced by path.
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/sense_estate.py" --shadow
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/pipeline_sensor.py" --shadow --sources "${SOURCES}"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/publish_verifier.py" --shadow --sources "${SOURCES}"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/manifest_sensor.py" --shadow --sources "${SOURCES}"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/hopper_sensor.py" --shadow --sources "${SOURCES}" --pipeline-repo "/mnt/d_drive/repos/podcast"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/funnel_floor_sensor.py" --shadow --sources "${SOURCES}" --pipeline-repo "/mnt/d_drive/repos/podcast"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/floor_compiler_run.py" --shadow --state-dir "${STATE_DIR}" --dept-dir "${REPO}/departments/${DEPARTMENT}"
# Expectation reconcile is receipt-gated like the DAG supervisor: exit 2 is a
# VALID findings verdict (observations recorded; compare/dedup below must
# process them). Any other nonzero exit is a node failure and stops the chain.
exp_rc=0
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/expectation_reconcile.py" --shadow --sources "${SOURCES}" || exp_rc=$?
if [ "${exp_rc}" -ne 0 ] && [ "${exp_rc}" -ne 2 ]; then
    echo "expectation_reconcile failed with rc=${exp_rc} (not a findings verdict)" >&2
    exit "${exp_rc}"
fi
# The escalation answer-return reader is receipt-gated: nonzero, empty, or
# non-object stdout stops the chain before compare/dedup can advance.
comms_receipt="$(
    python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/comms_reconcile_sensor.py" --tracker "${SOURCES}/referral_touch_tracker.json" --ledger "${SOURCES}/referral_ledger.json" --sla-hours 48 --state-dir "${STATE_DIR}"
)"
validate_json_object <<<"${comms_receipt}"
# DAG supervisor (map node N1): validates the pipeline's hashed projection
# receipt. The PIPELINE exports the file on its own timer (podcast repo,
# podcast-dag-projection.timer); this department only reads it — supervisory
# plane, never a scheduler. Exit 2 is a VALID alarm verdict whose findings the
# dedup/escalate chain below must process, so it must not abort the run; any
# other nonzero exit is a node failure and stops the chain as usual.
DAG_PROJECTION="/mnt/d_drive/repos/podcast/state/receipts/dag-projection.json"
sup_rc=0
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/dag_supervisor.py" --projection "${DAG_PROJECTION}" --state-dir "${STATE_DIR}" || sup_rc=$?
if [ "${sup_rc}" -ne 0 ] && [ "${sup_rc}" -ne 2 ]; then
    echo "dag_supervisor failed with rc=${sup_rc} (not an alarm verdict)" >&2
    exit "${sup_rc}"
fi
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/compare_charter.py" --shadow
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/fingerprint_dedup.py" --shadow
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/escalate_outbox.py" --shadow

# The conductor is the declared action driver. It must record before semantic
# verification so green proves the scheduled driver actually executed.
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/conductor_tick.py" --shadow --state-dir "${STATE_DIR}" --dept-dir "${REPO}/departments/${DEPARTMENT}"

# 2) Manager cycle (deterministic; charter is the source of truth).
python3 "${REPO}/factory/manager.py" --department "${DEPARTMENT}" --root "${REPO}" --outbox "${OUTBOX}" --budget "${STATE_DIR}/budget_used.json"

# 3) Publish pending approvals to the human-in-the-loop outbox.
python3 "${REPO}/factory/human_in_the_loop.py" push --queue "${QUEUE}" --department "${DEPARTMENT}" --outbox "${OUTBOX}"

# 4) Propose allowlisted heals for every open incident (SHADOW). Heal failures
#    do not abort the daily chain because the manager senses their run records
#    and heal receipts and drives the heal ladder or human escalation.
run_heal_phase

# 5) Bound retained observation evidence after the daily consumers finish.
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/rotate_observations.py" --state-dir "${STATE_DIR}" --max-lines 5000

# 6) Regenerate the estate-wide and podcast-specific operator boards.
python3 -m factory.boardfeed --repo-root "${REPO}"
python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" --site "${REPO}/estate/state/boards"
# Legacy commands replaced by the site render: python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" --out "${REPO}/estate/state/board.html"
# Legacy command replaced by tabs: python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" --department "${DEPARTMENT}" --out "${REPO}/estate/state/${DEPARTMENT}-board.html"
