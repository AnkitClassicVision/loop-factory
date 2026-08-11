# Comms reconcile sensor notes

## Read

- `departments/podcast/runtime/hopper_sensor.py`
- `departments/podcast/runtime/pipeline_sensor.py`
- `departments/podcast/runtime/README.md`
- Real tracker report, structure only; no identifying values copied.

## Tracker keys bound

- `summary.outbound_referral_touch_count`
- `summary.outbound_touch_count` (alternate spelling)
- `summary.inbound_reply_count`
- `inbound_replies_count` (defensive alternate spelling)

## Assumptions

- `--sla-hours` is reserved CLI input because the brief defines no timestamp/SLA behavior.
- An asked-to-replied gap is `warn`; a replied-to-harvested gap is `action`.
- Missing or invalid non-negative integer counts are unknown and produce `count_missing`.

## Commands run

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_comms_reconcile_sensor.py -q`
  - Result: `4 passed in 0.06s`
- `PYTHONDONTWRITEBYTECODE=1 python3 departments/podcast/runtime/comms_reconcile_sensor.py --tracker tests/fixtures/comms_reconcile/tracker_57_16_0.json --ledger tests/fixtures/comms_reconcile/referrals_empty.json`
  - Result: exit 0; one `replied->harvested` open-loop finding with asked 57, replied 16, harvested 0, severity `action`.
