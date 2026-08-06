#!/usr/bin/env bash
# Estate-side entrypoint: refresh every live sales source lane, in order.
# This is the single unit a future owner-enabled estate timer would call
# BEFORE the department's daily chain. It never invokes the department
# runtime itself — triggering sales_daily.sh stays an owner decision
# (timers are owner-enabled, never factory-enabled).
#
# Lanes NOT refreshed here, deliberately:
#   podcast_handoffs.json  blocked on the podcast S3/S4 taxonomy+owner gate
#                          (packets are email-less by design until Ankit
#                          resolves it); stays an honest empty stub.
#   luma.json              no API access; honest empty stub.
#   threads.json           needs a Gmail two-way-evidence fetcher (future).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "${HERE}/fetch_hubspot_sources.py" "$@"
python3 "${HERE}/fetch_hubspot_meetings.py" "$@"
