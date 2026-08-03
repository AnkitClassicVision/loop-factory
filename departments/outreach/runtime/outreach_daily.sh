#!/usr/bin/env bash
# GATED enable; SHADOW-only; standard loop-factory trigger.
# Copy to departments/outreach/runtime/outreach_daily.sh, replace
# outreach and /mnt/d_drive/repos/loop-factory, review the commands, then explicitly enable its
# systemd timer (templates/systemd/). Never auto-enabled by the factory.
set -euo pipefail

REPO="/mnt/d_drive/repos/loop-factory"
DEPARTMENT="outreach"
STATE_DIR="${REPO}/departments/${DEPARTMENT}/state"
QUEUE="${STATE_DIR}/approval_queue.jsonl"
OUTBOX="${REPO}/state/decisions_outbox.jsonl"   # your human-in-the-loop consumer watches this

mkdir -p "${STATE_DIR}" "$(dirname "${OUTBOX}")"

# 1) Pull adapter (department-specific, read-only): write the normalized batch
#    the worker loop expects. Replace with your department's pull node.
# python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/pull_adapter.py" --output "${STATE_DIR}/batch.jsonl"

# 2) Worker loop (SHADOW: drafts and queues, never sends). Launched through the
#    confinement launcher so the department holds no credentials.
# python3 "${REPO}/factory/launch.py" --department "${DEPARTMENT}" -- python3 "${REPO}/departments/${DEPARTMENT}/runtime/run_loop.py" --state-dir "${STATE_DIR}" --shadow

# 3) Manager cycle (deterministic; charter is the source of truth).
python3 "${REPO}/factory/manager.py" --department "${DEPARTMENT}" --root "${REPO}" --outbox "${OUTBOX}"

# 4) Publish pending approvals to the human-in-the-loop outbox.
python3 "${REPO}/factory/human_in_the_loop.py" push --queue "${QUEUE}" --department "${DEPARTMENT}" --outbox "${OUTBOX}"

# 5) Regenerate the estate feed, estate board, and this department's board.
python3 -m factory.timersense --out "${REPO}/estate/state/timers.json" --tolerate-missing
python3 -m factory.boardfeed --repo-root "${REPO}"
python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" --site "${REPO}/estate/state/boards"
# Legacy commands replaced by the site render: python3 -m factory.board --feed "${REPO}/estate/state/board-feed.ndjson" --out "${REPO}/estate/state/board.html"
# Legacy department flag replaced by tabs: --department "${DEPARTMENT}"
