# C03 — Lock the Unit Contract v1 schema and lint rules

Status: open · Type: grilling · Claimed: — · Blocked by: —

## Question

The exact field list a unit must declare (goal ref, state-read/state-advanced,
gate class, catch point, eval criteria ref, result-audit spec, heal path,
untrusted-input flags) and the lint rules that refuse a unit missing any.
How do the inherited v2 contracts (07 receipt fields, 09 eval tiers, 13 heal
classes, 14 auth) map onto contract fields rather than being re-decided?
What is the migration posture for existing units that predate the contract
(grandfather with a deadline vs hard refuse)? Deliverable: a schema file +
lint spec precise enough for an implement ticket, with the watched-to-fail
RED case defined (proof condition 1).
