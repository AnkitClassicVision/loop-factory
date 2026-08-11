# Task notes

## Traceability choice

The new `SG-WATCHDOG` node `N6` traces to concept node `C3` and interview
question `Q3`. C3 defines the watchdog's sensor families, and Q3 selected the
watchdog as the department's V1 proving slice. The reconciliation node is a
read-only sensor in that existing lane, not a new action class.

## Validation proof

Command run from `/mnt/d_drive/repos/loop-factory`:

```text
python3 loopfactory.py validate --name podcast
```

Output tail:

```text
  "maps": {
    "department": "podcast",
    "ok": true,
    "lint": [],
    "traceability": [],
    "drift": null
  },
  "ok": true
}
```

## Assumptions

- `N6` is the next available watchdog node ID after the existing `N5`; `N9`
  remains the shared record node.
- The new interview question is inserted as Q15 beside Q14, so the former Q15
  and Q16 become Q16 and Q17.
- The existing runtime accepts `--sla-hours` for interface compatibility but
  does not yet apply elapsed-time logic. This task maps the specified invariant
  and does not modify the runtime file outside the ownership boundary.
- The charter text remains a proposal only. `departments/podcast/charter.yaml`
  was not edited.

## External-file blocker

The cumulative ownership checker reports changes in these unowned paths:

- `departments/podcast/releases/50b79dc454f5082d/manifest.json`
- `departments/podcast/releases/current`
- `departments/social/runtime/social_daily.sh`
- `runbooks/ultimate-department-creation.md`

They were not edited or removed because this worker owns exactly the four task
deliverables plus this `notes.md`. Reverting them would violate the explicit
ownership boundary and could discard other workers' changes. In particular,
the social runtime diff changes engine routing and the runbook is an untracked
artifact unrelated to this task. Cleanup requires the owning worker or the
orchestrator to restore/remove those paths.
