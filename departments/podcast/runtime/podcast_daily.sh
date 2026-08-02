#!/usr/bin/env bash
# GATED enable; SHADOW-only; standard loop-factory trigger.
# Instantiated from templates/department_daily.sh.template for
# DEPARTMENT=podcast, REPO=/mnt/d_drive/repos/loop-factory. Review the commands,
# then explicitly enable its systemd timer (templates/systemd/). Never
# auto-enabled by the factory. Shadow-only by design: a live flag is never used.
set -euo pipefail

REPO="/mnt/d_drive/repos/loop-factory"
DEPARTMENT="podcast"
# Sensors import factory.* as a package; PYTHONPATH is on the kernel env
# allowlist, so the confined launcher passes it through.
export PYTHONPATH="${REPO}"
STATE_DIR="${REPO}/departments/${DEPARTMENT}/state"
SOURCES="${STATE_DIR}/sources"
QUEUE="${STATE_DIR}/approval_queue.jsonl"
OUTBOX="${REPO}/state/decisions_outbox.jsonl"   # your human-in-the-loop consumer watches this

mkdir -p "${STATE_DIR}" "$(dirname "${OUTBOX}")"

# 1) Watchdog chain (SHADOW). Each node runs through the confinement launcher
#    (factory/launch.py) so the department holds no credentials, and stays in
#    shadow. These nodes are authored by a concurrent lane; referenced by path.
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/sense_estate.py" --shadow
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/pipeline_sensor.py" --shadow --sources "${SOURCES}"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/publish_verifier.py" --shadow --sources "${SOURCES}"
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/manifest_sensor.py" --shadow --sources "${SOURCES}"
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

# 2) Manager cycle (deterministic; charter is the source of truth).
python3 "${REPO}/factory/manager.py" --department "${DEPARTMENT}" --root "${REPO}" --outbox "${OUTBOX}"

# 3) Publish pending approvals to the human-in-the-loop outbox.
python3 "${REPO}/factory/human_in_the_loop.py" push --queue "${QUEUE}" --department "${DEPARTMENT}" --outbox "${OUTBOX}"

# 4) Propose allowlisted heals for every open incident (SHADOW). Heal failures
#    do not abort the daily chain because the manager senses their run records
#    and heal receipts and drives the heal ladder or human escalation.
while IFS= read -r fingerprint; do
    selection="$(
        python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/heal_select.py" --state-dir "${STATE_DIR}" --fingerprint "${fingerprint}" --shadow || true
    )"
    if [ -z "${selection}" ]; then
        continue
    fi
    playbook="$(python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])' <<<"${selection}" || true)"
    if [ -z "${playbook}" ]; then
        continue
    fi
    python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/heal_apply.py" --state-dir "${STATE_DIR}" --fingerprint "${fingerprint}" --playbook "${playbook}" --shadow || true
    python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/heal_verify.py" --state-dir "${STATE_DIR}" --fingerprint "${fingerprint}" --playbook "${playbook}" --shadow || true
done < <(
    python3 -c 'import json, sys; incidents = json.load(open(sys.argv[1], encoding="utf-8")); print("\n".join(sorted(key for key, incident in incidents.items() if isinstance(incident, dict) and incident.get("state") in {"open", "department_defect"})))' "${STATE_DIR}/incidents.json" || true
)

# 5) Bound retained observation evidence after the daily consumers finish.
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/rotate_observations.py" --state-dir "${STATE_DIR}" --max-lines 5000

# 6) Regenerate the estate-wide and podcast-specific operator boards.
python3 -m factory.boardfeed --repo-root "${REPO}"
python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" --site "${REPO}/estate/state/boards"
# Legacy commands replaced by the site render: python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" --out "${REPO}/estate/state/board.html"
# Legacy command replaced by tabs: python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" --department "${DEPARTMENT}" --out "${REPO}/estate/state/${DEPARTMENT}-board.html"
