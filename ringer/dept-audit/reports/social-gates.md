# social — read-only gates against LIVE state
## validate
{
  "department": "social",
  "charter": {
    "ok": true,
    "autonomy_state": "shadow"
  },
  "maps": {
    "department": "social",
    "ok": true,
    "lint": [],
    "traceability": [],
    "drift": null
  },
  "ok": true
}
exit_code=0
## objectives
WHY social setpoints.objectives: missing or not a mapping
exit_code=1
## qa
{
  "department": "social",
  "ok": false,
  "lint": [],
  "traceability": [],
  "drift": {
    "ok": false,
    "current": "6f688bb22cc37415",
    "reason": "live tree differs from the pinned release \u2014 process changed without re-pin (run the process-change runbook)",
    "mismatches": [
      "runtime/draft_post.py",
      "runtime/social_daily.sh"
    ]
  }
}
exit_code=1
