# D1 — sensing authority: SPLIT (owner decision, Ankit 2026-08-03, F1-A)

The podcast department already watches four obe-* units serving guest
outreach: obe-scheduled-intent-sweeper, obe-draft-bridge,
obe-approved-send-executor, obe-context-sync. Owner chose SPLIT over
unify: podcast KEEPS exactly those four (no podcast-side change, no
coordination burden); outreach owns everything else in the outreach
machinery:

- hsd-daily-operator (12h HSD operator cycle)
- obe-fix-consumer (10min fix lane)
- the entire open-engine sales estate (sales_daily chain, speed/email/deal
  funnels, LinkedIn lanes, enrichment, approval + send-approved lane)

BOUNDARY RULE (documented, both sides): a unit is watched by exactly ONE
department; this table is the registry of record for the split. Any new
outreach-machinery unit defaults to OUTREACH unless it is created by the
podcast department for guest outreach.
