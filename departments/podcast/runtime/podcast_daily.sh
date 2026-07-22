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
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/compare_charter.py" --shadow
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/fingerprint_dedup.py" --shadow
python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/escalate_outbox.py" --shadow

# 2) Manager cycle (deterministic; charter is the source of truth).
python3 "${REPO}/factory/manager.py" --department "${DEPARTMENT}" --root "${REPO}" --outbox "${OUTBOX}"

# 3) Publish pending approvals to the human-in-the-loop outbox.
python3 "${REPO}/factory/human_in_the_loop.py" push --queue "${QUEUE}" --department "${DEPARTMENT}" --outbox "${OUTBOX}"
