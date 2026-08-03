# podcast — read-only gates against LIVE state
## validate
{
  "department": "podcast",
  "charter": {
    "ok": true,
    "autonomy_state": "shadow"
  },
  "maps": {
    "department": "podcast",
    "ok": true,
    "lint": [],
    "traceability": [],
    "drift": null
  },
  "ok": true
}
exit_code=0
## objectives
UNKNOWN publish_reliability: observed value absent
UNKNOWN hopper_interviews_ready: observed value absent
UNKNOWN state_drift: observed value absent
UNKNOWN unledgered_inbound: observed value absent
WHY publish_reliability observed: absent (honest unknown)
OBJECTIVE_BELOW_MIN hopper_depth
WHY hopper_interviews_ready observed: absent (honest unknown)
WHY state_drift observed: absent (honest unknown)
WHY unledgered_inbound observed: absent (honest unknown)
exit_code=1
## qa
{
  "department": "podcast",
  "ok": false,
  "lint": [],
  "traceability": [],
  "drift": {
    "ok": false,
    "current": "50b79dc454f5082d",
    "reason": "live tree differs from the pinned release \u2014 process changed without re-pin (run the process-change runbook)",
    "mismatches": [
      "charter.yaml",
      "procedural-graph.md",
      "runtime/comms_reconcile_sensor.py",
      "subgraphs.json"
    ]
  }
}
exit_code=1
