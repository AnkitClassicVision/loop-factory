# PROPOSAL — REQUIRES HUMAN SIGN-OFF (Ankit)

Proposed addition to `departments/podcast/charter.yaml`:

```yaml
comms_loop_invariant:
  applies_to: every outbound ask class
  requires:
    - return_path: the named component that reads the answers
    - return_sla_hours: how long a reply may sit unharvested
  escalates_when: upstream_count > 0 and downstream_count == 0 beyond SLA
  never: an ask class may not ship without a declared and executed return path
```

The incident reached 57 asked and 16 replied, but 0 harvested. Each measured
component could appear healthy while the end-to-end communication loop remained
open, leaving real replies unused.

This invariant makes the missing handoff observable for every outbound ask
class. It requires a named answer reader and a time limit, then escalates when
nonzero upstream work produces zero downstream progress beyond that limit.

Only a human may apply this proposal to `departments/podcast/charter.yaml`.
